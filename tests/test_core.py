from __future__ import annotations

import json
import io
import os
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from llm_endpoint_doctor.cli import main
from llm_endpoint_doctor.core import normalize_base_url, run_diagnostics


class _State:
    requests: list[dict[str, object]] = []
    status_code = 200
    redirect_to = ""
    redirect_status = 307
    redirected_authorization: str | None = None


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_: object) -> None:
        return

    def _send_json(self, payload: dict[str, object], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        _State.requests.append({"method": "GET", "path": self.path})
        if self.path.endswith("/models"):
            self._send_json({"data": [{"id": "fixture-model"}]})
        else:
            self._send_json({}, 404)

    def do_POST(self) -> None:
        if _State.redirect_to and not self.path.endswith("/final"):
            self.send_response(_State.redirect_status)
            self.send_header("Location", _State.redirect_to)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        _State.requests.append({
            "method": "POST",
            "path": self.path,
            "authorization": self.headers.get("Authorization"),
            "body": body,
        })
        if _State.status_code != 200:
            self._send_json({"error": {"message": "sensitive-provider-error-body"}}, _State.status_code)
            return
        if body.get("stream"):
            stream = b'data: {"choices":[{"delta":{"content":"endpoint-ok"}}]}\n\ndata: [DONE]\n\n'
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(stream)))
            self.end_headers()
            self.wfile.write(stream)
            return
        if body.get("response_format"):
            content = '{"status":"ok"}'
        else:
            content = "endpoint-ok"
        self._send_json({"choices": [{"message": {"content": content}}]})


class _CaptureRedirectHandler(BaseHTTPRequestHandler):
    def log_message(self, *_: object) -> None:
        return

    def do_POST(self) -> None:
        _State.redirected_authorization = self.headers.get("Authorization")
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}/v1"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self) -> None:
        _State.requests = []
        _State.status_code = 200
        _State.redirect_to = ""
        _State.redirect_status = 307
        _State.redirected_authorization = None

    def test_base_url_normalization_and_rejections(self) -> None:
        self.assertEqual(normalize_base_url(" https://api.example.com/v1/ "), "https://api.example.com/v1")
        with self.assertRaises(ValueError):
            normalize_base_url("http://api.example.com/v1")
        with self.assertRaises(ValueError):
            normalize_base_url("https://user:secret@api.example.com/v1")
        with self.assertRaises(ValueError):
            normalize_base_url("https://api.example.com/v1?token=secret")
        with self.assertRaises(ValueError):
            normalize_base_url("https://api.example.com/v1/chat/completions")
        with self.assertRaises(ValueError):
            normalize_base_url("https://api.example.com/\nv1")

    def test_default_check_sends_one_generation_and_sanitizes_report(self) -> None:
        report = run_diagnostics(self.base_url, " fixture-model ", "top-secret-test-key")
        self.assertTrue(report.chat_ready)
        self.assertEqual(report.model, "fixture-model")
        self.assertEqual(len(_State.requests), 1)
        request = _State.requests[0]
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["authorization"], "Bearer top-secret-test-key")
        serialized = json.dumps(report.to_dict())
        self.assertNotIn("top-secret-test-key", serialized)
        self.assertNotIn("endpoint-ok", serialized)
        self.assertNotIn("Reply with exactly", serialized)
        self.assertNotIn("/v1", serialized)

    def test_optional_checks_run_only_when_requested(self) -> None:
        report = run_diagnostics(
            self.base_url,
            "fixture-model",
            check_model_list=True,
            check_streaming=True,
            check_json_mode=True,
        )
        self.assertTrue(report.chat_ready)
        self.assertEqual(len(_State.requests), 4)
        statuses = {check.name: check.status for check in report.checks}
        self.assertEqual(statuses["model catalog"], "pass")
        self.assertEqual(statuses["streaming"], "pass")
        self.assertEqual(statuses["JSON mode"], "pass")

    def test_provider_error_body_is_not_in_report(self) -> None:
        _State.status_code = 401
        report = run_diagnostics(self.base_url, "fixture-model")
        self.assertFalse(report.chat_ready)
        serialized = json.dumps(report.to_dict())
        self.assertNotIn("sensitive-provider-error-body", serialized)
        self.assertIn("Authentication or access was rejected", serialized)

    def test_api_key_is_not_forwarded_to_a_different_origin(self) -> None:
        capture = ThreadingHTTPServer(("127.0.0.1", 0), _CaptureRedirectHandler)
        thread = threading.Thread(target=capture.serve_forever, daemon=True)
        thread.start()
        try:
            _State.redirect_to = f"http://127.0.0.1:{capture.server_port}/capture"
            report = run_diagnostics(self.base_url, "fixture-model", "top-secret-test-key")
            self.assertFalse(report.chat_ready)
            self.assertIsNone(_State.redirected_authorization)
        finally:
            capture.shutdown()
            capture.server_close()
            thread.join(timeout=2)

    def test_relative_same_origin_redirect_keeps_the_request_on_target_host(self) -> None:
        _State.redirect_to = "/v1/final"
        report = run_diagnostics(self.base_url, "fixture-model", "same-origin-test-key")
        self.assertTrue(report.chat_ready)
        self.assertEqual(len(_State.requests), 1)
        self.assertEqual(_State.requests[0]["path"], "/v1/final")
        self.assertEqual(_State.requests[0]["authorization"], "Bearer same-origin-test-key")

    def test_cli_writes_only_a_sanitized_new_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "report.json"
            output = io.StringIO()
            with patch("getpass.getpass", return_value="hidden-test-key"), redirect_stdout(output):
                exit_code = main([
                    "--base-url", self.base_url,
                    "--model", "fixture-model",
                    "--json-report", str(report_path),
                ])
            self.assertEqual(exit_code, 0)
            self.assertIn("Basic chat: READY", output.getvalue())
            self.assertNotIn("hidden-test-key", output.getvalue())
            report_text = report_path.read_text(encoding="utf-8")
            self.assertNotIn("hidden-test-key", report_text)
            self.assertNotIn("endpoint-ok", report_text)
            self.assertNotIn("/v1", report_text)

    def test_cli_reads_key_from_environment_without_printing_or_persisting_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "report.json"
            output = io.StringIO()
            with patch.dict(os.environ, {"LLM_DOCTOR_TEST_KEY": "hidden-env-key"}):
                with patch("getpass.getpass", side_effect=AssertionError("interactive prompt used")):
                    with redirect_stdout(output):
                        exit_code = main([
                            "--base-url", self.base_url,
                            "--model", "fixture-model",
                            "--api-key-env", "LLM_DOCTOR_TEST_KEY",
                            "--json-report", str(report_path),
                        ])
            self.assertEqual(exit_code, 0)
            self.assertEqual(_State.requests[0]["authorization"], "Bearer hidden-env-key")
            self.assertNotIn("hidden-env-key", output.getvalue())
            self.assertNotIn("hidden-env-key", report_path.read_text(encoding="utf-8"))

    def test_cli_no_prompt_runs_a_keyless_check_without_reading_input(self) -> None:
        output = io.StringIO()
        with patch("getpass.getpass", side_effect=AssertionError("interactive prompt used")):
            with redirect_stdout(output):
                exit_code = main([
                    "--base-url", self.base_url,
                    "--model", "fixture-model",
                    "--no-prompt",
                ])
        self.assertEqual(exit_code, 0)
        self.assertIsNone(_State.requests[0]["authorization"])

    def test_missing_key_environment_variable_fails_before_network_request(self) -> None:
        with patch.dict(os.environ, {}, clear=True), redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                main([
                    "--base-url", self.base_url,
                    "--model", "fixture-model",
                    "--api-key-env", "MISSING_LLM_DOCTOR_TEST_KEY",
                ])
        self.assertEqual(error.exception.code, 2)
        self.assertEqual(_State.requests, [])


if __name__ == "__main__":
    unittest.main()

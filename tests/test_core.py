from __future__ import annotations

import json
import io
import os
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
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
    reject_stream_options = False
    minimum_output_tokens = 0
    finish_reason = "stop"


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
        if body.get("stream_options") and _State.reject_stream_options:
            self._send_json({"error": {"message": "unsupported stream options"}}, 400)
            return
        if _State.status_code != 200:
            self._send_json({"error": {"message": "sensitive-provider-error-body"}}, _State.status_code)
            return
        cap = body.get("max_completion_tokens", body.get("max_tokens", 0))
        limited = cap < _State.minimum_output_tokens
        finish_reason = "length" if limited else _State.finish_reason
        if body.get("stream"):
            chunks = [b'data: {"choices":[{"delta":{"content":"endpoint-ok"}}]}\n\n']
            if isinstance(body.get("stream_options"), dict) and body["stream_options"].get("include_usage"):
                chunks.append(b'data: {"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}\n\n')
            chunks.append(('data: ' + json.dumps({"choices": [{"finish_reason": finish_reason, "delta": {}}]}) + '\n\n').encode())
            chunks.append(b"data: [DONE]\n\n")
            stream = b"".join(chunks)
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
        if limited:
            content = ""
        self._send_json({"choices": [{"message": {"content": content, "reasoning_content": "private-fixture-reasoning"}, "finish_reason": finish_reason}]})


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
        _State.reject_stream_options = False
        _State.minimum_output_tokens = 0
        _State.finish_reason = "stop"

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

    def test_stream_options_check_reports_usage_metadata(self) -> None:
        report = run_diagnostics(self.base_url, "fixture-model", check_stream_options=True)
        check = next(item for item in report.checks if item.name == "stream options")
        self.assertEqual(check.status, "pass")
        self.assertEqual(len(_State.requests), 2)
        self.assertEqual(_State.requests[1]["body"]["stream_options"], {"include_usage": True})

    def test_stream_options_rejection_is_reported_without_provider_body(self) -> None:
        _State.reject_stream_options = True
        report = run_diagnostics(self.base_url, "fixture-model", check_stream_options=True)
        check = next(item for item in report.checks if item.name == "stream options")
        self.assertEqual(check.status, "warn")
        self.assertIn("HTTP 400", check.detail)
        self.assertNotIn("unsupported stream options", json.dumps(report.to_dict()))

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

    def test_output_limit_is_distinct_from_a_missing_response_and_never_retries(self) -> None:
        _State.minimum_output_tokens = 64
        limited = run_diagnostics(self.base_url, "fixture-model", "fixture-secret")
        self.assertFalse(limited.chat_ready)
        self.assertEqual(len(_State.requests), 1)
        self.assertEqual(_State.requests[0]["body"]["max_tokens"], 12)
        self.assertIn("finish_reason=length", limited.checks[0].detail)
        self.assertIn("--max-output-tokens", limited.checks[0].detail)
        self.assertIn("No automatic retry", limited.checks[0].detail)
        self.assertNotIn("fixture-secret", json.dumps(limited.to_dict()))
        self.assertNotIn("private-fixture-reasoning", json.dumps(limited.to_dict()))

        explicit = run_diagnostics(self.base_url, "fixture-model", max_output_tokens=64)
        self.assertTrue(explicit.chat_ready)
        self.assertEqual(len(_State.requests), 2)
        self.assertEqual(_State.requests[1]["body"]["max_tokens"], 64)

    def test_partial_text_done_marker_usage_and_valid_json_do_not_hide_length_limit(self) -> None:
        _State.finish_reason = "length"
        report = run_diagnostics(self.base_url, "fixture-model", check_streaming=True, check_stream_options=True, check_json_mode=True)
        self.assertFalse(report.chat_ready)
        self.assertEqual(len(_State.requests), 4)
        self.assertEqual([check.status for check in report.checks], ["fail", "warn", "warn", "warn"])
        for check in report.checks:
            self.assertIn("finish_reason=length", check.detail)
        self.assertNotIn("endpoint-ok", json.dumps(report.to_dict()))
        self.assertNotIn("private-fixture-reasoning", json.dumps(report.to_dict()))

    def test_default_output_caps_are_unchanged_for_all_modes(self) -> None:
        report = run_diagnostics(self.base_url, "fixture-model", check_streaming=True, check_stream_options=True, check_json_mode=True)
        self.assertTrue(report.chat_ready)
        self.assertEqual([r["body"]["max_tokens"] for r in _State.requests], [12, 12, 12, 24])
        self.assertTrue(all("max_completion_tokens" not in r["body"] for r in _State.requests))

    def test_explicit_budget_and_field_apply_to_each_generation_not_catalog(self) -> None:
        report = run_diagnostics(self.base_url, "fixture-model", check_model_list=True, check_streaming=True, check_stream_options=True, check_json_mode=True, max_output_tokens=1024, token_field="max_completion_tokens")
        self.assertTrue(report.chat_ready)
        self.assertEqual(len(_State.requests), 5)
        self.assertEqual(_State.requests[0]["method"], "GET")
        for request in _State.requests[1:]:
            self.assertEqual(request["body"]["max_completion_tokens"], 1024)
            self.assertNotIn("max_tokens", request["body"])

    def test_bad_output_controls_stop_before_any_request(self) -> None:
        for cap in (0, -1, 8193, True, 1.5, "64"):
            with self.subTest(cap=cap), self.assertRaises(ValueError):
                run_diagnostics(self.base_url, "fixture-model", max_output_tokens=cap)
        with self.assertRaises(ValueError):
            run_diagnostics(self.base_url, "fixture-model", token_field="provider-arbitrary-field")
        self.assertEqual(_State.requests, [])

    def test_cli_output_controls_reach_the_request(self) -> None:
        _State.minimum_output_tokens = 64
        with redirect_stdout(io.StringIO()):
            code = main(["--base-url", self.base_url, "--model", "fixture-model", "--no-prompt", "--max-output-tokens", "64", "--token-field", "max_completion_tokens"])
        self.assertEqual(code, 0)
        self.assertEqual(len(_State.requests), 1)
        self.assertEqual(_State.requests[0]["body"]["max_completion_tokens"], 64)
        self.assertNotIn("max_tokens", _State.requests[0]["body"])

    def test_invalid_cli_budget_does_not_prompt_or_send(self) -> None:
        for cap in ("0", "8193", "not-a-number"):
            with self.subTest(cap=cap), patch("getpass.getpass", side_effect=AssertionError("prompt used")):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                    main(["--base-url", self.base_url, "--model", "fixture-model", "--max-output-tokens", cap])
            self.assertEqual(error.exception.code, 2)
        self.assertEqual(_State.requests, [])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import base64
import io
import json
import os
import struct
import tempfile
import threading
import unittest
import zlib
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from llm_endpoint_doctor.cli import main
from llm_endpoint_doctor.core import _request_stream, normalize_base_url, run_diagnostics


class StreamMetadataUnitTests(unittest.TestCase):
    def test_provider_metadata_shape_and_non_errors(self) -> None:
        class Response:
            status = 200

            def __init__(self, chunk: object) -> None:
                self.chunk = chunk

            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *_: object) -> None:
                pass

            def read(self, *_: object) -> bytes:
                return ("data: " + json.dumps(self.chunk) + "\n\n").encode()

        class Opener:
            def __init__(self, chunk: object) -> None:
                self.chunk = chunk

            def open(self, *_: object, **__: object) -> Response:
                return Response(self.chunk)

        cases = [
            ({"choices": None, "error_type": "BadRequestError", "error_message": "private"}, True),
            ({"choices": [], "error_type": "BadRequestError", "error_message": "private"}, True),
            ({"choices": [], "error_type": "", "error_message": "private"}, False),
            ({"choices": [], "error_type": "BadRequestError"}, False),
            ({"choices": [{"delta": {"content": "ok"}}], "error_type": "metadata", "error_message": "note"}, False),
            ({"choices": [], "usage": {"total_tokens": 2}}, False),
        ]
        for chunk, expected in cases:
            with self.subTest(chunk=chunk), patch("llm_endpoint_doctor.core.build_opener", return_value=Opener(chunk)):
                response = _request_stream("http://example.invalid/v1", "secret", {"stream": True}, 1)
                self.assertEqual(response.payload["saw_stream_error"], expected)
                self.assertFalse(response.payload["saw_done"])
                self.assertNotIn("private", json.dumps(response.payload))


class _State:
    requests: list[dict[str, object]] = []
    status_code = 200
    redirect_to = ""
    redirect_status = 307
    redirected_authorization: str | None = None
    reject_stream_options = False
    stream_error = ""
    minimum_output_tokens = 0
    finish_reason = "stop"
    image_status = 200
    image_response: dict[str, object] = {"data": [{"b64_json": "synthetic-image-payload"}]}
    vision_status = 200
    vision_response: dict[str, object] = {
        "choices": [{"message": {"content": "red, green, blue, yellow"}, "finish_reason": "stop"}]
    }
    tool_call_status = 200
    tool_call_response: dict[str, object] = {
        "choices": [{
            "finish_reason": "tool_calls",
            "message": {
                "tool_calls": [{
                    "type": "function",
                    "function": {"name": "endpoint_doctor_probe", "arguments": '{"status":"ok"}'},
                }]
            },
        }]
    }


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
        if self.path.endswith("/images/generations"):
            self._send_json(_State.image_response, _State.image_status)
            return
        messages = body.get("messages")
        content_parts = messages[0].get("content") if isinstance(messages, list) and messages and isinstance(messages[0], dict) else None
        if isinstance(content_parts, list) and any(
            isinstance(part, dict) and part.get("type") == "image_url"
            for part in content_parts
        ):
            self._send_json(_State.vision_response, _State.vision_status)
            return
        if body.get("tools"):
            self._send_json(_State.tool_call_response, _State.tool_call_status)
            return
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
            if _State.stream_error == "object":
                chunks.append(b'data: {"error":{"code":429,"message":"private-provider-error"},"choices":[]}\n\n')
            if _State.stream_error == "metadata":
                # Synthetic event based on the fields in hermes-agent #65631, not a captured wire response.
                chunks.append(b'data: {"choices":null,"error_type":"BadRequestError","error_message":"private-provider-error"}\n\n')
            if _State.stream_error == "finish":
                finish_reason = "error"
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
        _State.stream_error = ""
        _State.minimum_output_tokens = 0
        _State.finish_reason = "stop"
        _State.image_status = 200
        _State.image_response = {"data": [{"b64_json": "synthetic-image-payload"}]}
        _State.vision_status = 200
        _State.vision_response = {
            "choices": [{"message": {"content": "red, green, blue, yellow"}, "finish_reason": "stop"}]
        }
        _State.tool_call_status = 200
        _State.tool_call_response = {
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {
                    "tool_calls": [{
                        "type": "function",
                        "function": {"name": "endpoint_doctor_probe", "arguments": '{"status":"ok"}'},
                    }]
                },
            }]
        }

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

    def test_tool_calling_is_opt_in_and_validates_without_executing_function(self) -> None:
        report = run_diagnostics(
            self.base_url,
            "fixture-model",
            "tool-call-test-key",
            check_tool_calling=True,
        )
        self.assertTrue(report.chat_ready)
        self.assertTrue(report.tool_calling_ready)
        self.assertEqual(len(_State.requests), 2)
        request = _State.requests[1]
        body = request["body"]
        self.assertEqual(request["authorization"], "Bearer tool-call-test-key")
        self.assertEqual(body["tools"][0]["function"]["name"], "endpoint_doctor_probe")
        self.assertEqual(body["tool_choice"]["function"]["name"], "endpoint_doctor_probe")
        self.assertEqual(body["max_tokens"], 128)
        serialized = json.dumps(report.to_dict())
        self.assertNotIn("tool-call-test-key", serialized)
        self.assertNotIn('{"status":"ok"}', serialized)
        self.assertIn("The function was not executed.", report.checks[-1].detail)

    def test_tool_calling_warns_for_invalid_arguments_without_exposing_response(self) -> None:
        _State.tool_call_response = {
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {
                    "tool_calls": [{
                        "type": "function",
                        "function": {"name": "endpoint_doctor_probe", "arguments": '{"status":"not-ok"}'},
                    }]
                },
            }]
        }
        report = run_diagnostics(self.base_url, "fixture-model", check_tool_calling=True)
        check = next(item for item in report.checks if item.name == "tool calling")
        self.assertEqual(check.status, "warn")
        self.assertIsNone(report.tool_calling_ready)
        self.assertNotIn("not-ok", json.dumps(report.to_dict()))
        self.assertEqual(len(_State.requests), 2)

    def test_tool_calling_rejection_is_optional_warning_and_sanitized(self) -> None:
        _State.tool_call_status = 400
        _State.tool_call_response = {"error": {"message": "private-tool-provider-error"}}
        report = run_diagnostics(self.base_url, "fixture-model", check_tool_calling=True)
        check = next(item for item in report.checks if item.name == "tool calling")
        self.assertEqual(check.status, "warn")
        self.assertIsNone(report.tool_calling_ready)
        self.assertNotIn("private-tool-provider-error", json.dumps(report.to_dict()))
        self.assertEqual(len(_State.requests), 2)

    def test_tool_calling_output_limit_warns_without_retrying(self) -> None:
        _State.tool_call_response = {
            "choices": [{
                "finish_reason": "length",
                "message": {
                    "tool_calls": [{
                        "type": "function",
                        "function": {"name": "endpoint_doctor_probe", "arguments": '{"status":"ok"}'},
                    }]
                },
            }]
        }
        report = run_diagnostics(self.base_url, "fixture-model", check_tool_calling=True)
        check = next(item for item in report.checks if item.name == "tool calling")
        self.assertEqual(check.status, "warn")
        self.assertIn("output limit", check.detail)
        self.assertEqual(len(_State.requests), 2)

    def test_image_generation_is_opt_in_and_uses_one_separate_request(self) -> None:
        report = run_diagnostics(
            self.base_url,
            "fixture-chat-model",
            "hidden-image-test-key",
            check_image_generation=True,
            image_model=" fixture-image-model ",
        )
        self.assertTrue(report.chat_ready)
        self.assertTrue(report.image_generation_ready)
        self.assertEqual(report.image_model, "fixture-image-model")
        self.assertEqual(len(_State.requests), 2)
        image_request = _State.requests[1]
        self.assertEqual(image_request["path"], "/v1/images/generations")
        self.assertEqual(image_request["authorization"], "Bearer hidden-image-test-key")
        self.assertEqual(image_request["body"]["model"], "fixture-image-model")
        self.assertEqual(image_request["body"]["prompt"], "A simple blue circle centered on a plain white background.")
        self.assertEqual(image_request["body"]["n"], 1)
        self.assertTrue(report.to_dict()["image_generation_ready"])
        serialized = json.dumps(report.to_dict())
        self.assertNotIn("hidden-image-test-key", serialized)
        self.assertNotIn("synthetic-image-payload", serialized)
        self.assertNotIn("A simple blue circle", serialized)
        self.assertNotIn("/v1", serialized)

    def test_image_generation_is_reported_as_unchecked_by_default(self) -> None:
        report = run_diagnostics(self.base_url, "fixture-chat-model")
        self.assertIsNone(report.image_generation_ready)
        self.assertIsNone(report.to_dict()["image_model"])

    def test_vision_input_is_opt_in_and_sends_one_synthetic_data_url(self) -> None:
        report = run_diagnostics(
            self.base_url,
            "fixture-chat-model",
            "hidden-vision-test-key",
            check_vision_input=True,
            vision_model=" fixture-vision-model ",
        )
        self.assertTrue(report.chat_ready)
        self.assertTrue(report.vision_input_ready)
        self.assertEqual(report.vision_model, "fixture-vision-model")
        self.assertEqual(len(_State.requests), 2)
        request = _State.requests[1]
        self.assertEqual(request["path"], "/v1/chat/completions")
        self.assertEqual(request["authorization"], "Bearer hidden-vision-test-key")
        self.assertEqual(request["body"]["model"], "fixture-vision-model")
        parts = request["body"]["messages"][0]["content"]
        self.assertEqual(parts[0]["type"], "text")
        image_url = parts[1]["image_url"]["url"]
        self.assertTrue(image_url.startswith("data:image/png;base64,"))
        self.assertEqual(parts[1]["image_url"]["detail"], "low")
        png = base64.b64decode(image_url.split(",", 1)[1], validate=True)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        width = height = color_type = None
        image_data = bytearray()
        offset = 8
        while offset < len(png):
            chunk_size = struct.unpack(">I", png[offset:offset + 4])[0]
            chunk_type = png[offset + 4:offset + 8]
            chunk_data = png[offset + 8:offset + 8 + chunk_size]
            expected_crc = struct.unpack(">I", png[offset + 8 + chunk_size:offset + 12 + chunk_size])[0]
            self.assertEqual(expected_crc, zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF)
            if chunk_type == b"IHDR":
                width, height, bit_depth, color_type, *_ = struct.unpack(">IIBBBBB", chunk_data)
                self.assertEqual(bit_depth, 8)
            elif chunk_type == b"IDAT":
                image_data.extend(chunk_data)
            offset += chunk_size + 12
        self.assertEqual((width, height, color_type), (64, 64, 2))
        pixels = zlib.decompress(image_data)

        def pixel(x: int, y: int) -> bytes:
            row_start = y * (1 + width * 3)
            self.assertEqual(pixels[row_start], 0)
            pixel_start = row_start + 1 + x * 3
            return pixels[pixel_start:pixel_start + 3]

        self.assertEqual(pixel(8, 8), bytes((255, 0, 0)))
        self.assertEqual(pixel(56, 8), bytes((0, 255, 0)))
        self.assertEqual(pixel(56, 56), bytes((0, 0, 255)))
        self.assertEqual(pixel(8, 56), bytes((255, 255, 0)))
        serialized = json.dumps(report.to_dict())
        self.assertNotIn("hidden-vision-test-key", serialized)
        self.assertNotIn("red, green, blue, yellow", serialized)
        self.assertNotIn("data:image/png", serialized)

    def test_vision_input_warns_when_color_sequence_is_not_confirmed(self) -> None:
        _State.vision_response = {
            "choices": [{"message": {"content": "Sequence: red, green, blue, yellow."}, "finish_reason": "stop"}]
        }
        report = run_diagnostics(self.base_url, "fixture-chat-model", check_vision_input=True)
        check = next(item for item in report.checks if item.name == "vision input")
        self.assertEqual(check.status, "warn")
        self.assertIsNone(report.vision_input_ready)
        self.assertNotIn("Sequence: red, green, blue, yellow.", json.dumps(report.to_dict()))
        self.assertEqual(len(_State.requests), 2)

    def test_vision_input_reports_http_failure_without_provider_body(self) -> None:
        _State.vision_status = 502
        _State.vision_response = {"error": {"message": "private-vision-error"}}
        report = run_diagnostics(self.base_url, "fixture-chat-model", check_vision_input=True)
        check = next(item for item in report.checks if item.name == "vision input")
        self.assertEqual(check.status, "fail")
        self.assertIn("server error 502", check.detail)
        self.assertFalse(report.vision_input_ready)
        self.assertNotIn("private-vision-error", json.dumps(report.to_dict()))
        self.assertEqual(len(_State.requests), 2)

    def test_vision_input_oversize_response_is_unconfirmed_without_body(self) -> None:
        _State.vision_response = {
            "choices": [{"message": {"content": "x" * 200}, "finish_reason": "stop"}]
        }
        with patch("llm_endpoint_doctor.core._MAX_JSON_RESPONSE_BYTES", 32):
            report = run_diagnostics(self.base_url, "fixture-chat-model", check_vision_input=True)
        check = next(item for item in report.checks if item.name == "vision input")
        self.assertEqual(check.status, "warn")
        self.assertIsNone(report.vision_input_ready)
        self.assertNotIn("x" * 50, json.dumps(report.to_dict()))
        self.assertEqual(len(_State.requests), 2)

    def test_vision_model_validation_precedes_any_request(self) -> None:
        with self.assertRaisesRegex(ValueError, "only with the vision-input check"):
            run_diagnostics(self.base_url, "fixture-chat-model", vision_model="other-model")
        with self.assertRaisesRegex(ValueError, "cannot be empty"):
            run_diagnostics(self.base_url, "fixture-chat-model", check_vision_input=True, vision_model=" ")
        self.assertEqual(_State.requests, [])

    def test_cli_vision_check_prints_model_and_is_opt_in(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main([
                "--base-url", self.base_url,
                "--model", "fixture-chat-model",
                "--check-vision-input",
                "--vision-model", "fixture-vision-model",
                "--no-prompt",
            ])
        self.assertEqual(exit_code, 0)
        self.assertIn("Vision model: fixture-vision-model", output.getvalue())
        self.assertIn("[PASS] vision input", output.getvalue())
        self.assertEqual(len(_State.requests), 2)

    def test_image_generation_accepts_url_response_without_fetching_it(self) -> None:
        _State.image_response = {"data": [{"url": "https://images.example.test/generated.png"}]}
        report = run_diagnostics(
            self.base_url,
            "fixture-chat-model",
            check_image_generation=True,
            image_model="fixture-image-model",
        )
        self.assertTrue(report.image_generation_ready)
        self.assertEqual(len(_State.requests), 2)
        self.assertEqual(_State.requests[1]["path"], "/v1/images/generations")
        self.assertNotIn("images.example.test", json.dumps(report.to_dict()))

    def test_image_generation_rejects_malformed_success_and_sanitizes_http_errors(self) -> None:
        _State.image_response = {"data": [{"url": "file:///tmp/private.png"}]}
        report = run_diagnostics(
            self.base_url,
            "fixture-chat-model",
            check_image_generation=True,
            image_model="fixture-image-model",
        )
        check = next(item for item in report.checks if item.name == "image generation")
        self.assertEqual(check.status, "fail")
        self.assertNotIn("/tmp/private.png", json.dumps(report.to_dict()))

        _State.requests = []
        _State.image_status = 401
        _State.image_response = {"error": {"message": "private-provider-error"}}
        report = run_diagnostics(
            self.base_url,
            "fixture-chat-model",
            check_image_generation=True,
            image_model="fixture-image-model",
        )
        check = next(item for item in report.checks if item.name == "image generation")
        self.assertEqual(check.status, "fail")
        self.assertNotIn("private-provider-error", json.dumps(report.to_dict()))

    def test_image_generation_oversize_response_warns_without_recording_body(self) -> None:
        _State.image_response = {"data": [{"b64_json": "x" * 200}]}
        with patch("llm_endpoint_doctor.core._MAX_IMAGE_RESPONSE_BYTES", 32):
            report = run_diagnostics(
                self.base_url,
                "fixture-chat-model",
                check_image_generation=True,
                image_model="fixture-image-model",
            )
        check = next(item for item in report.checks if item.name == "image generation")
        self.assertEqual(check.status, "warn")
        self.assertIsNone(report.image_generation_ready)
        self.assertNotIn("x" * 50, json.dumps(report.to_dict()))
        self.assertEqual(len(_State.requests), 2)

    def test_cli_image_check_prints_selected_model_and_sends_only_when_selected(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main([
                "--base-url", self.base_url,
                "--model", "fixture-chat-model",
                "--check-image-generation",
                "--image-model", "fixture-image-model",
                "--no-prompt",
            ])
        self.assertEqual(exit_code, 0)
        self.assertIn("Image model: fixture-image-model", output.getvalue())
        self.assertIn("[PASS] image generation", output.getvalue())
        self.assertEqual(len(_State.requests), 2)

    def test_image_model_validation_is_explicit(self) -> None:
        with self.assertRaisesRegex(ValueError, "image model ID is required"):
            run_diagnostics(self.base_url, "fixture-chat-model", check_image_generation=True)
        with self.assertRaisesRegex(ValueError, "only with the image-generation check"):
            run_diagnostics(self.base_url, "fixture-chat-model", image_model="fixture-image-model")
        self.assertEqual(_State.requests, [])

    def test_cli_requires_image_model_before_prompting_or_sending(self) -> None:
        with patch("getpass.getpass", side_effect=AssertionError("key prompt must not run")):
            with self.assertRaises(SystemExit):
                main([
                    "--base-url", self.base_url,
                    "--model", "fixture-chat-model",
                    "--check-image-generation",
                ])
        self.assertEqual(_State.requests, [])

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

    def test_stream_error_overrides_done_and_usage_without_changing_basic_chat(self) -> None:
        for error in ("object", "finish", "metadata"):
            with self.subTest(error=error):
                _State.stream_error = error
                _State.requests = []
                report = run_diagnostics(self.base_url, "fixture-model", check_streaming=True, check_stream_options=True)
                self.assertTrue(report.chat_ready)
                self.assertEqual([check.status for check in report.checks], ["pass", "warn", "warn"])
                self.assertEqual(len(_State.requests), 3)
                for check in report.checks[1:]:
                    self.assertIn("stream reported an error", check.detail)
                self.assertNotIn("private-provider-error", json.dumps(report.to_dict()))

    def test_normal_stream_done_and_usage_still_pass(self) -> None:
        report = run_diagnostics(self.base_url, "fixture-model", check_streaming=True, check_stream_options=True)
        self.assertTrue(report.chat_ready)
        self.assertEqual([check.status for check in report.checks], ["pass", "pass", "pass"])
        self.assertEqual(len(_State.requests), 3)

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

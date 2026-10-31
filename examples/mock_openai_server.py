#!/usr/bin/env python3
"""Run a deterministic OpenAI-compatible endpoint on loopback for the README demo."""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


MAX_BODY_BYTES = 1_000_000


class MockOpenAIHandler(BaseHTTPRequestHandler):
    server_version = "LLMEndpointDoctorDemo/1.0"
    minimum_output_tokens = 0
    reject_tools = False
    split_routes = False

    def log_message(self, *_: Any) -> None:
        # Do not print request headers, keys, or request bodies.
        return

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if self.split_routes and hasattr(self, "demo_route"):
            self.send_header("X-Routed-Via", self.demo_route)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path != "/v1/models":
            self._send_json({"error": {"message": "Not found"}}, 404)
            return
        self._send_json({
            "object": "list",
            "data": [{"id": "demo-model", "object": "model", "owned_by": "local-demo"}],
        })

    def do_POST(self) -> None:
        if self.path not in {"/v1/chat/completions", "/v1/images/generations", "/v1/responses"}:
            self._send_json({"error": {"message": "Not found"}}, 404)
            return

        try:
            body_size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json({"error": {"message": "Invalid Content-Length"}}, 400)
            return
        if body_size <= 0 or body_size > MAX_BODY_BYTES:
            self._send_json({"error": {"message": "Invalid request size"}}, 413)
            return
        try:
            request = json.loads(self.rfile.read(body_size))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json({"error": {"message": "Expected JSON"}}, 400)
            return
        if not isinstance(request, dict):
            self._send_json({"error": {"message": "Expected a JSON object"}}, 400)
            return

        model = request.get("model")
        if not isinstance(model, str) or not model:
            self._send_json({"error": {"message": "A model is required"}}, 400)
            return

        if self.split_routes:
            self.demo_route = "example/tool-model" if request.get("tools") else "example/chat-model"

        if self.path == "/v1/responses":
            if not isinstance(request.get("input"), str):
                self._send_json({"error": {"message": "Responses demo expects input text"}}, 400)
                return
            self._send_json({"status": "completed", "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "fixture-ok"}]}]})
            return

        if self.path == "/v1/images/generations":
            prompt = request.get("prompt")
            if not isinstance(prompt, str) or not prompt or request.get("n") != 1:
                self._send_json({"error": {"message": "Image demo expects a prompt and n=1"}}, 400)
                return
            self._send_json({"created": 0, "data": [{"b64_json": "synthetic-placeholder-not-image"}]})
            return

        messages = request.get("messages")
        content_parts = messages[0].get("content") if isinstance(messages, list) and messages and isinstance(messages[0], dict) else None
        if isinstance(content_parts, list) and any(
            isinstance(part, dict) and part.get("type") == "image_url"
            for part in content_parts
        ):
            self._send_json({
                "id": "chatcmpl-local-vision-demo",
                "object": "chat.completion",
                "model": model,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "red, green, blue, yellow"},
                    "finish_reason": "stop",
                }],
            })
            return

        if request.get("tools"):
            if self.reject_tools:
                self._send_json({"error": {"message": "Synthetic tool rejection"}}, 400)
                return
            self._send_json({
                "id": "chatcmpl-local-tool-demo",
                "object": "chat.completion",
                "model": model,
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "tool_calls": [{
                            "id": "call_local_probe",
                            "type": "function",
                            "function": {
                                "name": "endpoint_doctor_probe",
                                "arguments": '{"status":"ok"}',
                            },
                        }],
                    },
                    "finish_reason": "tool_calls",
                }],
            })
            return

        cap = request.get("max_completion_tokens", request.get("max_tokens", 0))
        limited = type(cap) is int and cap < self.minimum_output_tokens

        if request.get("stream") is True:
            stream_options = request.get("stream_options")
            include_usage = (
                isinstance(stream_options, dict)
                and stream_options.get("include_usage") is True
            )
            self._send_stream(include_usage=include_usage, limited=limited)
            return

        response_format = request.get("response_format")
        wants_json = isinstance(response_format, dict) and response_format.get("type") == "json_object"
        content = '{"status":"ok"}' if wants_json else "endpoint-ok"
        if limited:
            content = ""
        self._send_json({
            "id": "chatcmpl-local-demo",
            "object": "chat.completion",
            "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "length" if limited else "stop",
            }],
        })

    def _send_stream(self, *, include_usage: bool, limited: bool = False) -> None:
        delta = {} if limited else {"content": "endpoint-ok"}
        chunks = ['data: ' + json.dumps({"choices": [{"index": 0, "delta": delta, "finish_reason": "length" if limited else "stop"}]}) + '\n\n']
        if include_usage:
            chunks.append('data: {"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}\n\n')
        chunks.append("data: [DONE]\n\n")
        body = "".join(chunks).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    parser = argparse.ArgumentParser(description="Start a local mock OpenAI-compatible endpoint.")
    parser.add_argument("--port", type=int, default=8765, help="Loopback port (default: 8765)")
    parser.add_argument("--simulate-token-limit", action="store_true", help="Synthetic length-ended response below 64 output tokens; no real model")
    parser.add_argument("--reject-tools", action="store_true", help="Synthetic chat succeeds while tool requests return HTTP 400; no real model")
    parser.add_argument("--split-routes", action="store_true", help="Return different fictional route headers for chat and tool calls")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    try:
        MockOpenAIHandler.minimum_output_tokens = 64 if args.simulate_token_limit else 0
        MockOpenAIHandler.reject_tools = args.reject_tools
        MockOpenAIHandler.split_routes = args.split_routes
        server = ThreadingHTTPServer(("127.0.0.1", args.port), MockOpenAIHandler)
    except OSError as exc:
        parser.error(f"could not listen on 127.0.0.1:{args.port}: {exc}")
    print(f"Local demo endpoint ready at http://127.0.0.1:{args.port}/v1 (Ctrl+C to stop)")
    if args.simulate_token_limit:
        print("Synthetic token-limit mode: caps below 64 return finish_reason=length; no real model is running.")
    if args.reject_tools:
        print("Synthetic tool-rejection mode: ordinary chat succeeds, tool requests return HTTP 400; no real model is running.")
    if args.split_routes:
        print("Synthetic split-route mode: chat and tool requests receive different fictional route labels.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nLocal demo stopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

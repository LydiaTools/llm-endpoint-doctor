#!/usr/bin/env python3
"""Count actual doctor requests against a keyless synthetic loopback endpoint."""

from __future__ import annotations

import json
import io
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from llm_endpoint_doctor.core import run_diagnostics
from mock_openai_server import MockOpenAIHandler


def main() -> None:
    calls: list[str] = []

    class CountedHandler(MockOpenAIHandler):
        def do_GET(self) -> None:
            calls.append(f"GET {self.path}")
            super().do_GET()

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            body = json.loads(raw)
            messages = body.get("messages")
            content = messages[0].get("content") if isinstance(messages, list) and messages and isinstance(messages[0], dict) else None
            is_vision = isinstance(content, list) and any(
                isinstance(part, dict) and part.get("type") == "image_url"
                for part in content
            )
            is_tool_call = bool(body.get("tools"))
            if is_vision:
                calls.append(f"POST {self.path} [vision]")
            elif is_tool_call:
                calls.append(f"POST {self.path} [tool calling]")
            else:
                calls.append(f"POST {self.path}")
            self.rfile = io.BytesIO(raw)
            super().do_POST()

    server = ThreadingHTTPServer(("127.0.0.1", 0), CountedHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    results = []
    try:
        base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
        for name, flags, expected in [
            ("default", {}, (0, 1, 0, 0, 0, 0)),
            ("responses-only", {"responses_only": True}, (0, 0, 0, 0, 0, 1)),
            ("all-options", {
                "check_model_list": True, "check_streaming": True,
                "check_stream_options": True, "check_json_mode": True,
                "check_image_generation": True, "image_model": "demo-image-model",
                "check_vision_input": True, "vision_model": "demo-vision-model",
                "check_tool_calling": True,
            }, (1, 4, 1, 1, 1, 0)),
            ("image-check", {
                "check_image_generation": True, "image_model": "demo-image-model",
            }, (0, 1, 1, 0, 0, 0)),
            ("vision-check", {
                "check_vision_input": True, "vision_model": "demo-vision-model",
            }, (0, 1, 0, 1, 0, 0)),
            ("tool-check", {"check_tool_calling": True}, (0, 1, 0, 0, 1, 0)),
        ]:
            calls.clear()
            run_diagnostics(base_url=base_url, model="demo-model", **flags)
            counts = (
                calls.count("GET /v1/models"),
                calls.count("POST /v1/chat/completions"),
                calls.count("POST /v1/images/generations"),
                calls.count("POST /v1/chat/completions [vision]"),
                calls.count("POST /v1/chat/completions [tool calling]"),
                calls.count("POST /v1/responses"),
            )
            if counts != expected:
                raise RuntimeError(f"Unexpected request counts for {name}: {counts}")
            results.append({
                "mode": name,
                "http_requests": len(calls),
                "model_catalog_requests": counts[0],
                "chat_generation_requests": counts[1],
                "image_generation_requests": counts[2],
                "vision_input_requests": counts[3],
                "tool_calling_requests": counts[4],
                "responses_requests": counts[5],
            })
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    if thread.is_alive():
        raise RuntimeError("Loopback server did not stop")
    print(json.dumps({
        "schema_version": 1, "scope": "synthetic loopback; no provider or billing measurement",
        "results": results, "server_closed": True,
    }, indent=2))


if __name__ == "__main__":
    main()

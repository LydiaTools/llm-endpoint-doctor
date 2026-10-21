#!/usr/bin/env python3
"""Count actual doctor requests against a keyless synthetic loopback endpoint."""

from __future__ import annotations

import json
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
            calls.append(f"POST {self.path}")
            super().do_POST()

    server = ThreadingHTTPServer(("127.0.0.1", 0), CountedHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    results = []
    try:
        base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
        for name, flags, expected in [
            ("default", {}, (0, 1, 0)),
            ("all-options", {
                "check_model_list": True, "check_streaming": True,
                "check_stream_options": True, "check_json_mode": True,
                "check_image_generation": True, "image_model": "demo-image-model",
            }, (1, 4, 1)),
            ("image-check", {
                "check_image_generation": True, "image_model": "demo-image-model",
            }, (0, 1, 1)),
        ]:
            calls.clear()
            run_diagnostics(base_url=base_url, model="demo-model", **flags)
            counts = (
                calls.count("GET /v1/models"),
                calls.count("POST /v1/chat/completions"),
                calls.count("POST /v1/images/generations"),
            )
            if counts != expected:
                raise RuntimeError(f"Unexpected request counts for {name}: {counts}")
            results.append({
                "mode": name,
                "http_requests": len(calls),
                "model_catalog_requests": counts[0],
                "chat_generation_requests": counts[1],
                "image_generation_requests": counts[2],
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

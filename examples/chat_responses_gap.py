#!/usr/bin/env python3
"""Show a chat-only endpoint using a disposable, keyless local server."""
from __future__ import annotations

import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from llm_endpoint_doctor.core import run_diagnostics
from mock_openai_server import MockOpenAIHandler


class ChatOnlyHandler(MockOpenAIHandler):
    def do_POST(self) -> None:
        if self.path == '/v1/responses':
            self._send_json({'error': {'message': 'Synthetic unavailable route'}}, 404)
            return
        super().do_POST()


def main() -> int:
    server = ThreadingHTTPServer(('127.0.0.1', 0), ChatOnlyHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f'http://127.0.0.1:{server.server_address[1]}/v1'
        chat = run_diagnostics(base, 'demo-model')
        responses = run_diagnostics(base, 'demo-model', responses_only=True)
        print('Synthetic local gateway: chat available; Responses returns HTTP 404')
        print(f"Chat Completions: {'PASS' if chat.chat_ready else 'NOT CONFIRMED'}")
        print(f"Responses text: {'PASS' if responses.responses_text_ready else 'NOT CONFIRMED'}")
        print('Two loopback requests; no key, real provider, model, or Codex run.')
        return 0 if chat.chat_ready and responses.responses_text_ready is False else 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


if __name__ == '__main__':
    raise SystemExit(main())

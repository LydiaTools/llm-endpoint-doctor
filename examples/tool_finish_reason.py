#!/usr/bin/env python3
"""Compare two synthetic tool responses on a disposable local server."""
from __future__ import annotations

import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from llm_endpoint_doctor.core import run_diagnostics
from mock_openai_server import MockOpenAIHandler


class FinishReasonHandler(MockOpenAIHandler):
    finish_reason = 'tool_calls'
    request_count = 0

    def do_POST(self) -> None:
        type(self).request_count += 1
        super().do_POST()

    def _send_json(self, payload, status=200) -> None:
        choices = payload.get('choices')
        if choices and choices[0].get('message', {}).get('tool_calls'):
            choices[0]['finish_reason'] = type(self).finish_reason
        super()._send_json(payload, status)


def main() -> int:
    server = ThreadingHTTPServer(('127.0.0.1', 0), FinishReasonHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f'http://127.0.0.1:{server.server_address[1]}/v1'
        results = []
        for reason in ('tool_calls', 'stop'):
            FinishReasonHandler.finish_reason = reason
            report = run_diagnostics(base, 'demo-model', check_tool_calling=True)
            check = next(item for item in report.checks if item.name == 'tool calling')
            results.append((check.status, report.tool_calling_ready, check.detail))
            print(f'finish_reason={reason}: {check.status.upper()} — {check.detail}')
        print(f'{FinishReasonHandler.request_count} loopback requests; no key, real provider, model or tool execution.')
        return 0 if (results[0][0:2] == ('pass', True) and results[1][0:2] == ('warn', None)
                     and 'finish_reason was stop' in results[1][2] and FinishReasonHandler.request_count == 4) else 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


if __name__ == '__main__':
    raise SystemExit(main())

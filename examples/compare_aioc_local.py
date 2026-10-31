#!/usr/bin/env python3
"""Compare two probe commands against a disposable synthetic loopback endpoint."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

REQUESTS: list[tuple[str, str]] = []
ROOT = Path(__file__).resolve().parents[1]


class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        pass

    def _respond(self) -> None:
        REQUESTS.append((self.command, self.path))
        self.rfile.read(int(self.headers.get('Content-Length', '0')))
        if self.path == '/v1/models':
            status, data = 200, {'object': 'list', 'data': [{'id': 'demo-model', 'object': 'model'}]}
        elif self.path == '/v1/chat/completions':
            status, data = 200, {
                'id': 'chatcmpl-synthetic', 'object': 'chat.completion', 'model': 'demo-model',
                'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'hello'}, 'finish_reason': 'stop'}],
            }
        else:
            status, data = 404, {'error': {'message': 'not found'}}
        body = json.dumps(data).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _respond
    do_POST = _respond
    do_DELETE = _respond


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--aioc', required=True, help='Path to aioc v0.4.3 executable in its virtual environment')
    args = parser.parse_args()
    executable = str(Path(args.aioc).expanduser().resolve(strict=True))
    server = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_address[1]}'
    try:
        with TemporaryDirectory(prefix='probe-comparison-') as temp:
            # Do not pass the user's API credentials or proxy settings to either probe.
            env = {'PATH': os.defpath, 'HOME': temp, 'NO_PROXY': '127.0.0.1,localhost',
                   'no_proxy': '127.0.0.1,localhost', 'PYTHONPATH': str(ROOT / 'src')}
            if os.name == 'nt' and 'SYSTEMROOT' in os.environ:
                env['SYSTEMROOT'] = os.environ['SYSTEMROOT']
            version = subprocess.run([executable, '--version'], env=env, text=True,
                                     capture_output=True, timeout=10, check=False)
            if version.returncode or version.stdout.strip() != 'aioc 0.4.3':
                print(f'Expected aioc 0.4.3; got {version.stdout.strip()!r}', file=sys.stderr)
                return 1
            doctor = [sys.executable, '-m', 'llm_endpoint_doctor', '--base-url', base + '/v1',
                      '--model', 'demo-model', '--no-prompt']
            commands = [
                ('Doctor: chat', doctor),
                ('Doctor: Responses', doctor + ['--responses-only']),
                ('aioc: selected routes', [executable, 'probe', base, '--model', 'demo-model',
                 '--endpoints-filter', '^/v1/(chat/completions|responses)$', '--timeout', '2',
                 '--report', str(Path(temp) / 'report.json')]),
            ]
            for label, command in commands:
                REQUESTS.clear()
                result = subprocess.run(command, env=env, text=True, capture_output=True, timeout=30, check=False)
                print(f'\n{label}: exit {result.returncode}; {len(REQUESTS)} request(s)')
                for method, path in REQUESTS:
                    print(f'  {method} {path}')
                print(result.stdout.strip())
                if result.stderr:
                    print(result.stderr.strip(), file=sys.stderr)
                expected_exit = 0 if label == 'Doctor: chat' else 1
                if result.returncode != expected_exit:
                    return 1
                if label == 'aioc: selected routes':
                    events = json.loads((Path(temp) / 'report.json').read_text(encoding='utf-8'))
                    for event in events:
                        print(f"  {event['endpoint']} [{event['phase']}] {event['status']}: {event['detail']}")
                    if not any(event.get('endpoint') == '/v1/responses' and event.get('http_status') == 404
                               for event in events):
                        return 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

from __future__ import annotations

import io
import json
import threading
import unittest
from contextlib import redirect_stdout, redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from llm_endpoint_doctor.cli import main
from llm_endpoint_doctor.core import run_diagnostics


class State:
    payload = {}
    status = 200
    requests = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        size = int(self.headers['Content-Length'])
        State.requests.append((self.path, json.loads(self.rfile.read(size)), self.headers.get('Authorization')))
        raw = json.dumps(State.payload).encode()
        self.send_response(State.status)
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class ResponsesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}/v1'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        State.requests = []
        State.status = 200
        State.payload = {
            'status': 'completed',
            'output': [
                {'type': 'reasoning', 'summary': []},
                {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'private-fixture-text'}]},
            ],
        }

    def test_responses_only_sends_one_request_and_hides_content_and_key(self):
        report = run_diagnostics(self.url, 'demo', 'secret-fixture-key', responses_only=True)
        self.assertTrue(report.responses_text_ready)
        self.assertIsNone(report.chat_ready)
        self.assertEqual(len(State.requests), 1)
        path, body, authorization = State.requests[0]
        self.assertEqual(path, '/v1/responses')
        self.assertEqual(body['model'], 'demo')
        self.assertEqual(body['max_output_tokens'], 128)
        self.assertNotIn('messages', body)
        self.assertEqual(authorization, 'Bearer secret-fixture-key')
        for secret in ('secret-fixture-key', 'private-fixture-text', 'short greeting'):
            self.assertNotIn(secret, json.dumps(report.to_dict()))

    def test_incomplete_or_wrong_shape_is_not_confirmed(self):
        for payload in (
            {'status': 'incomplete', 'output': State.payload['output']},
            {'status': 'failed', 'output': State.payload['output']},
            {'status': 'completed', 'error': {'message': 'private'}, 'output': State.payload['output']},
            {'status': 'completed', 'output': [{'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': '  '}]}]},
            {'choices': [{'message': {'content': 'wrong protocol'}}]},
            {'status': 'completed', 'output': [{'type': 'reasoning'}]},
        ):
            with self.subTest(payload=payload):
                State.payload = payload
                self.assertFalse(run_diagnostics(self.url, 'demo', responses_only=True).responses_text_ready)

    def test_rejection_is_sanitized_and_never_retried(self):
        State.status = 404
        State.payload = {'error': {'message': 'private-error-body'}}
        report = run_diagnostics(self.url, 'demo', responses_only=True)
        self.assertFalse(report.responses_text_ready)
        self.assertEqual(len(State.requests), 1)
        self.assertNotIn('private-error-body', json.dumps(report.to_dict()))

    def test_cli_separates_chat_and_responses_results(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(['--base-url', self.url, '--model', 'demo', '--no-prompt', '--responses-only', '--max-output-tokens', '256'])
        self.assertEqual(code, 0)
        self.assertIn('Responses text: CONFIRMED', output.getvalue())
        self.assertIn('Codex compatibility were not checked', output.getvalue())
        self.assertEqual(State.requests[0][1]['max_output_tokens'], 256)
        self.assertNotIn('private-fixture-text', output.getvalue())

    def test_conflicting_options_fail_before_network(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(['--base-url', self.url, '--model', 'demo', '--no-prompt', '--responses-only', '--check-streaming'])
        self.assertEqual(State.requests, [])

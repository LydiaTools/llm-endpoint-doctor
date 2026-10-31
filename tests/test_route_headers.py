import importlib.util
import json
import os
from email.message import Message
from http.server import ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from llm_endpoint_doctor.core import _route_fingerprint, run_diagnostics

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('route_mock', ROOT / 'examples' / 'mock_openai_server.py')
mock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mock)


class RouteHeaderTest(unittest.TestCase):
    def serve(self, handler):
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(worker.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f'http://127.0.0.1:{server.server_port}/v1'

    def test_different_headers_are_compared_without_disclosing_them(self):
        class Handler(mock.MockOpenAIHandler):
            split_routes = True
            requests = 0
            def do_POST(self):
                type(self).requests += 1
                return super().do_POST()
        base = self.serve(Handler)
        with tempfile.TemporaryDirectory() as td:
            report = Path(td) / 'report.json'
            env = os.environ.copy()
            env['PYTHONPATH'] = str(ROOT / 'src')
            env['NO_PROXY'] = env['no_proxy'] = '127.0.0.1,localhost'
            proc = subprocess.run([sys.executable, '-m', 'llm_endpoint_doctor.cli',
                                   '--base-url', base, '--model', 'demo-model', '--no-prompt',
                                   '--check-tool-calling', '--compare-route-headers', '--json-report', str(report)],
                                  cwd=ROOT, env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn('header values differed', proc.stdout)
            for secret in ('example/chat-model', 'example/tool-model'):
                self.assertNotIn(secret, proc.stdout)
                self.assertNotIn(secret, report.read_text())
            self.assertEqual(Handler.requests, 2)
            rows = json.loads(report.read_text())['checks']
            self.assertTrue(any(x['name'] == 'route header comparison' and 'differed' in x['detail'] for x in rows))

    def test_matching_values_are_not_reported_or_saved(self):
        class Handler(mock.MockOpenAIHandler):
            split_routes = True
            def _send_json(self, payload, status=200):
                self.demo_route = 'private-provider/secret-model'
                return super()._send_json(payload, status)
        result = run_diagnostics(self.serve(Handler), 'demo-model', 'synthetic-key',
                                 check_tool_calling=True, compare_route_headers=True)
        self.assertIn('header values matched', result.checks[-1].detail)
        self.assertNotIn('private-provider', json.dumps(result.to_dict()))
        self.assertNotIn('secret-model', json.dumps(result.to_dict()))
        self.assertNotIn('synthetic-key', json.dumps(result.to_dict()))

    def test_failed_or_missing_response_is_unknown(self):
        class Handler(mock.MockOpenAIHandler):
            split_routes = True
            reject_tools = True
        result = run_diagnostics(self.serve(Handler), 'demo-model',
                                 check_tool_calling=True, compare_route_headers=True)
        self.assertIn('Comparison unavailable', result.checks[-1].detail)
        class NoHeaders(mock.MockOpenAIHandler):
            split_routes = False
        result = run_diagnostics(self.serve(NoHeaders), 'demo-model',
                                 check_tool_calling=True, compare_route_headers=True)
        self.assertIn('Comparison unavailable', result.checks[-1].detail)

    def test_ambiguous_duplicate_cache_and_invalid_headers_are_rejected(self):
        for values in ([], ['cache'], ['idempotency'], ['a/b', 'c/d'], ['a/'], ['/b'], ['a/b, c/d'], ['a/' + 'x'*300], ['a/b\ncontrol']):
            headers = Message()
            for value in values: headers.add_header('X-Routed-Via', value)
            self.assertIsNone(_route_fingerprint(headers), values)
        headers = Message(); headers.add_header('X-Routed-Via', 'provider/model%2Fversion')
        self.assertIsNotNone(_route_fingerprint(headers))

    def test_flag_requires_tool_check_before_requests(self):
        proc = subprocess.run([sys.executable, '-m', 'llm_endpoint_doctor.cli', '--base-url',
                               'http://127.0.0.1:1/v1', '--model', 'x', '--no-prompt', '--compare-route-headers'],
                              cwd=ROOT, env={**os.environ, 'PYTHONPATH': str(ROOT / 'src')}, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn('requires --check-tool-calling', proc.stderr)
        with self.assertRaisesRegex(ValueError, 'requires the tool-calling check'):
            run_diagnostics('http://127.0.0.1:1/v1', 'x', compare_route_headers=True)

    def test_default_does_not_add_or_expose_route_comparison(self):
        class Handler(mock.MockOpenAIHandler):
            split_routes = True
        result = run_diagnostics(self.serve(Handler), 'demo-model', check_tool_calling=True)
        self.assertFalse(any(x.name == 'route header comparison' for x in result.checks))
        self.assertNotIn('example/chat-model', json.dumps(result.to_dict()))
        self.assertNotIn('example/tool-model', json.dumps(result.to_dict()))

if __name__ == '__main__':
    unittest.main()

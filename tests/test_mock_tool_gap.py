import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("local_mock", ROOT / "examples" / "mock_openai_server.py")
mock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mock)


class MockToolGapTest(unittest.TestCase):
    def test_chat_success_and_tool_rejection_are_distinct(self):
        class Handler(mock.MockOpenAIHandler):
            reject_tools = True
            requests = 0

            def do_POST(self):
                type(self).requests += 1
                return super().do_POST()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT / "src")
            env["NO_PROXY"] = env["no_proxy"] = "127.0.0.1,localhost"
            result = subprocess.run(
                [sys.executable, "-m", "llm_endpoint_doctor.cli", "--base-url",
                 f"http://127.0.0.1:{server.server_port}/v1", "--model", "demo-model",
                 "--no-prompt", "--check-tool-calling"],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("[PASS] chat completion", result.stdout)
            self.assertIn("[WARN] tool calling", result.stdout)
            self.assertIn("Basic chat: READY", result.stdout)
            self.assertNotIn("Synthetic tool rejection", result.stdout)
            self.assertEqual(Handler.requests, 2)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)


if __name__ == "__main__":
    unittest.main()

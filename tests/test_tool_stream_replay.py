from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "replay_tool_stream.py"
spec = importlib.util.spec_from_file_location("tool_stream_replay", EXAMPLE)
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)


class ToolStreamReplayTests(unittest.TestCase):
    def setUp(self):
        self.cases = json.loads(replay.CASES.read_text())["cases"]

    def test_interleaved_arguments_stay_with_their_own_call(self):
        case = next(c for c in self.cases if c["name"] == "interleaved_calls")
        self.assertEqual(replay._assemble_calls(case["sse"]), [
            {"id": "call_blue", "name": "choose_label", "arguments": {"label": "blue"}},
            {"id": "call_sum", "name": "sum_pair", "arguments": {"left": 2, "right": 3}},
        ])
        duplicate_id = case["sse"].replace("call_sum", "call_blue")
        self.assertEqual(replay.inspect_stream(duplicate_id)["reason"], "duplicate_call_identity")

    def test_complete_json_does_not_override_length_finish(self):
        case = next(c for c in self.cases if c["name"] == "length_even_with_valid_json")
        self.assertEqual(replay.inspect_stream(case["sse"])["reason"], "not_a_completed_tool_turn")

    def test_cli_capture_output_does_not_disclose_arguments(self):
        text = self.cases[0]["sse"].replace("blue", "private-capture-marker")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private-filename-marker.sse"
            path.write_text(text)
            result = subprocess.run([sys.executable, str(EXAMPLE), "--file", str(path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["call_count"], 1)
        self.assertNotIn("private-capture-marker", result.stdout + result.stderr)
        self.assertNotIn("private-filename-marker", result.stdout + result.stderr)

    def test_default_controls_and_post_done_data(self):
        result = subprocess.run([sys.executable, str(EXAMPLE)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertTrue(all(c["matches_expected"] for c in data["cases"]))
        self.assertTrue(data["wrong_per_delta_identity_rule_rejects_valid_control"])
        text = self.cases[0]["sse"] + 'data: {"choices":[]}\n\n'
        self.assertEqual(replay.inspect_stream(text)["reason"], "data_after_done")

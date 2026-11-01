"""Offline, bounded Chat Completions tool-stream example. Never executes tools."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

CASES = Path(__file__).with_name("tool_stream_cases.json")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from llm_endpoint_doctor.tool_stream import (
    MAX_BYTES,
    ReplayError,
    assemble_tool_calls,
    inspect_tool_stream,
    iter_sse_events,
)

_assemble_calls = assemble_tool_calls


def inspect_stream(text: str) -> dict[str, Any]:
    return inspect_tool_stream(text)


def every_delta_identity_rule_rejects(text: str) -> bool:
    """Deliberately wrong consumer rule, used only as a labeled counterexample."""
    for event in iter_sse_events(text):
        if event == "[DONE]":
            continue
        for choice in json.loads(event).get("choices", []):
            for item in choice.get("delta", {}).get("tool_calls", []):
                if not item.get("id") or not item.get("function", {}).get("name"):
                    return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, help="Inspect one local SSE capture; output omits its text and arguments")
    args = parser.parse_args(argv)
    if args.file:
        try:
            with args.file.open("rb") as handle:
                raw = handle.read(MAX_BYTES + 1)
            result = inspect_stream(raw.decode("utf-8")) if len(raw) <= MAX_BYTES else {"accepted": False, "reason": "capture_too_large", "call_count": 0}
        except (OSError, UnicodeError):
            result = {"accepted": False, "reason": "file_unavailable_or_not_utf8", "call_count": 0}
        print(json.dumps(result))
        return 0 if result["accepted"] else 1
    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    rows = []
    for case in cases:
        result = inspect_stream(case["sse"])
        matched = result == case["expected"]
        if matched and case.get("expected_calls"):
            matched = assemble_tool_calls(case["sse"]) == case["expected_calls"]
        rows.append({"case": case["name"], **result, "matches_expected": matched})
    counterexample = every_delta_identity_rule_rejects(cases[0]["sse"])
    print(json.dumps({"synthetic": True, "network_requests": 0, "tools_executed": 0, "cases": rows,
                      "wrong_per_delta_identity_rule_rejects_valid_control": counterexample}, indent=2))
    return 0 if all(row["matches_expected"] for row in rows) and counterexample else 1


if __name__ == "__main__":
    raise SystemExit(main())

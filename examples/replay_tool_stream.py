"""Offline, bounded Chat Completions tool-stream example. Never executes tools."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

MAX_BYTES = 1_000_000
MAX_ARGUMENT_CHARS = 64_000
CASES = Path(__file__).with_name("tool_stream_cases.json")


class ReplayError(ValueError):
    pass


def _events(text: str):
    data: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").splitlines():
        if not line:
            if data:
                yield "\n".join(data)
                data = []
        elif line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
        elif line == "data":
            data.append("")
    if data:
        raise ReplayError("incomplete_sse_event")


def _reject_constant(_: str):
    raise ValueError("Non-JSON numeric constant")


def _assemble_calls(text: str) -> list[dict[str, Any]]:
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ReplayError("capture_too_large")
    calls: dict[int, dict[str, Any]] = {}
    finish = None
    done = False
    for count, event in enumerate(_events(text), 1):
        if count > 128:
            raise ReplayError("too_many_events")
        if done:
            raise ReplayError("data_after_done")
        if event == "[DONE]":
            done = True
            continue
        try:
            chunk = json.loads(event, parse_constant=_reject_constant)
        except (ValueError, RecursionError):
            raise ReplayError("invalid_event_json") from None
        if not isinstance(chunk, dict) or "error" in chunk or not isinstance(chunk.get("choices"), list):
            raise ReplayError("not_a_chat_chunk")
        for choice in chunk["choices"]:
            if not isinstance(choice, dict) or type(choice.get("index")) is not int or choice["index"] != 0:
                raise ReplayError("unsupported_choice")
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                raise ReplayError("invalid_delta")
            deltas = delta.get("tool_calls", [])
            if not isinstance(deltas, list):
                raise ReplayError("invalid_tool_deltas")
            if deltas and finish is not None:
                raise ReplayError("delta_after_finish")
            for item in deltas:
                if not isinstance(item, dict) or type(item.get("index")) is not int or not 0 <= item["index"] < 8:
                    raise ReplayError("invalid_call_index")
                call = calls.setdefault(item["index"], {"id": None, "name": None, "arguments": ""})
                function = item.get("function", {})
                if not isinstance(function, dict) or item.get("type", "function") != "function":
                    raise ReplayError("invalid_function_delta")
                for field, value in [("id", item.get("id")), ("name", function.get("name"))]:
                    if value is not None:
                        if not isinstance(value, str) or not value or (call[field] is not None and call[field] != value):
                            raise ReplayError("invalid_or_changed_identity")
                        call[field] = value
                fragment = function.get("arguments", "")
                if not isinstance(fragment, str):
                    raise ReplayError("invalid_argument_delta")
                call["arguments"] += fragment
                if len(call["arguments"]) > MAX_ARGUMENT_CHARS:
                    raise ReplayError("arguments_too_large")
            reason = choice.get("finish_reason")
            if reason is not None:
                if finish is not None:
                    raise ReplayError("multiple_finish_reasons")
                finish = reason
    if not done:
        raise ReplayError("missing_done_marker")
    if finish != "tool_calls":
        raise ReplayError("not_a_completed_tool_turn")
    if not calls or any(not call["id"] or not call["name"] for call in calls.values()):
        raise ReplayError("incomplete_call_identity")
    if len({call["id"] for call in calls.values()}) != len(calls):
        raise ReplayError("duplicate_call_identity")
    result = []
    for index in sorted(calls):
        call = calls[index]
        try:
            arguments = json.loads(call["arguments"], parse_constant=_reject_constant)
        except (ValueError, RecursionError):
            raise ReplayError("invalid_arguments_json") from None
        if not isinstance(arguments, dict):
            raise ReplayError("arguments_not_object")
        result.append({"id": call["id"], "name": call["name"], "arguments": arguments})
    return result


def inspect_stream(text: str) -> dict[str, Any]:
    try:
        calls = _assemble_calls(text)
        return {"accepted": True, "reason": "completed_tool_turn", "call_count": len(calls)}
    except ReplayError as exc:
        return {"accepted": False, "reason": str(exc), "call_count": 0}


def every_delta_identity_rule_rejects(text: str) -> bool:
    """Deliberately wrong consumer rule, used only as a labeled counterexample."""
    for event in _events(text):
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
            matched = _assemble_calls(case["sse"]) == case["expected_calls"]
        rows.append({"case": case["name"], **result, "matches_expected": matched})
    counterexample = every_delta_identity_rule_rejects(cases[0]["sse"])
    print(json.dumps({"synthetic": True, "network_requests": 0, "tools_executed": 0, "cases": rows,
                      "wrong_per_delta_identity_rule_rejects_valid_control": counterexample}, indent=2))
    return 0 if all(row["matches_expected"] for row in rows) and counterexample else 1


if __name__ == "__main__":
    raise SystemExit(main())

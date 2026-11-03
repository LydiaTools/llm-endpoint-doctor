"""Bounded inspection of OpenAI-style streamed tool-call responses."""
from __future__ import annotations

import json
from typing import Any

MAX_BYTES = 1_000_000
MAX_ARGUMENT_CHARS = 64_000


class ReplayError(ValueError):
    pass


def iter_sse_events(text: str):
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


def assemble_tool_calls(text: str) -> list[dict[str, Any]]:
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ReplayError("capture_too_large")
    calls: dict[int, dict[str, Any]] = {}
    finish = None
    done = False
    for count, event in enumerate(iter_sse_events(text), 1):
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
                for field, value in (("id", item.get("id")), ("name", function.get("name"))):
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


def inspect_tool_stream(
    text: str,
    *,
    expected_name: str | None = None,
    expected_arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        calls = assemble_tool_calls(text)
    except ReplayError as exc:
        return {"accepted": False, "reason": str(exc), "call_count": 0}
    if expected_name is not None and (
        len(calls) != 1
        or calls[0]["name"] != expected_name
        or calls[0]["arguments"] != expected_arguments
    ):
        return {"accepted": False, "reason": "unexpected_tool_call", "call_count": len(calls)}
    return {"accepted": True, "reason": "completed_tool_turn", "call_count": len(calls)}

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import socket
import struct
import time
import zlib
from dataclasses import asdict, dataclass
from http.client import HTTPResponse
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from . import __version__

_MAX_JSON_RESPONSE_BYTES = 1_000_000
_MAX_IMAGE_RESPONSE_BYTES = 32_000_000
_VISION_EXPECTED_COLORS = ("red", "green", "blue", "yellow")


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str
    elapsed_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DiagnosticReport:
    target: str
    model: str
    checks: list[CheckResult]
    image_model: str | None = None
    vision_model: str | None = None

    @property
    def chat_ready(self) -> bool:
        return any(c.name == "chat completion" and c.status == "pass" for c in self.checks)

    @property
    def image_generation_ready(self) -> bool | None:
        check = next((c for c in self.checks if c.name == "image generation"), None)
        if check is None or check.status == "warn":
            return None
        return check.status == "pass"

    @property
    def vision_input_ready(self) -> bool | None:
        check = next((c for c in self.checks if c.name == "vision input"), None)
        if check is None or check.status == "warn":
            return None
        return check.status == "pass"

    @property
    def tool_calling_ready(self) -> bool | None:
        check = next((c for c in self.checks if c.name == "tool calling"), None)
        if check is None or check.status == "warn":
            return None
        return check.status == "pass"

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": "llm-endpoint-doctor",
            "version": __version__,
            "target": self.target,
            "model": self.model,
            "chat_ready": self.chat_ready,
            "image_model": self.image_model,
            "image_generation_ready": self.image_generation_ready,
            "vision_model": self.vision_model,
            "vision_input_ready": self.vision_input_ready,
            "tool_calling_ready": self.tool_calling_ready,
            "checks": [c.to_dict() for c in self.checks],
        }


@dataclass
class _Response:
    status_code: int | None
    elapsed_ms: int
    payload: Any = None
    transport_error: str | None = None
    route_fingerprint: bytes | None = None


def _route_fingerprint(headers: Any) -> bytes | None:
    """Compare one bounded route label in memory; never place it in a report."""
    values = headers.get_all("X-Routed-Via", [])
    if len(values) != 1:
        return None
    value = values[0].strip()
    # FreeLLMAPI also emits cache/idempotency markers, which are not routes.
    if not 3 <= len(value) <= 256 or not value.isascii() or any(ord(c) < 33 or ord(c) > 126 or c == "," for c in value):
        return None
    provider, separator, model = value.partition("/")
    if not separator or not provider or not model:
        return None
    return hashlib.sha256(value.encode("ascii")).digest()


def _origin_key(parsed: Any) -> tuple[str, str, int]:
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return parsed.scheme, (parsed.hostname or "").lower(), port


class _SameOriginRedirectHandler(HTTPRedirectHandler):
    def __init__(self, origin: tuple[str, str, int]):
        super().__init__()
        self.origin = origin

    def redirect_request(
        self,
        req: Request,
        fp: HTTPResponse,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        target = urlsplit(urljoin(req.full_url, newurl))
        if _origin_key(target) != self.origin:
            return None
        if req.data is not None and code in {307, 308}:
            return Request(
                newurl,
                data=req.data,
                headers=dict(req.header_items()),
                origin_req_host=req.origin_req_host,
                unverifiable=True,
                method=req.get_method(),
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _is_loopback(hostname: str) -> bool:
    if hostname.lower() == "localhost" or hostname.lower().endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def normalize_base_url(raw: str) -> str:
    value = raw.strip()
    if not value.isprintable() or any(char.isspace() for char in value):
        raise ValueError("The base URL cannot contain whitespace or control characters.")
    parsed = urlsplit(value)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("Enter a complete HTTPS endpoint URL, such as https://api.example.com/v1.")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("The endpoint URL contains an invalid port.") from exc
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Do not put credentials in the URL; enter the key at the hidden prompt.")
    if parsed.query or parsed.fragment:
        raise ValueError("Query strings and fragments are not accepted in the base URL.")
    if parsed.scheme == "http" and not _is_loopback(parsed.hostname):
        raise ValueError("Use HTTPS for remote endpoints. HTTP is allowed only for localhost tests.")
    path = parsed.path.rstrip("/")
    if path.endswith(("/chat/completions", "/models")):
        raise ValueError("Enter the API base URL (for example, /v1), not a complete endpoint path.")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _safe_target(base_url: str) -> str:
    parsed = urlsplit(base_url)
    host = parsed.hostname or "unknown-host"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return f"{parsed.scheme}://{host}"


def _error_detail(status_code: int | None, transport_error: str | None) -> str:
    if status_code == 401 or status_code == 403:
        return f"Authentication or access was rejected ({status_code}); check the key and provider access."
    if status_code == 404:
        return "Endpoint or model was not found (404); check the base URL and exact model ID."
    if status_code == 429:
        return "Provider rate limit returned 429; the request did not complete."
    if status_code is not None and status_code >= 500:
        return f"Provider returned server error {status_code}."
    if transport_error == "response-too-large" and (status_code is None or 200 <= status_code < 300):
        return "Response exceeded the local safety limit; the response body was omitted."
    if status_code is not None:
        return f"Provider returned HTTP {status_code}. Response bodies are omitted from reports."
    if transport_error == "timeout":
        return "Connection timed out."
    if transport_error == "tls":
        return "TLS verification failed. The tool does not disable certificate checks."
    return "Could not reach the endpoint. Check the host, network, and provider status."


def _request_json(
    base_url: str,
    path: str,
    api_key: str,
    body: dict[str, Any] | None,
    timeout: float,
    max_response_bytes: int = _MAX_JSON_RESPONSE_BYTES,
    capture_route: bool = False,
) -> _Response:
    parsed = urlsplit(base_url)
    origin = _origin_key(parsed)
    opener = build_opener(_SameOriginRedirectHandler(origin))
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = Request(f"{base_url}{path}", data=data, headers=headers)
    start = time.monotonic()
    route_fingerprint = None
    try:
        with opener.open(request, timeout=timeout) as response:
            if capture_route and 200 <= response.status < 300:
                route_fingerprint = _route_fingerprint(response.headers)
            raw = response.read(max_response_bytes + 1)
            status_code = response.status
    except HTTPError as exc:
        raw = exc.read(max_response_bytes + 1)
        status_code = exc.code
    except (TimeoutError, socket.timeout):
        return _Response(None, round((time.monotonic() - start) * 1000), transport_error="timeout")
    except URLError as exc:
        reason = exc.reason
        if isinstance(reason, (TimeoutError, socket.timeout)):
            category = "timeout"
        elif isinstance(reason, ssl_error_types()):
            category = "tls"
        else:
            category = "network"
        return _Response(None, round((time.monotonic() - start) * 1000), transport_error=category)
    except OSError:
        return _Response(None, round((time.monotonic() - start) * 1000), transport_error="network")
    elapsed = round((time.monotonic() - start) * 1000)
    if len(raw) > max_response_bytes:
        return _Response(status_code, elapsed, transport_error="response-too-large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = None
    return _Response(status_code, elapsed, payload=payload, route_fingerprint=route_fingerprint)


def ssl_error_types() -> tuple[type[BaseException], ...]:
    import ssl

    return (ssl.SSLError,)


def _message_content(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    return content if isinstance(content, str) else None


def _has_image_result(payload: Any) -> bool:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return False
    for item in payload["data"]:
        if not isinstance(item, dict):
            continue
        encoded = item.get("b64_json")
        if isinstance(encoded, str) and encoded.strip():
            return True
        url = item.get("url")
        if isinstance(url, str):
            try:
                parsed = urlsplit(url)
                port = parsed.port
            except ValueError:
                continue
            if (
                parsed.scheme in {"http", "https"}
                and parsed.hostname
                and parsed.username is None
                and parsed.password is None
                and (port is None or 1 <= port <= 65535)
            ):
                return True
    return False


def _vision_probe_data_url() -> str:
    """Build a small deterministic 2x2 PNG using only the standard library."""
    width = height = 64
    rgb = {
        "red": (255, 0, 0),
        "green": (0, 255, 0),
        "blue": (0, 0, 255),
        "yellow": (255, 255, 0),
    }
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # PNG filter: None
        for x in range(width):
            if y < height // 2:
                color = "red" if x < width // 2 else "green"
            else:
                color = "yellow" if x < width // 2 else "blue"
            rows.extend(rgb[color])

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(rows)))
    png += chunk(b"IEND", b"")
    encoded = base64.b64encode(png).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _matches_vision_probe(content: str) -> bool:
    colors = tuple(color.strip() for color in content.lower().strip().split(","))
    return tuple(colors) == _VISION_EXPECTED_COLORS


_OUTPUT_LIMIT_DETAIL = (
    "HTTP succeeded, but finish_reason=length reports a truncated generation. "
    "Check the output budget (reasoning can consume it); choose --max-output-tokens explicitly. "
    "No automatic retry was made."
)


def _output_limited(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    choices = payload.get("choices")
    return (
        isinstance(choices, list)
        and bool(choices)
        and isinstance(choices[0], dict)
        and choices[0].get("finish_reason") == "length"
    )


def _chat_body(
    model: str, *, stream: bool = False, json_mode: bool = False,
    max_output_tokens: int | None = None, token_field: str = "max_tokens",
) -> dict[str, Any]:
    prompt = 'Reply with exactly the JSON object {"status":"ok"}.' if json_mode else "Reply with exactly: endpoint-ok"
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        token_field: max_output_tokens if max_output_tokens is not None else (24 if json_mode else 12),
        "stream": stream,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    return body


def _tool_call_body(
    model: str, *, max_output_tokens: int | None = None, token_field: str = "max_tokens"
) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [{"role": "user", "content": "Call endpoint_doctor_probe now."}],
        token_field: max_output_tokens if max_output_tokens is not None else 128,
        "stream": False,
        "tools": [{
            "type": "function",
            "function": {
                "name": "endpoint_doctor_probe",
                "description": "Return the requested status value.",
                "parameters": {
                    "type": "object",
                    "properties": {"status": {"type": "string", "enum": ["ok"]}},
                    "required": ["status"],
                    "additionalProperties": False,
                },
            },
        }],
        "tool_choice": {
            "type": "function",
            "function": {"name": "endpoint_doctor_probe"},
        },
    }


def _tool_call_is_valid(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return False
    choice = choices[0]
    if choice.get("finish_reason") != "tool_calls":
        return False
    message = choice.get("message")
    if not isinstance(message, dict):
        return False
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list) or len(tool_calls) != 1 or not isinstance(tool_calls[0], dict):
        return False
    function = tool_calls[0].get("function")
    if not isinstance(function, dict) or function.get("name") != "endpoint_doctor_probe":
        return False
    arguments = function.get("arguments")
    if not isinstance(arguments, str):
        return False
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return False
    return parsed == {"status": "ok"}


def _request_stream(
    base_url: str, api_key: str, body: dict[str, Any], timeout: float
) -> _Response:
    parsed = urlsplit(base_url)
    origin = _origin_key(parsed)
    opener = build_opener(_SameOriginRedirectHandler(origin))
    headers = {"Accept": "text/event-stream", "Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = Request(
        f"{base_url}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
    )
    start = time.monotonic()
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(1_000_001)
            status_code = response.status
    except HTTPError as exc:
        raw = exc.read(1_000_001)
        status_code = exc.code
    except (TimeoutError, socket.timeout):
        return _Response(None, round((time.monotonic() - start) * 1000), transport_error="timeout")
    except URLError as exc:
        reason = exc.reason
        category = "timeout" if isinstance(reason, (TimeoutError, socket.timeout)) else "network"
        if isinstance(reason, ssl_error_types()):
            category = "tls"
        return _Response(None, round((time.monotonic() - start) * 1000), transport_error=category)
    except OSError:
        return _Response(None, round((time.monotonic() - start) * 1000), transport_error="network")
    elapsed = round((time.monotonic() - start) * 1000)
    if len(raw) > 1_000_000:
        return _Response(status_code, elapsed, transport_error="response-too-large")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = ""
    saw_done = False
    saw_usage = False
    saw_length_limit = False
    saw_stream_error = False
    for line in text.splitlines():
        line = line.strip()
        if line == "data: [DONE]":
            saw_done = True
            continue
        if not line.startswith("data:"):
            continue
        try:
            chunk = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            continue
        if isinstance(chunk, dict) and isinstance(chunk.get("usage"), dict):
            saw_usage = True
        if isinstance(chunk, dict):
            choices = chunk.get("choices")
            if chunk.get("error") is not None or (
                isinstance(choices, list)
                and any(isinstance(choice, dict) and choice.get("finish_reason") == "error" for choice in choices)
            ) or (
                (choices is None or choices == [])
                and isinstance(chunk.get("error_type"), str)
                and bool(chunk["error_type"].strip())
                and isinstance(chunk.get("error_message"), str)
                and bool(chunk["error_message"].strip())
            ):
                saw_stream_error = True
        if _output_limited(chunk):
            saw_length_limit = True
    return _Response(status_code, elapsed, payload={
        "saw_done": saw_done, "saw_usage": saw_usage, "saw_length_limit": saw_length_limit,
        "saw_stream_error": saw_stream_error,
    })


def run_diagnostics(
    base_url: str,
    model: str,
    api_key: str = "",
    timeout: float = 20.0,
    check_model_list: bool = False,
    check_streaming: bool = False,
    check_json_mode: bool = False,
    check_stream_options: bool = False,
    max_output_tokens: int | None = None,
    token_field: str = "max_tokens",
    check_image_generation: bool = False,
    image_model: str | None = None,
    check_vision_input: bool = False,
    vision_model: str | None = None,
    check_tool_calling: bool = False,
    compare_route_headers: bool = False,
) -> DiagnosticReport:
    normalized = normalize_base_url(base_url)
    model = model.strip()
    if not model:
        raise ValueError("Model ID is required.")
    if len(model) > 256 or not model.isprintable():
        raise ValueError("Model ID must be printable and no longer than 256 characters.")
    if any(ord(char) < 32 or ord(char) == 127 for char in api_key):
        raise ValueError("API key contains control characters.")
    if not 1 <= timeout <= 120:
        raise ValueError("Timeout must be between 1 and 120 seconds.")
    if max_output_tokens is not None and (type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 8192):
        raise ValueError("Output token limit must be an integer between 1 and 8192.")
    if token_field not in ("max_tokens", "max_completion_tokens"):
        raise ValueError("Token field must be max_tokens or max_completion_tokens.")
    if compare_route_headers and not check_tool_calling:
        raise ValueError("Route comparison requires the tool-calling check.")
    if check_image_generation:
        if image_model is None or not image_model.strip():
            raise ValueError("An image model ID is required for the image-generation check.")
        image_model = image_model.strip()
        if len(image_model) > 256 or not image_model.isprintable():
            raise ValueError("Image model ID must be printable and no longer than 256 characters.")
    elif image_model is not None:
        raise ValueError("Image model ID can be used only with the image-generation check.")
    if vision_model is not None:
        vision_model = vision_model.strip()
        if not vision_model:
            raise ValueError("Vision model ID cannot be empty.")
        if len(vision_model) > 256 or not vision_model.isprintable():
            raise ValueError("Vision model ID must be printable and no longer than 256 characters.")
        if not check_vision_input:
            raise ValueError("Vision model ID can be used only with the vision-input check.")
    elif check_vision_input:
        vision_model = model
    body_options = {"max_output_tokens": max_output_tokens, "token_field": token_field}
    checks: list[CheckResult] = []

    if check_model_list:
        result = _request_json(normalized, "/models", api_key, None, timeout)
        if result.status_code is not None and 200 <= result.status_code < 300:
            data = result.payload.get("data") if isinstance(result.payload, dict) else None
            available = [item.get("id") for item in data if isinstance(item, dict)] if isinstance(data, list) else []
            if model in available:
                checks.append(CheckResult("model catalog", "pass", "Exact model ID appears in the provider catalog.", result.elapsed_ms))
            elif isinstance(data, list):
                checks.append(CheckResult("model catalog", "warn", "Catalog responded, but this exact ID was not listed; some providers use aliases.", result.elapsed_ms))
            else:
                checks.append(CheckResult("model catalog", "warn", "Catalog response was not in the expected format.", result.elapsed_ms))
        else:
            checks.append(CheckResult("model catalog", "warn", _error_detail(result.status_code, result.transport_error), result.elapsed_ms))

    result = _request_json(
        normalized,
        "/chat/completions",
        api_key,
        _chat_body(model, **body_options),
        timeout,
        capture_route=compare_route_headers,
    )
    chat_route_fingerprint = result.route_fingerprint
    content = _message_content(result.payload)
    if result.status_code is not None and 200 <= result.status_code < 300 and _output_limited(result.payload):
        checks.append(CheckResult("chat completion", "fail", _OUTPUT_LIMIT_DETAIL, result.elapsed_ms))
    elif result.status_code is not None and 200 <= result.status_code < 300 and content:
        checks.append(CheckResult("chat completion", "pass", "A non-empty chat response was returned.", result.elapsed_ms))
    elif result.status_code is not None and 200 <= result.status_code < 300:
        checks.append(CheckResult("chat completion", "fail", "HTTP succeeded, but no chat text was found in the expected response shape.", result.elapsed_ms))
    else:
        checks.append(CheckResult("chat completion", "fail", _error_detail(result.status_code, result.transport_error), result.elapsed_ms))

    if check_streaming:
        result = _request_stream(normalized, api_key, _chat_body(model, stream=True, **body_options), timeout)
        if result.status_code is not None and 200 <= result.status_code < 300 and isinstance(result.payload, dict) and result.payload.get("saw_stream_error"):
            checks.append(CheckResult("streaming", "warn", "The stream reported an error; the response body was omitted.", result.elapsed_ms))
        elif result.status_code is not None and 200 <= result.status_code < 300 and isinstance(result.payload, dict) and result.payload.get("saw_length_limit"):
            checks.append(CheckResult("streaming", "warn", _OUTPUT_LIMIT_DETAIL, result.elapsed_ms))
        elif result.status_code is not None and 200 <= result.status_code < 300 and isinstance(result.payload, dict) and result.payload.get("saw_done"):
            checks.append(CheckResult("streaming", "pass", "Server sent an SSE completion marker.", result.elapsed_ms))
        elif result.status_code is not None and 200 <= result.status_code < 300:
            checks.append(CheckResult("streaming", "warn", "HTTP succeeded, but no SSE completion marker was detected.", result.elapsed_ms))
        else:
            checks.append(CheckResult("streaming", "warn", _error_detail(result.status_code, result.transport_error), result.elapsed_ms))

    if check_stream_options:
        body = _chat_body(model, stream=True, **body_options)
        body["stream_options"] = {"include_usage": True}
        result = _request_stream(normalized, api_key, body, timeout)
        complete = result.status_code is not None and 200 <= result.status_code < 300 and isinstance(result.payload, dict) and result.payload.get("saw_done")
        if result.status_code is not None and 200 <= result.status_code < 300 and isinstance(result.payload, dict) and result.payload.get("saw_stream_error"):
            checks.append(CheckResult("stream options", "warn", "The stream reported an error; the response body was omitted.", result.elapsed_ms))
        elif result.status_code is not None and 200 <= result.status_code < 300 and isinstance(result.payload, dict) and result.payload.get("saw_length_limit"):
            checks.append(CheckResult("stream options", "warn", _OUTPUT_LIMIT_DETAIL, result.elapsed_ms))
        elif complete and result.payload.get("saw_usage"):
            checks.append(CheckResult("stream options", "pass", "SSE completed and included usage metadata when requested.", result.elapsed_ms))
        elif complete:
            checks.append(CheckResult("stream options", "warn", "SSE completed, but no usage metadata was returned despite include_usage.", result.elapsed_ms))
        elif result.status_code is not None and 200 <= result.status_code < 300:
            checks.append(CheckResult("stream options", "warn", "HTTP succeeded, but no SSE completion marker was detected.", result.elapsed_ms))
        else:
            checks.append(CheckResult("stream options", "warn", f"The include_usage streaming request did not complete. {_error_detail(result.status_code, result.transport_error)}", result.elapsed_ms))

    if check_json_mode:
        result = _request_json(
            normalized,
            "/chat/completions",
            api_key,
            _chat_body(model, json_mode=True, **body_options),
            timeout,
        )
        content = _message_content(result.payload)
        if result.status_code is not None and 200 <= result.status_code < 300 and _output_limited(result.payload):
            checks.append(CheckResult("JSON mode", "warn", _OUTPUT_LIMIT_DETAIL, result.elapsed_ms))
        elif result.status_code is not None and 200 <= result.status_code < 300 and content:
            try:
                parsed_content = json.loads(content)
                valid = isinstance(parsed_content, dict)
            except json.JSONDecodeError:
                valid = False
            checks.append(CheckResult("JSON mode", "pass" if valid else "warn", "Returned a JSON object." if valid else "Request succeeded, but the response content was not a JSON object.", result.elapsed_ms))
        else:
            checks.append(CheckResult("JSON mode", "warn", _error_detail(result.status_code, result.transport_error), result.elapsed_ms))

    if check_image_generation:
        result = _request_json(
            normalized,
            "/images/generations",
            api_key,
            {
                "model": image_model,
                "prompt": "A simple blue circle centered on a plain white background.",
                "n": 1,
            },
            timeout,
            max_response_bytes=_MAX_IMAGE_RESPONSE_BYTES,
        )
        if result.transport_error == "response-too-large" and result.status_code is not None and 200 <= result.status_code < 300:
            checks.append(CheckResult(
                "image generation",
                "warn",
                "HTTP succeeded, but the response exceeded the local safety limit; image compatibility was not confirmed.",
                result.elapsed_ms,
            ))
        elif result.status_code is not None and 200 <= result.status_code < 300 and _has_image_result(result.payload):
            checks.append(CheckResult(
                "image generation",
                "pass",
                "Returned an image result in base64 or HTTP(S) URL form; the media was not downloaded or saved.",
                result.elapsed_ms,
            ))
        elif result.status_code is not None and 200 <= result.status_code < 300:
            checks.append(CheckResult(
                "image generation",
                "fail",
                "HTTP succeeded, but no non-empty data[].b64_json or HTTP(S) data[].url result was found.",
                result.elapsed_ms,
            ))
        else:
            checks.append(CheckResult("image generation", "fail", _error_detail(result.status_code, result.transport_error), result.elapsed_ms))

    if check_vision_input:
        vision_model = vision_model or model
        vision_body = _chat_body(
            vision_model,
            max_output_tokens=max_output_tokens if max_output_tokens is not None else 24,
            token_field=token_field,
        )
        vision_body["messages"] = [{
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "Read the four solid-color quadrants clockwise, starting at top-left. Reply with exactly four lowercase color names separated by commas.",
                },
                {
                    "type": "image_url",
                    "image_url": {"url": _vision_probe_data_url(), "detail": "low"},
                },
            ],
        }]
        result = _request_json(normalized, "/chat/completions", api_key, vision_body, timeout)
        content = _message_content(result.payload)
        if result.transport_error == "response-too-large" and result.status_code is not None and 200 <= result.status_code < 300:
            checks.append(CheckResult(
                "vision input",
                "warn",
                "HTTP succeeded, but the response exceeded the local safety limit; visual interpretation was not confirmed.",
                result.elapsed_ms,
            ))
        elif result.status_code is not None and 200 <= result.status_code < 300 and _output_limited(result.payload):
            checks.append(CheckResult("vision input", "warn", _OUTPUT_LIMIT_DETAIL, result.elapsed_ms))
        elif result.status_code is not None and 200 <= result.status_code < 300 and content:
            matches = _matches_vision_probe(content)
            checks.append(CheckResult(
                "vision input",
                "pass" if matches else "warn",
                "The response matched the built-in color sequence." if matches else "A response arrived, but it did not match the built-in color sequence; visual interpretation was not confirmed.",
                result.elapsed_ms,
            ))
        elif result.status_code is not None and 200 <= result.status_code < 300:
            checks.append(CheckResult("vision input", "fail", "HTTP succeeded, but no chat text was found in the expected response shape.", result.elapsed_ms))
        else:
            checks.append(CheckResult("vision input", "fail", _error_detail(result.status_code, result.transport_error), result.elapsed_ms))

    if check_tool_calling:
        result = _request_json(
            normalized,
            "/chat/completions",
            api_key,
            _tool_call_body(model, max_output_tokens=max_output_tokens, token_field=token_field),
            timeout,
            capture_route=compare_route_headers,
        )
        tool_route_fingerprint = result.route_fingerprint
        if result.status_code is not None and 200 <= result.status_code < 300 and _output_limited(result.payload):
            checks.append(CheckResult(
                "tool calling",
                "warn",
                "The response hit the output limit before tool-call compatibility could be confirmed.",
                result.elapsed_ms,
            ))
        elif result.status_code is not None and 200 <= result.status_code < 300 and _tool_call_is_valid(result.payload):
            checks.append(CheckResult(
                "tool calling",
                "pass",
                "Returned one valid function call with the expected argument shape. The function was not executed.",
                result.elapsed_ms,
            ))
        elif result.status_code is not None and 200 <= result.status_code < 300:
            checks.append(CheckResult(
                "tool calling",
                "warn",
                "HTTP succeeded, but a valid function call with the expected name and arguments was not confirmed.",
                result.elapsed_ms,
            ))
        else:
            checks.append(CheckResult("tool calling", "warn", _error_detail(result.status_code, result.transport_error), result.elapsed_ms))

        if compare_route_headers:
            if chat_route_fingerprint is None or tool_route_fingerprint is None:
                detail = "Comparison unavailable: both successful responses need one usable X-Routed-Via route label."
            elif chat_route_fingerprint == tool_route_fingerprint:
                detail = "The two X-Routed-Via header values matched; this alone does not prove identical routing."
            else:
                detail = "The two X-Routed-Via header values differed; check the router for details."
            checks.append(CheckResult("route header comparison", "info", detail))

    return DiagnosticReport(
        _safe_target(normalized), model, checks,
        image_model if check_image_generation else None,
        vision_model if check_vision_input else None,
    )

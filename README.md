# LLM Endpoint Doctor

**Check chat, tool calling, vision input, and image generation before you wire an OpenAI-compatible API into your app.**

One local command checks your base URL, model ID, authentication, and first chat response. Add optional checks for the model catalog, streaming, JSON mode, tool calling, vision input, or a separate image-generation model only when you need them.

[简体中文](README.zh-CN.md) · [Report a bug](https://github.com/LydiaTools/llm-endpoint-doctor/issues)

## Why this exists

“OpenAI-compatible” can still mean a wrong base URL, a retired model ID, a provider-specific auth rule, or an endpoint that accepts chat but not streaming. LLM Endpoint Doctor gives you a small, repeatable check before those details become a debugging session inside your app.

It complements provider directories: directories help you find an endpoint; this tool checks the endpoint you plan to use.

**Connecting a free-tier model?** Start with [three provider recipes: Groq, Gemini and OpenRouter](docs/free-api-setup.md). Each includes the correct base URL, where to choose the model, and one copyable check. [中文接入指南](docs/free-api-setup.zh-CN.md).

**Starting an agent loop?** [Check the billing project and count requests first](docs/api-cost-preflight.md): Gemini's current spend-cap path, its reporting delay, and keyless request counts for the default and optional checks.

## Quick start

Requires Python 3.9 or later. No third-party runtime dependencies.

Prefer a prebuilt package? Download the [v0.6.0 alpha wheel](https://github.com/LydiaTools/llm-endpoint-doctor/releases/download/v0.6.0/llm_endpoint_doctor-0.6.0-py3-none-any.whl) and verify it with the accompanying [SHA-256 file](https://github.com/LydiaTools/llm-endpoint-doctor/releases/download/v0.6.0/llm_endpoint_doctor-0.6.0-py3-none-any.whl.sha256). This is an alpha preview.

```bash
git clone https://github.com/LydiaTools/llm-endpoint-doctor.git
cd llm-endpoint-doctor
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
llm-doctor --base-url https://api.example.com/v1 --model your-model-id
```

The API key is requested through a hidden terminal prompt. Press Enter for a keyless endpoint. The default run sends **one** chat-completion request.

The default output cap is deliberately small: 12 tokens for ordinary chat, 24 for JSON or vision, and 128 for tool calling. For a reasoning model, select a budget explicitly, for example `--max-output-tokens 1024`. If your provider requires the newer field, also use `--token-field max_completion_tokens`. Both apply to every selected generation check; neither adds a retry or a request. A response ending with `finish_reason=length` is reported as truncated, even if it includes text, valid JSON, usage or an SSE `[DONE]` marker. [Keyless, reproducible example](docs/free-api-setup.md#reproduce-the-output-budget-case-without-a-key).

```text
LLM Endpoint Doctor
Target host: https://api.example.com
Model: your-model-id

[PASS] chat completion — A non-empty chat response was returned.

Basic chat: READY
No API key, prompt, or response content is included in the report.
```

## Try it without a provider key

You can see a complete local pass without signing up for a provider or sending a request over the internet. In a second terminal, start the deterministic mock endpoint:

```bash
python3 examples/mock_openai_server.py
```

Then run the doctor against it:

```bash
llm-doctor \
  --base-url http://127.0.0.1:8765/v1 \
  --model demo-model \
  --no-prompt \
  --check-model-list \
  --check-streaming \
  --check-stream-options \
  --check-json-mode \
  --check-image-generation \
  --image-model demo-image-model \
  --check-vision-input \
  --check-tool-calling \
  --vision-model demo-vision-model
```

```text
[PASS] model catalog — Exact model ID appears in the provider catalog.
[PASS] chat completion — A non-empty chat response was returned.
[PASS] streaming — Server sent an SSE completion marker.
[PASS] stream options — SSE completed and included usage metadata when requested.
[PASS] JSON mode — Returned a JSON object.
[PASS] image generation — Returned an image result in base64 or HTTP(S) URL form; the media was not downloaded or saved.
[PASS] vision input — The response matched the built-in color sequence.
[PASS] tool calling — Returned one valid function call with the expected argument shape. The function was not executed.

Basic chat: READY
```

The fixture binds to loopback and returns fixed responses. Its image route returns a synthetic placeholder, not real image bytes; it demonstrates the request and report flow without testing an external provider. Stop it with `Ctrl+C`.

## Chat works, but streamed tool calls fail?

Replay a small protocol example before changing models. In a current source checkout, this command runs **six original offline controls**, with no key, server, model or package installation:

```bash
python3 examples/replay_tool_stream.py
```

It accepts call metadata sent only in the first delta and keeps interleaved arguments with the right call. It rejects missing complete-call identity, unfinished JSON and a turn ended by the output limit—even when that turn's argument JSON is valid. A labeled counterexample shows why requiring the name and ID in every delta rejects a valid stream.

You can also inspect one local capture with `--file capture.sse`; output contains only acceptance, a fixed reason and the call count. No tool is executed. [Actual controls, output and bilingual troubleshooting guide](docs/tool-stream-replay.md). The optional `--check-tool-calling` probe below separately checks a live endpoint's returned function-call shape; it does not execute that function or test streamed tool-call handling.

## Optional checks

Each selected check adds one request. Streaming, stream-options, JSON mode, tool calling, image generation, and vision input each send one additional generation request; the model catalog check adds one GET.

```bash
llm-doctor \
  --base-url https://api.example.com/v1 \
  --model your-model-id \
  --check-model-list \
  --check-streaming \
  --check-stream-options \
  --check-json-mode \
  --check-tool-calling \
  --json-report endpoint-report.json
```

The JSON report is created only if the destination file does not already exist. It includes the target host, model ID, check results, and timings. It excludes the API key, URL path, prompts, and response content.

To check a separate image model, add one explicit image-generation request:

```bash
llm-doctor \
  --base-url https://api.example.com/v1 \
  --model your-chat-model \
  --check-image-generation \
  --image-model your-image-model
```

This sends one fixed prompt (`A simple blue circle centered on a plain white background.`) to `POST /images/generations` with `n=1`. It uses the same `--base-url` and API key as the chat check, but the image model may differ. Image generation can incur provider charges. The tool checks for a non-empty base64 image result or an HTTP(S) result URL; it does not download or save the image. The response is held only long enough to inspect it, up to a 32 MB safety limit. [OpenAI image-generation API reference](https://developers.openai.com/api/reference/resources/images/methods/generate).

## Check vision input

Run `llm-doctor --check-vision-input` to send one extra `POST /chat/completions` containing a small built-in synthetic PNG as a Base64 data URL. The image has four colored quadrants. By default, the check uses the chat model; `--vision-model` selects a different one.

A pass means the response matched the expected clockwise color sequence. A response that does not match is reported as unconfirmed. This is a smoke test, not an accuracy benchmark or proof of vision quality. The provider receives the image request and may charge for it. The PNG is generated in memory, not fetched from a URL, and not written to disk. [OpenAI vision input format](https://developers.openai.com/api/docs/guides/images-vision).

## Check tool calling

Run `llm-doctor --check-tool-calling` to send one extra `POST /chat/completions` with a single required function definition. The check passes only when the endpoint returns one `tool_calls` result with the expected function name and valid JSON arguments. The function is never executed. This confirms one response shape for the selected model; it is not a semantic tool-use benchmark or a guarantee that every schema, prompt, stream consumer, or future request will work. The request can use provider quota or incur a charge.

## Non-interactive and CI use

For a keyless endpoint, skip the prompt explicitly:

```bash
llm-doctor --base-url http://localhost:8000/v1 --model local-model --no-prompt
```

For CI, read the key from an environment variable so it never appears in command arguments:

```yaml
- name: Check LLM endpoint
  env:
    LLM_API_KEY: ${{ secrets.LLM_API_KEY }}
  run: >-
    llm-doctor --base-url https://api.example.com/v1
    --model your-model-id --api-key-env LLM_API_KEY
```

If the named environment variable is missing or empty, the command stops before sending a request.

## What it checks

- **Chat completion:** Sends one short test prompt to `/chat/completions` and verifies the response has text in the expected OpenAI chat format.
- **Model catalog (optional):** Checks whether the exact model ID appears in `/models`. Some providers use aliases or incomplete catalogs, so a missing ID is a warning rather than a failed chat test.
- **Streaming (optional):** Checks for a server-sent events completion marker.
- **Stream options (optional):** Sends `stream_options: {"include_usage": true}` with a streaming request and checks whether usage metadata arrives. This can reveal endpoints that accept ordinary streaming but reject or ignore this client option.
- **JSON mode (optional):** Requests a JSON object and checks that the returned content parses as an object.
- **Image generation (optional):** With `--check-image-generation --image-model ID`, sends one additional fixed-prompt request to `/images/generations` and checks for a non-empty `data[].b64_json` or HTTP(S) `data[].url` result. It does not fetch URL results, decode or save image bytes, or judge image quality.
- **Vision input (optional):** With `--check-vision-input`, sends one additional chat request with a built-in synthetic PNG data URL. It checks the response against four expected color quadrants; a mismatch stays unconfirmed. This is not a vision-quality benchmark.
- **Tool calling (optional):** With `--check-tool-calling`, sends one additional chat request with a fixed function schema. It checks for one function call with the expected name and JSON arguments; it never executes the function and does not grade semantic reliability.
- **Common failures:** Separates authentication/access errors, missing endpoints or models, rate limits, server errors, timeouts, and TLS failures without printing provider error bodies.

The tool makes no automatic retries. It does not grade answer quality, benchmark providers, guarantee compatibility with every client, or determine whether a provider's free tier will remain available.

## Privacy and request handling

- The API key is entered with a hidden prompt or read from a named environment variable, used in the authorization header for the endpoint you supplied, and never written to a file by the tool.
- The tool has no telemetry or account system.
- Reports omit the API key, URL path, prompt, and response text.
- Remote endpoints must use HTTPS. Plain HTTP is allowed only for localhost tests.
- Query strings, URL-embedded credentials, and cross-origin redirects are rejected so a key is not forwarded to another host.
- The endpoint provider receives the test request and may count it against its own limits. Optional checks send extra requests; use them only when needed.
- The image check sends its fixed prompt to the endpoint and may incur image-generation charges. It requests one image and does not save the returned image; oversized responses are not parsed.
- The vision check sends one built-in synthetic image to the endpoint and may incur provider charges. The image is generated in memory and is not saved. It makes no repeated or follow-up requests.
- The tool-calling check sends one fixed function schema and prompt to the endpoint. It does not execute the returned function or save the response, and it makes no repeated or follow-up request.

## Development

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

All tests use local fixture servers. They do not call an external provider or use a real API key.

## License

MIT. See [LICENSE](LICENSE).

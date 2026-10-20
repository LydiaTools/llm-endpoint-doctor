# LLM Endpoint Doctor

**Find out whether an OpenAI-compatible endpoint works before you wire it into your app.**

One local command checks your base URL, model ID, authentication, and first chat response. Add optional checks for the model catalog, streaming, and JSON mode only when you need them.

[简体中文](README.zh-CN.md) · [Report a bug](https://github.com/LydiaTools/llm-endpoint-doctor/issues)

## Why this exists

“OpenAI-compatible” can still mean a wrong base URL, a retired model ID, a provider-specific auth rule, or an endpoint that accepts chat but not streaming. LLM Endpoint Doctor gives you a small, repeatable check before those details become a debugging session inside your app.

It complements provider directories: directories help you find an endpoint; this tool checks the endpoint you plan to use.

## Quick start

Requires Python 3.9 or later. No third-party runtime dependencies.

```bash
git clone https://github.com/LydiaTools/llm-endpoint-doctor.git
cd llm-endpoint-doctor
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
llm-doctor --base-url https://api.example.com/v1 --model your-model-id
```

The API key is requested through a hidden terminal prompt. Press Enter for a keyless endpoint. The default run sends **one** chat-completion request.

```text
LLM Endpoint Doctor
Target host: https://api.example.com
Model: your-model-id

[PASS] chat completion — A non-empty chat response was returned.

Basic chat: READY
No API key, prompt, or response content is included in the report.
```

## Optional checks

Each option adds a request. Streaming and JSON-mode checks each send one additional generation request.

```bash
llm-doctor \
  --base-url https://api.example.com/v1 \
  --model your-model-id \
  --check-model-list \
  --check-streaming \
  --check-json-mode \
  --json-report endpoint-report.json
```

The JSON report is created only if the destination file does not already exist. It includes the target host, model ID, check results, and timings. It excludes the API key, URL path, prompts, and response content.

## What it checks

- **Chat completion:** Sends one short test prompt to `/chat/completions` and verifies the response has text in the expected OpenAI chat format.
- **Model catalog (optional):** Checks whether the exact model ID appears in `/models`. Some providers use aliases or incomplete catalogs, so a missing ID is a warning rather than a failed chat test.
- **Streaming (optional):** Checks for a server-sent events completion marker.
- **JSON mode (optional):** Requests a JSON object and checks that the returned content parses as an object.
- **Common failures:** Separates authentication/access errors, missing endpoints or models, rate limits, server errors, timeouts, and TLS failures without printing provider error bodies.

The tool makes no automatic retries. It does not grade answer quality, benchmark providers, guarantee compatibility with every client, or determine whether a provider's free tier will remain available.

## Privacy and request handling

- The API key is entered with a hidden prompt, used in the authorization header for the endpoint you supplied, and never written to a file by the tool.
- The tool has no telemetry or account system.
- Reports omit the API key, URL path, prompt, and response text.
- Remote endpoints must use HTTPS. Plain HTTP is allowed only for localhost tests.
- Query strings, URL-embedded credentials, and cross-origin redirects are rejected so a key is not forwarded to another host.
- The endpoint provider receives the test request and may count it against its own limits. Optional checks send extra requests; use them only when needed.

## Development

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

All tests use local fixture servers. They do not call an external provider or use a real API key.

## License

MIT. See [LICENSE](LICENSE).

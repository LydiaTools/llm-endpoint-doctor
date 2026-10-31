# Chat works. Will tool calls work too?

A model router can accept a chat request while a tool-calling request fails or returns a different response shape. A [FreeLLMAPI feature request](https://github.com/tashfeenahmed/freellmapi/issues/1438) discusses this distinction and proposes per-model capability labels. Here is a small manual check you can run before wiring a compatible endpoint into an agent.

[Project](../README.md) · [中文](chat-vs-tools.zh-CN.md)

## Try a keyless example

First [install the current source](../README.md#quick-start). The following fixture is in the current source tree. In terminal A, from the repository:

```sh
python3 examples/mock_openai_server.py --reject-tools
```

In terminal B, using the environment where `llm-doctor` is installed:

```sh
llm-doctor --base-url http://127.0.0.1:8765/v1 --model demo-model --no-prompt --check-tool-calling
```

The synthetic endpoint accepts ordinary chat and deliberately returns HTTP 400 for a request containing tools. The report shows:

```text
[PASS] chat completion — A non-empty chat response was returned.
[WARN] tool calling — Provider returned HTTP 400. Response bodies are omitted from reports.

Basic chat: READY
```

The actual report also shows timings. **READY describes basic chat only.** The optional tool check is a warning, so this invocation exits 0 when chat succeeds; inspect the tool line independently. This example uses a local mock, not FreeLLMAPI or a real model. Stop terminal A with Ctrl+C.

## Could the two requests have taken different routes?

In terminal A, restart the fictional server with different route headers:

```sh
python3 examples/mock_openai_server.py --split-routes
```

In terminal B, compare the two headers without making an extra request:

```sh
llm-doctor --base-url http://127.0.0.1:8765/v1 --model demo-model --no-prompt --check-tool-calling --compare-route-headers
```

Both checks pass in this fictional example, and the report says the two `X-Routed-Via` header values differed. The flag only reports **matched**, **differed**, or **unavailable**; it does not print or save the values. It compares trimmed ASCII header values in memory, not the underlying models. Matching labels alone do not prove identical routing; differently encoded equivalent labels can differ. A missing, repeated, malformed or overlong header, a non-successful response, and cache/idempotency markers without a provider/model route produce **unavailable**. This is a synthetic example; stop the server with Ctrl+C.

## Check a running router

If you already run FreeLLMAPI using its documented Docker setup, its [README](https://github.com/tashfeenahmed/freellmapi#works-with-openai-compatible-clients) uses `http://localhost:3001/v1` and a unified key. In the terminal where `llm-doctor` is installed:

```sh
llm-doctor --base-url http://localhost:3001/v1 --model auto --max-output-tokens 128 --check-tool-calling --compare-route-headers
```

Enter your own unified key at the hidden prompt. Replace the address and model alias if your installation uses others. This recipe follows the project's published client settings; **we have not run it against a live FreeLLMAPI instance or provider**.

The doctor sends two separate requests: one basic chat request and one forced tool-call request. A router can perform its own retries or fallbacks, so the upstream request count and actual routed models may differ. FreeLLMAPI documents an [`X-Routed-Via` response header](https://github.com/tashfeenahmed/freellmapi/blob/main/docs/en/api/01-rest-api.md#response-headers); the optional comparison checks whether the two returned labels match. It does not show the labels or establish that either is accurate. The doctor does not execute the proposed function, benchmark availability, schedule checks or change the router. Provider quota or charges depend on your own configuration.

A tool `PASS` means one response had the expected function name and argument shape. A `WARN` means this check did not confirm that shape: HTTP errors, output limits, a different response or a temporary failure can all produce it. It does **not** prove a model never supports tools. To investigate an underlying model, use the router's own logs or routing information and select an appropriate direct route; do not assume two requests using `auto` reached the same model.

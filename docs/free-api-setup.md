# From a free-tier API to one checked request

[Project](../README.md) · [简体中文](free-api-setup.zh-CN.md)

**Choose the provider, copy its exact model ID, then check it before adding it to your app.** Start with the direct request below, or install the [v0.6.4 wheel or current source](../README.md#quick-start) for the `llm-doctor` checks. The wheel includes these output controls; the local mock example at the end uses files from the source repository.

Official documentation reviewed on **2026-10-21 (Asia/Shanghai)**. Provider recipes are documentation-checked; no live provider account or key was used to validate them. The local reproduction below is a synthetic fixture.

## Send one request without installing this tool

With an OpenRouter API key, this Bash/zsh example sends one chat request to its [free-model router](https://openrouter.ai/openrouter/free). The router chooses an available free model rather than pinning one. The endpoint and model label were rechecked against OpenRouter's public documentation on **2026-10-31**; the example has not been run with a live key.

```bash
printf 'OpenRouter API key: '
read -r -s OPENROUTER_API_KEY
printf '\n'
curl -sS -w '\nHTTP %{http_code}\n' https://openrouter.ai/api/v1/chat/completions \
  -H "Authorization: Bearer $OPENROUTER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"openrouter/free","messages":[{"role":"user","content":"Say hello in one sentence."}],"max_tokens":64}'
unset OPENROUTER_API_KEY
```

The key prompt does not echo your input. Check the returned JSON or HTTP error and your account's [current free-model limits](https://openrouter.ai/docs/api/reference/limits). This is a single request with no fallback array. For your own application, use the base URL `https://openrouter.ai/api/v1`, the same key, and `openrouter/free` as the model. If you would rather use the separate API I maintain, [ChinaGenAI's setup page](https://chinagenai.com/connect) lists its base URL and `free` model label.

## Pick your route

| Provider | OpenAI-compatible base URL | Model selection | Check before calling |
| --- | --- | --- | --- |
| [Groq](https://console.groq.com/docs/openai) | `https://api.groq.com/openai/v1` | An ID enabled for your organization | [Current models](https://console.groq.com/docs/models) and [your plan's limits](https://console.groq.com/docs/rate-limits) |
| [Gemini Developer API](https://ai.google.dev/gemini-api/docs/openai) | `https://generativelanguage.googleapis.com/v1beta/openai` | An exact model ID enabled in your AI Studio project | Its [Free Tier pricing entry](https://ai.google.dev/gemini-api/docs/pricing) and [project limits](https://ai.google.dev/gemini-api/docs/rate-limits) |
| [OpenRouter](https://openrouter.ai/docs/quickstart) | `https://openrouter.ai/api/v1` | `openrouter/free`, or a listed free variant | [Free variants and fallback behavior](https://openrouter.ai/docs/guides/routing/model-variants/free) and [account limits](https://openrouter.ai/docs/api_reference/limits) |

You supply the provider's key through the hidden prompt. Nothing in this guide creates an account, enables billing or adds paid fallbacks. Free eligibility and capacity belong to the provider and your account, not to this tool.

Before starting an agent loop, [check the billing project and request count](api-cost-preflight.md). Gemini now documents project spend caps with reporting delays; this is separate from the doctor's per-check output cap.

## Groq

This example uses the model ID listed in Groq's current Free Plan table, and the output field documented in its [API reference](https://console.groq.com/docs/api-reference):

```bash
llm-doctor --base-url https://api.groq.com/openai/v1 --model openai/gpt-oss-20b --token-field max_completion_tokens --max-output-tokens 1024
```

That is one chat request with an explicit upper output cap, not a promise that 1,024 tokens will be used or will always be sufficient. Check your console if access is rejected. Old tutorials may still recommend `llama-3.1-8b-instant`: Groq's [deprecation notice](https://console.groq.com/docs/deprecations) retired it for Free/Developer usage on 2026-08-16 while exempting committed-spend Enterprise customers.

## Gemini

Copy an enabled model ID from your AI Studio project and **replace `YOUR_ENABLED_GEMINI_MODEL`**:

```bash
llm-doctor --base-url https://generativelanguage.googleapis.com/v1beta/openai --model YOUR_ENABLED_GEMINI_MODEL --max-output-tokens 1024
```

Keep the entire `/v1beta/openai` path. A bare Google API host is not this chat-completions route. The [compatibility guide](https://ai.google.dev/gemini-api/docs/openai) uses your Gemini API key as a Bearer credential on this route; native Gemini clients use a different configuration. Model thinking defaults also vary. [AI Studio](https://aistudio.google.com/) shows your project's available models and limits. A model existing in documentation does not grant your project access to it.

## OpenRouter

```bash
llm-doctor --base-url https://openrouter.ai/api/v1 --model openrouter/free --max-output-tokens 1024
```

The [Free Models Router](https://openrouter.ai/openrouter/free) selects among available free models; it does not pin one model. For repeatable model comparisons, choose an exact free catalog entry instead. Appending `:free` to an arbitrary model name does not create a free variant. This command sends one model, with no fallback array. OpenRouter documents that a fallback to a paid model is billed at that model's price.

## Another route I maintain: ChinaGenAI

[ChinaGenAI](https://chinagenai.com/connect) is a separate project I maintain. Its public setup page lists the following base URL and client model label; the label `free` does not identify a fixed underlying model. With a site key, you can check one chat request using:

```bash
llm-doctor --base-url https://chinagenai.com/v1 --model free --max-output-tokens 128
```

Enter the site key at the hidden prompt. I checked the public setup page on 2026-10-30; this recipe was not tested with a live account or key. Check the site's current access and availability before relying on it. The doctor sends one request and does not create a key or retry.

## Read the result before wiring up your app

| Result | Next action |
| --- | --- |
| `READY` | This basic chat returned non-empty text without a reported length ending. Check the extra features your app actually uses separately. |
| HTTP 401/403 | Check that the key, provider route, model and account access belong together. |
| HTTP 404 | Recheck the whole base URL and the current model ID. |
| HTTP 429 | Inspect the provider's account quota and limits; the doctor does not retry. |
| HTTP 503 | Check the provider's status and account access; it may be temporary service unavailability, not necessarily an exhausted quota. |
| `finish_reason=length` | The generation was truncated. Review the output/context budget; reasoning may consume tokens before an answer. Explicitly choose a different cap if appropriate. |

For 429 or 503, v0.6.4 and current source show an unambiguous `Retry-After` of up to 86,400 seconds when present. HTTP-date values are converted using your local clock; invalid, duplicate or larger values are omitted. A hint is not a guarantee of availability. The tool never retries automatically, though each explicitly selected optional check sends its own request. The older v0.6.3 wheel does not contain this hint.

`--max-output-tokens` accepts 1–8192. `--token-field` sends exactly one of `max_tokens` or `max_completion_tokens`; it does not infer a field from the model name. Defaults are 12 tokens for chat, 24 for JSON or vision, and 128 for tool-calling checks. A larger cap can use more of a provider's quota or incur cost on a paid route. No automatic budget increase or retry occurs.

## Reproduce the output-budget case without a key

In terminal A, from the current repository:

```bash
python3 examples/mock_openai_server.py --simulate-token-limit
```

In terminal B, make two separate, explicit requests:

```bash
llm-doctor --base-url http://127.0.0.1:8765/v1 --model demo-model --no-prompt
llm-doctor --base-url http://127.0.0.1:8765/v1 --model demo-model --no-prompt --max-output-tokens 64
```

The first exits 1 and reports `finish_reason=length`; the second exits 0 with `Basic chat: READY`. This fixture intentionally returns an empty, length-ended response below 64 tokens and a fixed answer at or above 64. It does not run a reasoning model, measure a provider or establish a universal sufficient budget. Stop terminal A with `Ctrl+C`.

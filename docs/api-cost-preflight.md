# Before a free API becomes a bill

[中文](api-cost-preflight.zh-CN.md) · [Three provider setups](free-api-setup.md)

**Check the project, count the requests, then start the loop.** A model appearing
in a catalog does not establish that your account's route is free.

## Gemini: use the provider's existing controls first

Checked 2026-10-21. Google's [billing documentation](https://ai.google.dev/gemini-api/docs/billing#project-spend-caps)
provides project spending controls in [AI Studio's Spend page](https://aistudio.google.com/spend):
**Monthly spend cap → Edit spend cap**, for accounts with a suitable project role.
Invoiced/offline accounts are excluded. Google describes processing delays of
around ten minutes and possible overages from long-running work. Treat it as a
spending control with delayed enforcement, rather than an instantaneous stop.

Check the selected project's billing status and model eligibility. Its API keys
share project-level usage. Google also documents separate [spend-based rate
limits](https://ai.google.dev/gemini-api/docs/rate-limits#spend-rate-limits);
an RPM limit, a monthly cap and an application retry limit govern different things.
This guide does not change your account or billing settings.

## Count what the diagnostic actually sends

These are **observed HTTP requests** from the current doctor against its local
mock, not provider invoices or token measurements:

| Selected checks | HTTP requests | Generation requests |
|---|---:|---:|
| Default chat only | 1 | 1 |
| Chat + model list + streaming + stream options + JSON mode | 5 | 4 |
| All checks, including image generation and vision input | 7 | 6 |

The catalog is one GET. Each selected generation check is a separate POST;
vision input and image generation are opt-in checks.
There is no automatic retry. Each selected generation check gets its own output
cap; `--max-output-tokens` is not one shared allowance for the entire run.

Reproduce the counts from current source, with Python 3.9+, no installation or key:

```sh
python3 examples/count_demo_requests.py
```

It starts an isolated loopback server on an available port, records actual HTTP
methods for each mode, checks the counts and closes the server. It never reads
your environment keys or contacts a provider. An installed package may expose
fewer checks than this source tree; use `llm-doctor --version` to identify it.

## Before running your own application

1. Select the exact provider, project and model. Check its current price and
   account eligibility using the [provider recipes](free-api-setup.md).
2. Start with one chat check. Choose output tokens explicitly for a reasoning
   model; inspect `finish_reason=length` before changing the cap.
3. Add only the optional checks your application needs. Count retries,
   fallbacks, concurrent workers and scheduled runs in your application too.
4. Inspect provider usage after the small run, allowing for its documented
   reporting delay. Do not project a dollar total from this local request count.

LLM Endpoint Doctor diagnoses requests; it is not a proxy, account spending
monitor or billing kill switch. It cannot stop other applications using the key.

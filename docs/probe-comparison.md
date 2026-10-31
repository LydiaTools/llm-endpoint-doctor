# Two ways to inspect an API endpoint

When an app cannot connect, you may want a small check of one route. When building a server, you may instead want a wider catalog of endpoint behavior. These are different jobs.

[Am I OpenAI Compatible?](https://github.com/heiervang-technologies/am-i-openai-compatible) offers a broader endpoint catalog and structured reports. LLM Endpoint Doctor offers individually selected checks, with one chat or one Responses text request by default. Neither result alone certifies an application or provider.

## Try the same synthetic endpoint

On macOS/Linux, from a checkout of this repository, create a **separate** virtual environment for aioc. The pinned commit corresponds to its v0.4.3 tag; installation downloads third-party code and dependencies:

```sh
python3 -m venv /tmp/aioc-demo-venv
/tmp/aioc-demo-venv/bin/python -m pip install 'git+https://github.com/heiervang-technologies/am-i-openai-compatible.git@96ed18c1dc9851b83037681fe7a2e1fcdd064ef1'
python3 examples/compare_aioc_local.py --aioc /tmp/aioc-demo-venv/bin/aioc
```

Python 3.10+ and Git are needed for aioc. The script starts its own loopback-only server, uses synthetic responses, strips the inherited environment passed to the two tools, and shuts down the server afterward. It sends no real provider requests or API keys. Its `aioc` argument is an executable you explicitly provide; use a trusted installation. On Windows, pass the corresponding `Scripts\\aioc.exe` path.

In our run, the fixture returned a chat-shaped response and a 404 for `/v1/responses`. Doctor's chat and Responses checks made one POST each. aioc, limited to those two paths with `--endpoints-filter`, made five requests: two model-list GETs, two chat POSTs (existence and shape checks), and one Responses POST. Its report marked the Responses route absent. The two commands do not perform identical checks; counts and results depend on versions, flags and endpoint behavior. This is a reproducible comparison of one synthetic case, not a benchmark, a real-provider test, or a statement that one tool is better for every task.

The example is in current source; the v0.7.1 wheel predates this comparison script. [Return to the README](../README.md).

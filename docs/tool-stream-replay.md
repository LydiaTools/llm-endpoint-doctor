# Chat works. Streamed tool calls need a different check.

A successful text response checks one request path. A tool stream also has to be assembled correctly by its consumer.

In Chat Completions chunks, call `id`, `type` and function `name` can be absent from later deltas. The required `index` connects each delta to its call. Accumulate argument fragments for that call, then inspect the completed arguments. See the [official OpenAI chunk reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events#chat.completion.chunk).

## Try it in one command

From a current source checkout, using Python 3.9+:

```sh
python3 examples/replay_tool_stream.py
```

This runs six [original synthetic inputs and explicit expectations](../examples/tool_stream_cases.json), entirely offline:

| Control | Result | What it distinguishes |
| --- | --- | --- |
| ID/name in the first delta only | Accept, 1 call | Missing later metadata is normal for this stream. |
| Two interleaved calls | Accept, 2 calls | Argument fragments stay with their own index. |
| ID/name never arrive | Reject | Complete-call identity really is missing. |
| Argument JSON cut off | Reject | Terminal markers cannot repair unfinished arguments. |
| Valid arguments, `finish_reason: length` | Reject | A parseable object does not establish a completed tool turn. |
| Capture without `[DONE]` | Reject | This capture does not show the expected terminal marker. |

Every control must match its explicit outcome, including the expected rejections, for the demonstration to exit successfully. The first valid control is also fed to a deliberately wrong per-delta identity rule; that rule rejects it. This is a constructed consumer counterexample, not a run of an upstream SDK.

Inspect a local capture:

```sh
python3 examples/replay_tool_stream.py --file capture.sse
```

Accepted captures exit `0`; rejected captures exit `1`. Standard output contains only `accepted`, a fixed `reason`, and `call_count`. Captured text, tool names, IDs, arguments and filenames are omitted. No network request or tool execution occurs.

## Use the result to locate the layer

1. **An HTTP path or model ID fails:** compare the endpoint protocol and configured provider with the actual request. Switching the model alone may not change the routed protocol.
2. **A consumer rejects an argument-only delta:** check whether it expects complete call metadata in every chunk. The first control separates that mistake from missing identity across the whole stream.
3. **Argument values are wrong despite a complete stream:** inspect the model, chat template, sampling and inference engine with the same task. This example checks assembly and JSON objects, not semantic tool accuracy.

One recent [AI SDK harness report](https://github.com/vercel/ai/issues/22941) describes native-provider routing against an OpenAI-compatible gateway and a consumer rejecting later tool deltas. The author says it arose in a simple test project. A [LocalLLaMA discussion](https://www.reddit.com/r/LocalLLaMA/comments/1xaos52/qwen38flashnext_vs_qwen3827b_tool_calling_issues/) raises model/engine/template possibilities; its replies do not establish a single cause. Those are distinct reports, not evidence that this example fixes either product.

## Check one live endpoint

To check a selected OpenAI-compatible route once, run:

~~~sh
llm-doctor --base-url https://api.example.com/v1 --model your-model-id --check-tool-streaming
~~~

This sends one additional streamed function-call request. The doctor assembles SSE deltas and checks for one completed call with the expected name and arguments. It never executes the function, prints or stores response content, or retries automatically. The provider may count or charge the request. A pass describes this response from the selected model and route only; it is not a model benchmark, SDK certification, or guarantee about a router's later requests. Provider retries or fallbacks can make upstream request counts differ.

## Explicit example profile

This bounded example accepts one choice at index `0`, up to eight indexed function calls, stable complete IDs/names when supplied, JSON-object arguments, `finish_reason: tool_calls` and terminal `[DONE]`. It supports SSE multi-line data events and CRLF. Limits are 1 MB input, 128 events and 64,000 argument characters per call. It reports incomplete captures without inferring why they stopped.

It is not a full SSE/API validator, a schema/permission checker, a model benchmark, a Responses API decoder or a live provider compatibility test. The optional `llm-doctor --check-tool-calling` probe checks a separate non-streaming provider response shape; it does not test this SSE assembly path. This supplementary example ships in current source. All examples are independently authored under MIT; no upstream implementation or captured user traffic is included.

## 中文

普通聊天成功，只证明那一条请求能通。流式工具调用还依赖客户端正确组装增量：`index` 标记所属调用，ID、名称和类型可能只在首块出现。缺少后续重复元数据，不等于完整调用缺字段。

### 对一个真实接口做一次检查

```sh
llm-doctor --base-url https://api.example.com/v1 --model your-model-id --check-tool-streaming
```

这会额外发送一次流式函数调用请求，拼接 SSE 增量并核对一条完整调用。工具不会执行函数、打印或保存响应内容，也不会自动重试；服务商可能计量或收费。通过只说明所选模型和路由这一次响应符合预期，不代表模型基准、SDK 认证或路由器后续请求保证。

运行上面的单条命令，可离线检查六组合成对照。前两组应接受，后四组应拒绝；全部符合明确预期，程序才返回成功。另一个故意错误的逐块校验规则会拒绝正常首组，帮助定位客户端误判。它没有运行或修复上游 SDK。

`--file capture.sse` 可读本机捕获，只打印判断、固定原因、调用数量；不会上传文件、打印参数或执行工具。该有限示例不评判模型工具调用能力，不把引擎、模板和客户端协议问题混为一谈。可选的 `llm-doctor --check-tool-calling` 会单独检查非流式 Provider 响应结构，不检查这里的 SSE 组装路径。

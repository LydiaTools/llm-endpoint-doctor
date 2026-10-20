# LLM Endpoint Doctor｜大模型接口体检

**把 OpenAI 兼容接口接进应用之前，先确认它能不能正常工作。**

运行一条本地命令，检查 API 地址、模型 ID、鉴权和第一次对话响应。需要时再额外检查模型列表、流式输出和 JSON 模式。

[English README](README.md) · [反馈问题](https://github.com/LydiaTools/llm-endpoint-doctor/issues)

## 为什么做这个工具

“兼容 OpenAI”仍可能遇到地址填错、模型 ID 已下线、服务商有特殊鉴权方式，或普通对话可用但流式不支持。LLM Endpoint Doctor 让你先做一次小检查，再把这些问题带进自己的应用调试。

它补充 API 资源目录：目录帮你找到接口，本工具检查你准备使用的接口。

**正在接入免费档模型？** [Groq、Gemini、OpenRouter 三家接入指南](docs/free-api-setup.zh-CN.md)列出正确地址、模型选择入口和可复制的单次检查。[English guide](docs/free-api-setup.md)。

## 快速开始

需要 Python 3.9 或更新版本，无第三方运行依赖。

```bash
git clone https://github.com/LydiaTools/llm-endpoint-doctor.git
cd llm-endpoint-doctor
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
llm-doctor --base-url https://api.example.com/v1 --model your-model-id
```

API Key 会通过隐藏输入提示读取。无 Key 接口直接回车。默认只发送 **一条**聊天补全请求。

默认输出预算刻意保持很小：普通检查12 token，可选JSON检查24 token。使用思考型模型时，可显式增加预算，例如 `--max-output-tokens 1024`；服务商要求新参数名时，再选 `--token-field max_completion_tokens`。设置会应用到每个已选生成检查，不增加请求或自动重试。`finish_reason=length`会明确标为截断，即使已有部分文字、合法JSON、用量或SSE的`[DONE]`也不算该项完整通过。[无需Key的复现步骤](docs/free-api-setup.zh-CN.md#不用key复现输出预算问题)。

```text
LLM Endpoint Doctor
Target host: https://api.example.com
Model: your-model-id

[PASS] chat completion — 收到符合预期格式的非空回复。

Basic chat: READY
报告不包含 API Key、提示词或回复内容。
```

## 不用服务商 Key 也能先试

你可以先在本机跑完整流程，不用注册服务商，也不会向互联网发送请求。打开第二个终端，启动固定响应的模拟接口：

```bash
python3 examples/mock_openai_server.py
```

再运行体检命令：

```bash
llm-doctor \
  --base-url http://127.0.0.1:8765/v1 \
  --model demo-model \
  --no-prompt \
  --check-model-list \
  --check-streaming \
  --check-stream-options \
  --check-json-mode
```

```text
[PASS] model catalog — 找到完全匹配的模型 ID。
[PASS] chat completion — 收到符合预期格式的非空回复。
[PASS] streaming — 收到 SSE 完成标记。
[PASS] stream options — 流式请求完成，并返回了请求的用量信息。
[PASS] JSON mode — 返回了 JSON 对象。

Basic chat: READY
```

模拟服务只监听本机回环地址并返回固定响应。它展示请求和报告流程，不代表已测试任何外部服务。按 `Ctrl+C` 停止。

## 聊天正常，流式工具调用却失败？

先复现协议层的小例子，再判断要不要换模型。使用当前仓库源码，无需 Key、模型、服务或安装包：

```bash
python3 examples/replay_tool_stream.py
```

六组原创离线对照会接受「只在首个增量发送名称／ID」及交错的多次调用，拒绝完整调用身份缺失、JSON 不完整及输出上限中断。即使参数 JSON 完整，`finish_reason=length` 也不会被当成已完成的工具轮次。反例另展示了为何逐块强制要求名称和 ID 会误拒正常数据。

也可用 `--file capture.sse` 检查本机捕获；只输出判断、固定原因与调用数量，不执行工具。[实际对照、输出与中英文排查步骤](docs/tool-stream-replay.md)。这是当前源码中的补充案例，固定 v0.2.0 发行包保持原样；没有新增真实服务商工具调用探针。

## 可选检查

每个选项都会增加请求。流式、流式选项和 JSON 模式分别额外发送一条生成请求。

```bash
llm-doctor \
  --base-url https://api.example.com/v1 \
  --model your-model-id \
  --check-model-list \
  --check-streaming \
  --check-stream-options \
  --check-json-mode \
  --json-report endpoint-report.json
```

只有目标文件不存在时才会创建 JSON 报告。报告包含目标主机、模型 ID、检查结果和耗时，不包含 API Key、URL 路径、提示词或回复内容。

## 非交互模式与 CI

无 Key 接口可以显式跳过输入提示：

```bash
llm-doctor --base-url http://localhost:8000/v1 --model local-model --no-prompt
```

CI 中可以从环境变量读取 Key，避免把它放进命令行参数：

```yaml
- name: Check LLM endpoint
  env:
    LLM_API_KEY: ${{ secrets.LLM_API_KEY }}
  run: >-
    llm-doctor --base-url https://api.example.com/v1
    --model your-model-id --api-key-env LLM_API_KEY
```

指定的环境变量缺失或为空时，命令会在发送请求前停止。

## 检查内容

- **普通对话：** 向 `/chat/completions` 发送一条短测试提示，确认返回符合 OpenAI Chat 格式的文本。
- **模型列表（可选）：** 检查 `/models` 是否列出完全匹配的模型 ID。部分服务商使用别名或不完整目录，因此未列出只显示提醒，不直接判定对话失败。
- **流式输出（可选）：** 检查是否收到 server-sent events 结束标记。
- **流式选项（可选）：** 在流式请求中发送 `stream_options: {"include_usage": true}`，检查端点是否接受该参数并返回用量信息。普通流式可用但不兼容此客户端选项时，这项检查会给出提醒。
- **JSON 模式（可选）：** 请求返回 JSON 对象，并检查内容能否解析为对象。
- **常见故障：** 区分鉴权/权限错误、接口或模型不存在、速率限制、服务错误、超时和 TLS 错误，不输出服务商的原始错误正文。

工具不会自动重试，不评价回答质量、不做服务商跑分、不保证兼容每种客户端，也不能判断服务商的免费额度会长期不变。

## 隐私与请求处理

- API Key 通过隐藏输入或指定的环境变量读取，只放在发往你指定接口的 Authorization 请求头中；工具不会把它写入文件。
- 工具没有遥测或账号系统。
- 报告省略 API Key、URL 路径、提示词和回复正文。
- 远程接口必须使用 HTTPS；只有 localhost 测试允许普通 HTTP。
- 工具拒绝 URL 查询参数、内嵌凭据和跨域跳转，避免将 Key 转发给其他主机。
- 接口服务商会收到测试请求，并可能按自己的规则计量。可选检查会增加请求次数，只在需要时启用。

## 开发

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

测试使用本地模拟服务，不连接外部接口，也不使用真实 API Key。

## 许可证

MIT，详见 [LICENSE](LICENSE)。

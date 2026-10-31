# 聊天成功，就代表支持工具调用吗？

模型路由器能正常聊天，不代表带工具的请求也一定能通过。[FreeLLMAPI 的一个功能建议](https://github.com/tashfeenahmed/freellmapi/issues/1438)正在讨论如何区分这些能力。这里提供一个手动检查方法，方便接入 Agent 前先看一次实际响应。

[项目主页](../README.zh-CN.md) · [English](chat-vs-tools.md)

## 不需要密钥，先试一个例子

先[安装当前源码](../README.zh-CN.md)。在终端 A 进入源码目录，启动示例：

```sh
python3 examples/mock_openai_server.py --reject-tools
```

在已经安装 `llm-doctor` 的终端 B 运行：

```sh
llm-doctor --base-url http://127.0.0.1:8765/v1 --model demo-model --no-prompt --check-tool-calling
```

这个虚构服务会接受普通聊天请求，并故意对带工具的请求返回 HTTP 400。报告会显示聊天通过、工具调用警告：

```text
[PASS] chat completion — A non-empty chat response was returned.
[WARN] tool calling — Provider returned HTTP 400. Response bodies are omitted from reports.

Basic chat: READY
```

实际报告还会显示耗时。**READY 只代表基础聊天通过**；工具调用是可选检查，因此聊天通过时这条命令的退出码仍为 0，需要单独看工具调用一行。这里是本机模拟服务，不是 FreeLLMAPI 或真实模型。用 Ctrl+C 停止终端 A 的服务。

## 检查已运行的路由器

如果你已经按 FreeLLMAPI 的 Docker 说明运行了服务，它的[项目文档](https://github.com/tashfeenahmed/freellmapi#works-with-openai-compatible-clients)使用 `http://localhost:3001/v1` 和统一密钥。在装有 `llm-doctor` 的终端运行：

```sh
llm-doctor --base-url http://localhost:3001/v1 --model auto --max-output-tokens 128 --check-tool-calling
```

在隐藏输入提示处填写自己的统一密钥；如果你的安装使用其他地址或模型别名，请替换。这个命令参考该项目公开的客户端设置；**我们尚未连接真实 FreeLLMAPI 实例或上游服务验证它**。

诊断器发送两次独立请求：一次普通聊天、一次指定工具调用。路由器自身可能重试或切换上游，因此实际上游请求次数和服务模型可能不同。诊断器不会显示每次由哪个供应商处理，不执行返回的函数，不做可用率评测，不定时运行，也不修改路由器。额度或费用由你的实际配置决定。

工具调用 `PASS` 表示这一次响应符合预期函数名和参数格式。`WARN` 表示本次未能确认，HTTP 错误、输出长度不足、响应格式不同或临时故障都可能导致警告，**不能证明模型永远不支持工具调用**。如果要确认具体模型，请结合路由器日志或路由信息，选择合适的直接路径；不要假定两次 `auto` 请求一定落到同一模型。

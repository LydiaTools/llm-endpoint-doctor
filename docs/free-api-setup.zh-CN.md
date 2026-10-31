# 免费档API：先跑通一条请求，再接进应用

[项目主页](../README.zh-CN.md) · [English](free-api-setup.md)

**选服务商、复制准确模型ID，再做一次检查。** 可以先直接运行下面的请求；如果需要诊断，再[安装v0.6.4发行包或当前源码](../README.zh-CN.md#快速开始)，使用后面的 `llm-doctor` 命令。文末本地模拟示例另需仓库中的源码文件。

官方文档核对日期：**2026-10-21（Asia/Shanghai）**。本页核对了文档与本地请求流程，没有用真实服务商账号/Key验收三家的在线能力；下方复现是明确的模拟输入。

## 不安装本工具，先发一条请求

有 OpenRouter API Key 后，可以在 Bash/zsh 运行下面的示例，向其[免费模型路由](https://openrouter.ai/openrouter/free)发送一次聊天请求。路由会从可用免费模型中选择，而非固定某个模型。我们于 **2026-10-31** 再次核对了其公开接口和模型标签；未用真实 Key 在线运行此示例。

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

输入 Key 时终端不会回显。查看返回的 JSON 或 HTTP 错误，并核对账号的[当前免费模型额度](https://openrouter.ai/docs/api/reference/limits)。示例只发一次请求，不带模型回退数组。接入自己的应用时，Base URL 使用 `https://openrouter.ai/api/v1`，模型填 `openrouter/free`。也可以选择我维护的另一个 API：[ChinaGenAI 接入页](https://chinagenai.com/connect)列出了对应地址和 `free` 模型标签。

## 选接入路径

| 服务商 | OpenAI兼容Base URL | 模型怎么选 | 调用前看哪里 |
| --- | --- | --- | --- |
| [Groq](https://console.groq.com/docs/openai) | `https://api.groq.com/openai/v1` | 当前组织有权限的准确ID | [模型目录](https://console.groq.com/docs/models)、[对应计划额度](https://console.groq.com/docs/rate-limits) |
| [Gemini Developer API](https://ai.google.dev/gemini-api/docs/openai) | `https://generativelanguage.googleapis.com/v1beta/openai` | AI Studio项目中可用的准确ID | [模型免费档价格](https://ai.google.dev/gemini-api/docs/pricing)、[项目限额](https://ai.google.dev/gemini-api/docs/rate-limits) |
| [OpenRouter](https://openrouter.ai/docs/quickstart) | `https://openrouter.ai/api/v1` | `openrouter/free`或目录中存在的免费变体 | [免费变体与回退计费](https://openrouter.ai/docs/guides/routing/model-variants/free)、[账号限制](https://openrouter.ai/docs/api_reference/limits) |

运行时在隐藏提示中输入该服务商的Key。本指南不注册账号、开启计费或添加付费回退。能否用免费档、当时有多少容量，以服务商和你的账号为准。

启动 Agent 循环前，先[核对计费项目和请求次数](api-cost-preflight.zh-CN.md)。Gemini 已有带处理延迟的项目支出上限，与本工具每项检查的输出上限分别管理。

## Groq

下面使用核对时Free Plan表中的模型ID，并采用[API文档](https://console.groq.com/docs/api-reference)说明的输出字段：

```bash
llm-doctor --base-url https://api.groq.com/openai/v1 --model openai/gpt-oss-20b --token-field max_completion_tokens --max-output-tokens 1024
```

只发一条聊天请求，1024是显式上限，不表示一定用满或一定足够。若无权限，回控制台核对。旧教程里的`llama-3.1-8b-instant`已在[下线公告](https://console.groq.com/docs/deprecations)中列为2026-08-16停止Free/Developer使用；有承诺消费合同的Enterprise客户例外，不能仅凭模型名仍在目录就判断免费账号可用。

## Gemini

先从AI Studio复制项目可用的模型ID，**替换命令中的`YOUR_ENABLED_GEMINI_MODEL`**：

```bash
llm-doctor --base-url https://generativelanguage.googleapis.com/v1beta/openai --model YOUR_ENABLED_GEMINI_MODEL --max-output-tokens 1024
```

保留完整的`/v1beta/openai`，只填Google API域名并不是这条聊天路由。[兼容文档](https://ai.google.dev/gemini-api/docs/openai)在该路由使用Gemini Key的Bearer鉴权；原生Gemini客户端配置不同。模型默认思考设置也会变化，去[AI Studio](https://aistudio.google.com/)看自己项目的模型与额度；文档里存在的模型不等于项目已获权限。

## OpenRouter

```bash
llm-doctor --base-url https://openrouter.ai/api/v1 --model openrouter/free --max-output-tokens 1024
```

[Free Models Router](https://openrouter.ai/openrouter/free)在可用免费模型中选择，不固定某个模型。要比较同一模型，应选目录里具体的免费条目。不能给任意模型名加`:free`就当成免费模型。这条命令只发送一个模型、不带回退数组；官方文档明确，回退到付费模型按那个模型收费。

## 另一个由我维护的接入方式：ChinaGenAI

[ChinaGenAI](https://chinagenai.com/connect) 是我维护的另一个项目。其公开接入页列出了下面的 Base URL 和客户端模型标签；`free` 是标签，不代表固定的底层模型。有网站 Key 后，可以用下面的命令检查一次聊天请求：

```bash
llm-doctor --base-url https://chinagenai.com/v1 --model free --max-output-tokens 128
```

在隐藏提示中输入网站 Key。我于 2026-10-30 核对了公开接入页；这条命令尚未使用真实账号或 Key 在线验收。实际使用前请查看网站当前的访问条件和可用情况。工具只发送一次请求，不会创建 Key 或自动重试。

## 看结果，再接应用

| 结果 | 下一步 |
| --- | --- |
| `READY` | 基础聊天返回非空正文且没有报告length结束；应用用到的额外能力另选检查。 |
| HTTP 401/403 | 核对Key、服务商路由、模型和账号权限是否匹配。 |
| HTTP 404 | 核对完整Base URL与当前模型ID。 |
| HTTP 429 | 看服务商账号额度与限流；本工具不自动重试。 |
| HTTP 503 | 查看服务商状态和账号访问情况；可能是临时服务不可用，不一定是额度耗尽。 |
| `finish_reason=length` | 生成被截断。检查输出/上下文预算，思考可能先消耗token；需要时手动选择另一预算。 |

对于 429 或 503，v0.6.4 和当前源码会显示单个有效 `Retry-After` 中不超过 86,400 秒的等待提示。HTTP 日期格式使用本机时钟换算；无效、重复或超出范围的值不显示。提示不保证等待后一定可用。工具不会自动重试，但每个明确选择的可选检查都会各自发送请求。较早的 v0.6.3 wheel 尚不包含此提示。

`--max-output-tokens`范围1–8192；`--token-field`只发送`max_tokens`或`max_completion_tokens`中的一个，不按模型名猜测。默认普通聊天12 token、JSON和看图检查24 token、工具调用检查128 token。更高上限可能多消耗额度，付费路由可能收费；工具不会自动加预算或重试。

## 不用Key复现输出预算问题

终端A，在当前仓库目录运行：

```bash
python3 examples/mock_openai_server.py --simulate-token-limit
```

终端B，显式运行两次：

```bash
llm-doctor --base-url http://127.0.0.1:8765/v1 --model demo-model --no-prompt
llm-doctor --base-url http://127.0.0.1:8765/v1 --model demo-model --no-prompt --max-output-tokens 64
```

第一条退出码1，明确报告`finish_reason=length`；第二条退出码0，显示`Basic chat: READY`。模拟器刻意在上限小于64时返回空正文与length结束，达到64时返回固定答案。它没有运行思考模型、衡量服务商或证明64是通用预算。终端A按`Ctrl+C`停止。

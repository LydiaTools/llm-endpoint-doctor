# 免费 API 接入前，先核对项目和请求次数

[English](api-cost-preflight.md) · [三家服务商接入](free-api-setup.zh-CN.md)

**先看项目、数清请求，再启动循环。** 模型出现在目录里，不等于你的账号路由免费。

## Gemini：先用平台已有设置

核查日：2026-10-21。Google [计费文档](https://ai.google.dev/gemini-api/docs/billing#project-spend-caps)
已提供项目支出控制：在 [AI Studio Spend 页](https://aistudio.google.com/spend)选择
**Monthly spend cap → Edit spend cap**，需要相应项目角色。Invoiced/offline 账号除外。
官方说明处理可能延迟约十分钟，长任务可能超额；它是有延迟的支出控制，并非瞬间停止。

先确认项目计费状态和模型的免费档资格。同项目 Key 共用项目用量。官方另有
[按支出计的限流](https://ai.google.dev/gemini-api/docs/rate-limits#spend-rate-limits)；
每分钟请求限制、月度支出上限和应用重试次数管理的是不同事情。本指南不会改账号设置。

## 诊断到底发送几次请求

以下是在本地模拟接口实际观察的 HTTP 请求，不是服务商账单或 token 测量：

| 所选检查 | HTTP 请求 | 生成请求 |
|---|---:|---:|
| 默认聊天 | 1 | 1 |
| 聊天＋模型列表＋流式＋stream options＋JSON | 5 | 4 |
| 所有检查（含生图和看图输入） | 7 | 6 |

模型列表是一次 GET。每项生成检查各发一次 POST；生图和看图输入均为可选项。
`--max-output-tokens` 应用于每项生成检查，并非整个运行共享的总预算。

当前源码中运行以下命令即可复现，需要 Python 3.9+，不必安装包或输入 Key：

```sh
python3 examples/count_demo_requests.py
```

脚本在空闲回环端口启动隔离服务，记录各模式的真实 HTTP 方法，核对次数后关闭服务。
不读取环境中的 Key，也不联系服务商。已安装的包可能比当前源码少一些检查选项，可运行 `llm-doctor --version` 确认版本。

## 接进自己的应用前

1. 确认服务商、项目和准确模型，按[接入指南](free-api-setup.zh-CN.md)核对价格与账号资格。
2. 先做一项聊天检查。思考型模型显式选择输出上限，遇到 `finish_reason=length` 先检查再调整。
3. 只增加实际需要的检查，还要数清应用中的重试、回退、并发工作进程和定时运行。
4. 小样后查看平台用量，并考虑官方说明的延迟。不能把本地请求次数直接推算成金额。

LLM Endpoint Doctor 诊断接口，不充当代理、账号支出监控或计费停止开关，也不能阻止
其他应用使用同一个 Key。

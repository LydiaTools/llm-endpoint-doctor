# 两种接口检查方式

应用接不上接口时，你可能只想先检查一条路径；开发接口服务时，也可能需要更广泛地查看多条接口的行为。这是两种不同的任务。

[Am I OpenAI Compatible?](https://github.com/heiervang-technologies/am-i-openai-compatible) 提供更广的接口目录和结构化报告；LLM Endpoint Doctor 提供按需选择的检查，默认的聊天或 Responses 文本检查各发送一次请求。两者的单次结果都不能证明整个应用或供应商兼容。

## 对同一个模拟接口试一试

在 macOS/Linux 上，从本仓库源码目录运行以下命令，为 aioc 建立**独立**虚拟环境。固定提交对应其 v0.4.3 标签；安装会下载第三方代码与依赖：

```sh
python3 -m venv /tmp/aioc-demo-venv
/tmp/aioc-demo-venv/bin/python -m pip install 'git+https://github.com/heiervang-technologies/am-i-openai-compatible.git@96ed18c1dc9851b83037681fe7a2e1fcdd064ef1'
python3 examples/compare_aioc_local.py --aioc /tmp/aioc-demo-venv/bin/aioc
```

aioc 需要 Python 3.10+ 和 Git。脚本只启动自己的本机回环模拟服务，不向真实供应商发请求或传递 API 密钥，结束时关闭服务；启动两个工具时不继承用户环境。`--aioc` 是你明确指定的可执行文件，请使用可信安装。Windows 请传入相应的 `Scripts\\aioc.exe` 路径。

本次运行中，模拟接口返回聊天格式的响应，`/v1/responses` 返回 404。Doctor 的聊天和 Responses 检查各发出一次 POST；aioc 用 `--endpoints-filter` 限定这两条路径后共发出五次请求：两次读取模型列表、两次聊天 POST（路径存在及响应形状检查）和一次 Responses POST，报告该路径不存在。两组命令的检查内容并不完全相同；请求数和结果随版本、参数与接口行为变化。这只是一个可复现的模拟案例，不是综合性能排行、真实供应商测试或通用优劣结论。

该示例在当前源码中；v0.7.1 wheel 早于这份对比脚本。[返回 README](../README.zh-CN.md)。

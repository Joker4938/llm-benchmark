# CONTEXT.md

## 术语表

### API 配置（API Configuration）
由用户命名的 API 接口设置组合，包含三个字段：API 接口地址（Base URL）、API 密钥（API Key）、模型名称（Model）。
用户可以在压测前保存多份 API 配置，并在运行时选择其中一份使用。

### 压测参数（Benchmark Parameters）
与 API 配置独立的另一组参数，包括总请求数、并发数、最大输出 Token 数、请求超时时间、是否使用长文本上下文提示词。
压测参数不属于 API 配置的一部分，每次压测可独立调整。

# LLM Benchmark CLI 使用说明

LLM Benchmark CLI 是一个面向 **OpenAI Chat Completions 兼容接口**的单机性能压测工具。它与 Web 服务共享同一套压测核心，但不依赖浏览器、FastAPI 服务、SQLite 任务队列或前端页面，适合以下场景：

- 目标服务器没有浏览器；
- Web 服务尚未启动或正在维护；
- 需要用 shell 脚本、计划任务或 CI 命令执行压测；
- 需要快速验证内网模型接口、数据集、阈值或报告导出；
- 需要在完全无法访问互联网的企业内网中运行。

CLI 本身不会访问公网。`diagnose` 和 `validate-dataset` 完全不发起网络请求；`run` 和 `compare` 只访问命令中指定的 OpenAI 兼容接口。请确保 Python 依赖已在联网环境准备好并随部署包带入内网。

> 本文命令默认在项目根目录执行。示例中的地址、模型名、API Key 环境变量和压测规模必须按实际环境调整。

## 1. 安装与命令入口

项目要求 Python 3.10 或更高版本。源码环境安装后会提供 `llm-benchmark` 命令：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install .
llm-benchmark --help
```

在开发目录中也可以不安装 console script，直接使用模块入口：

```bash
python3 -m benchmark_cli --help
```

两种入口功能相同。本文主要使用 `python3 -m benchmark_cli`，如果已经安装，可将其替换为 `llm-benchmark`。

CLI 包含四个子命令：

| 子命令 | 用途 | 是否访问模型接口 |
|---|---|---:|
| `run` | 运行一个性能测试计划 | 是 |
| `compare` | 使用相同负载顺序比较两个或更多模型 | 是 |
| `validate-dataset` | 校验 OpenAI `messages` JSONL 数据集 | 否 |
| `diagnose` | 检查 Python、平台和输出目录 | 否 |

查看子命令参数：

```bash
python3 -m benchmark_cli run --help
python3 -m benchmark_cli compare --help
```

## 2. 端点与 API Key

### 2.1 推荐使用环境变量

不要把 API Key 写入脚本、配置文件或 Git 仓库。推荐先设置环境变量：

```bash
export LLM_BENCHMARK_BASE_URL='http://10.0.0.20:8000/v1'
export LLM_BENCHMARK_MODEL='internal-model'
export LLM_BENCHMARK_API_KEY='replace-me'
```

之后命令中可以省略 `--base-url`、`--model` 和 `--api-key`：

```bash
python3 -m benchmark_cli run --plan smoke --requests 3 --concurrency 1
```

### 2.2 API Key 解析优先级

API Key 按以下顺序解析：

1. 命令行 `--api-key`；
2. 配置文件 `endpoint.api_key_env` 指定的环境变量；
3. 默认环境变量 `LLM_BENCHMARK_API_KEY`；
4. 如果都未配置，则使用空字符串，适用于不要求认证的内网端点。

`Base URL` 和模型名的优先级为：

1. 命令行 `--base-url` / `--model`；
2. 配置文件 `endpoint.base_url` / `endpoint.model`；
3. 环境变量 `LLM_BENCHMARK_BASE_URL` / `LLM_BENCHMARK_MODEL`。

如果最终仍缺少 Base URL 或模型名，CLI 返回退出码 `2`。

### 2.3 TLS 校验

HTTPS 端点默认校验证书。只有明确了解风险并且内网服务确实使用无法验证的自签名证书时，才使用：

```bash
python3 -m benchmark_cli run ... --insecure
```

`--insecure` 会关闭 TLS 证书校验，不能用于不可信网络，也不应作为证书配置错误的长期解决方案。

## 3. 最小冒烟测试

```bash
python3 -m benchmark_cli run \
  --plan smoke \
  --base-url "$LLM_BENCHMARK_BASE_URL" \
  --model "$LLM_BENCHMARK_MODEL" \
  --requests 3 \
  --concurrency 1 \
  --output-tokens 32 \
  --format json \
  --format html
```

未指定 `--requests` 和 `--duration` 时，`smoke` 默认执行 3 个请求；其他非阶梯计划默认执行 10 个请求。生产验收时建议显式填写请求数或时长，避免依赖默认值。

默认行为：

- 使用流式请求；
- 请求超时为 60 秒；
- 随机种子为 `1`；
- 内置负载维度为 `general / short / 128 tokens`；
- 默认并发为 `1`；
- 默认报告格式为 `json` 和 `jsonl.gz`；
- 默认输出目录为 `reports/`。

可使用 `--name` 为计划指定便于识别的名称；未指定时名称为 `cli-<计划类型>`：

```bash
python3 -m benchmark_cli run \
  --name 'release-2026-08-capacity' \
  --plan smoke \
  --requests 3
```

## 4. 六类测试计划

### 4.1 `smoke`：冒烟测试

用途：用极小负载确认地址、认证、模型名、流式协议和报告写入是否正常。它不是容量测试。

```bash
python3 -m benchmark_cli run \
  --plan smoke \
  --requests 3 \
  --concurrency 1 \
  --output-tokens 32
```

建议在每次修改端点、模型或证书配置后先执行。

### 4.2 `baseline`：单并发基线

用途：在并发干扰最小的情况下观察单请求延迟、TTFT、生成速度和成功率，为后续并发测试提供参考。

```bash
python3 -m benchmark_cli run \
  --plan baseline \
  --requests 30 \
  --concurrency 1 \
  --output-tokens 128 \
  --format json \
  --format html
```

为了让历史基线可比，应保持模型、数据集哈希、样本顺序、输入维度、输出 Token、流式模式和计划参数一致。

### 4.3 `fixed_concurrency`：固定并发

用途：在固定最大并发下持续发起请求，观察吞吐、尾延迟、错误率以及生成器是否成为瓶颈。

```bash
python3 -m benchmark_cli run \
  --plan fixed_concurrency \
  --requests 200 \
  --concurrency 20 \
  --timeout 120 \
  --output-tokens 256 \
  --format json \
  --format jsonl.gz \
  --format html
```

这是默认计划；省略 `--plan` 时等同于 `--plan fixed_concurrency`。

### 4.4 `stepped`：阶梯容量测试

用途：按阶段逐步提高并发或 QPS，寻找吞吐拐点、尾延迟恶化点和错误率上升区间。

CLI 阶段格式：

```text
名称:并发:请求数
名称:并发:请求数:QPS
```

示例：

```bash
python3 -m benchmark_cli run \
  --plan stepped \
  --stage warm:2:20 \
  --stage normal:8:100 \
  --stage peak:16:200:12 \
  --cooldown 3 \
  --output-tokens 128 \
  --format json \
  --format html
```

`stepped` 至少需要一个 `--stage`。需要按时长定义阶段时，请在 JSON 配置文件的 `plan.stages[].duration` 中设置；CLI 的 `--stage` 语法使用请求数。

### 4.5 `constant_rate`：恒定到达率

用途：按目标 QPS 调度请求，用于观察排队、背压、实际 QPS 与目标 QPS 的差异。并发数是同时执行请求的上限，不代表固定并发。

```bash
python3 -m benchmark_cli run \
  --plan constant_rate \
  --qps 10 \
  --duration 60 \
  --concurrency 30 \
  --output-tokens 128 \
  --format json \
  --format events.jsonl.gz \
  --format html
```

`constant_rate` 必须提供 `--qps`，并提供 `--requests` 或 `--duration` 作为结束条件。

### 4.6 `stability`：稳定性测试

用途：长时间观察吞吐、延迟、错误、资源使用和事件循环延迟是否持续恶化。

```bash
python3 -m benchmark_cli run \
  --plan stability \
  --duration 1800 \
  --concurrency 8 \
  --warmup 30 \
  --cooldown 10 \
  --output-tokens 128 \
  --format json \
  --format jsonl.gz \
  --format events.jsonl.gz \
  --format html
```

`stability` 必须提供 `--duration`。

## 5. 预热、冷却与安全护栏

- `--warmup 秒数`：正式负载前执行预热，预热样本不计入正式结果；
- `--cooldown 秒数`：计划阶段之间或结束前留出冷却时间；
- `--risk-confirmed`：显式确认高并发、高 QPS 或长时间运行风险。

内置硬上限：

| 项目 | 硬上限 |
|---|---:|
| 并发数 | 500 |
| QPS | 1000 |
| 单计划总时长 | 86400 秒 |
| 总请求数 | 1,000,000 |
| 单请求最大输出 Token | 32768 |

超过硬上限会直接拒绝，`--risk-confirmed` 不能绕过。以下情况必须显式增加 `--risk-confirmed`：

- 并发数大于 100；
- QPS 大于 100；
- 时长大于 3600 秒。

示例：

```bash
python3 -m benchmark_cli run \
  --plan fixed_concurrency \
  --requests 5000 \
  --concurrency 150 \
  --risk-confirmed
```

压测可能影响共享内网模型服务。正式执行前应确认维护窗口、目标地址、模型实例、总并发和总 QPS。

## 6. 测试负载

### 6.1 内置负载维度

未指定数据集或自定义消息时，CLI 使用随代码版本固定的内置负载。

`--prompt-type`：

- `general`：通用问答；
- `structured`：结构化或 JSON 输出；
- `reasoning`：推理、分析和计算。

`--input-size`：

- `short`：短输入；
- `medium`：中等输入；
- `long`：长输入。

`--output-tokens` 设置请求中的最大输出 Token 数。

示例：

```bash
python3 -m benchmark_cli run \
  --plan fixed_concurrency \
  --requests 100 \
  --concurrency 10 \
  --prompt-type reasoning \
  --input-size long \
  --output-tokens 512
```

### 6.2 自定义 System/User 消息

```bash
python3 -m benchmark_cli run \
  --plan fixed_concurrency \
  --requests 50 \
  --concurrency 5 \
  --system-message '你是企业内网运维助手，只输出简洁中文。' \
  --user-message '列出三项大模型服务上线检查项。' \
  --output-tokens 128
```

`--user-message` 不能为空。只设置 `--system-message` 不会创建自定义负载；没有 `--user-message` 时仍使用内置负载。

### 6.3 JSONL 数据集

每一行必须是一个独立 JSON 对象，并包含非空 `messages` 数组：

```jsonl
{"id":"case-1","messages":[{"role":"system","content":"只输出 JSON"},{"role":"user","content":"返回 {\"status\":\"ok\"}"}],"metadata":{"group":"smoke"}}
{"id":"case-2","messages":[{"role":"user","content":"说明 HTTP 429 的含义"}],"metadata":{"group":"general"}}
```

字段说明：

- `id`：可选；省略时自动使用行号；
- `messages`：必填，使用 OpenAI messages 结构；
- `role`：支持 `system`、`user`、`assistant`、`tool`；
- `content`：必填；
- `metadata`：可选对象，用于保存分组等自定义信息。

先校验数据集：

```bash
python3 -m benchmark_cli validate-dataset assets/example.jsonl
```

成功时输出数据集名称、版本、SHA-256 和记录数。格式错误返回退出码 `2`，并指出错误行。

运行数据集：

```bash
python3 -m benchmark_cli run \
  --plan fixed_concurrency \
  --dataset assets/example.jsonl \
  --seed 42 \
  --requests 100 \
  --concurrency 10
```

### 6.4 随机种子与复现

`--seed` 控制数据集抽样顺序，默认值为 `1`。请求数大于数据集记录数时，CLI 会循环数据集并在每轮使用同一随机数生成器重新打乱。

报告会记录：

- 数据集名称、版本和 SHA-256；
- 随机种子；
- 实际选中的记录 ID 顺序；
- prompt/input/output 维度；
- 模型名和流式模式。

要复现一次测试，应同时保留数据集文件、报告 JSON 和完整命令或配置文件。

## 7. 流式与非流式请求

CLI 默认发送流式 Chat Completions 请求，以便统计 TTFT 和生成阶段指标。若目标端点不支持流式协议，可使用：

```bash
python3 -m benchmark_cli run ... --non-stream
```

流式与非流式结果不应直接作为同一基线比较。非流式响应通常无法提供等价的 TTFT 数据，相关阈值可能显示 `not_evaluable`。

## 8. 响应断言

通过 `--assertions` 指定 JSON 数组文件。断言是确定性规则，不调用额外模型，也不会产生 LLM-as-a-Judge 成本。

可用结构示例 `assertions.json`（按测试目标选择所需断言，不要不加区分地全部启用）：

```json
[
  {"type": "non_empty"},
  {"type": "token_range", "options": {"min": 1, "max": 256}},
  {"type": "finish_reason", "value": ["stop", "length"]},
  {"type": "contains", "value": "ok"},
  {"type": "regex", "value": "status\\s*[:=]\\s*ok", "options": {"ignore_case": true}},
  {"type": "exact", "value": "ok", "options": {"strip": true}},
  {"type": "json_parse"},
  {
    "type": "json_schema",
    "value": {
      "type": "object",
      "required": ["status"],
      "properties": {"status": {"const": "ok"}}
    }
  },
  {"type": "response_field", "options": {"path": "result.status", "equals": "ok"}}
]
```

可用断言：

| 类型 | 作用 |
|---|---|
| `non_empty` | 响应去除首尾空白后非空 |
| `token_range` | `completion_tokens` 在 `options.min/max` 范围内 |
| `finish_reason` | finish reason 在允许值中 |
| `contains` | 响应包含指定文本 |
| `regex` | 响应匹配正则表达式 |
| `exact` | 响应与指定文本精确匹配 |
| `json_parse` | 响应可被解析为 JSON |
| `json_schema` | 响应 JSON 通过 Draft 2020-12 Schema 校验 |
| `response_field` | JSON 点路径字段存在或等于指定值，数组索引可写为 `items.0.name` |

使用：

```bash
python3 -m benchmark_cli run \
  --plan fixed_concurrency \
  --requests 50 \
  --concurrency 5 \
  --assertions assertions.json \
  --threshold 'assertion_pass_rate:>=:0.99'
```

传输成功、协议有效和断言通过是三个不同层次。不要只看 HTTP 成功率判断模型响应质量。

## 9. 完成后阈值

`--threshold` 格式为：

```text
指标:运算符:数值
```

支持运算符：`<=`、`>=`、`<`、`>`、`==`。该参数可重复。

```bash
python3 -m benchmark_cli run \
  --plan fixed_concurrency \
  --requests 100 \
  --concurrency 10 \
  --threshold 'latency.p95:<=:2.5' \
  --threshold 'error_rate:<=:0.01' \
  --threshold 'valid_response_rate:>=:0.99' \
  --threshold 'achieved_qps:>=:8'
```

可评估指标：

```text
latency.mean
latency.p50
latency.p90
latency.p95
latency.p99
ttft.mean
ttft.p50
ttft.p95
ttft.p99
generation_duration.mean
request_output_tps.mean
achieved_qps
aggregate_output_tps
error_rate
valid_response_rate
assertion_pass_rate
```

比例指标使用 `0` 到 `1` 的小数，例如 1% 错误率写为 `0.01`。

阈值结果包括：

- `passed`：通过；
- `failed`：失败，CLI 返回退出码 `4`；
- `not_evaluable`：当前没有足够数据，不能评估，不会伪装为通过，也不会单独触发退出码 `4`。

## 10. 历史基线比较

使用上一次运行生成的 JSON 摘要：

```bash
python3 -m benchmark_cli run \
  --plan fixed_concurrency \
  --requests 100 \
  --concurrency 10 \
  --seed 42 \
  --baseline reports/<baseline-task-id>.json \
  --format json
```

基线比较会输出逐指标绝对差异、百分比差异以及 `improved`、`stable`、`regressed` 或 `not_evaluable` 状态。默认使用 5% 容差；比例指标还使用 0.01 的绝对容差。

以下内容不一致时会标记为不兼容，而不是强行给出回归结论：

- 报告 schema 版本；
- 计划类型、并发、请求数、时长、QPS、预热、冷却和阶段；
- 数据集哈希、种子和样本顺序；
- prompt/input/output 维度；
- 模型名和流式模式。

`--baseline` 只产生比较结论，不等同于 `--threshold`，也不会因 `regressed` 自动返回退出码 `4`。需要自动阻断脚本时，应同时配置明确阈值。

## 11. 运行中自动停止

自动停止与完成后阈值不同：它持续观察窗口指标，在同一个指标连续超限后停止调度新请求，并保留已完成样本、触发原因和报告。

| 参数 | 单位/含义 |
|---|---|
| `--max-error-rate` | 错误率，范围 `0` 到 `1` |
| `--max-p95-latency` | P95 延迟上限，秒 |
| `--max-queue-backlog` | 队列积压数量上限 |
| `--max-queue-growth` | 单窗口队列增长上限 |
| `--max-cpu-percent` | CLI 压测生成器进程 CPU 百分比上限 |
| `--max-memory-mb` | CLI 压测生成器进程内存上限，MB |
| `--max-event-loop-lag` | 事件循环延迟上限，秒 |
| `--stop-windows` | 同一指标连续超限窗口数，默认 `3` |
| `--stop-minimum-samples` | 样本窗口最少样本数，默认 `10` |
| `--stop-window-seconds` | 评估窗口长度，默认 `1.0` 秒 |

示例：

```bash
python3 -m benchmark_cli run \
  --plan stability \
  --duration 1800 \
  --concurrency 20 \
  --max-error-rate 0.10 \
  --max-p95-latency 5 \
  --max-queue-backlog 200 \
  --max-queue-growth 50 \
  --max-cpu-percent 90 \
  --max-memory-mb 2048 \
  --max-event-loop-lag 0.5 \
  --stop-windows 3 \
  --stop-minimum-samples 10 \
  --stop-window-seconds 2 \
  --format json \
  --format events.jsonl.gz
```

注意：CPU、内存和事件循环延迟测量的是 **压测生成器进程**，不是远端模型服务器。自动停止属于受控完成，通常返回退出码 `0`；请从 JSON 的 `stopped_reason` 和 `stop_trigger` 判断是否提前停止。

## 12. 模型比较

`compare` 至少需要两个 `--target`。格式：

```text
名称|Base URL|模型|API_KEY环境变量
```

最后一个 API Key 环境变量名可省略。目标名称不能包含 `|`。

### 12.1 顺序比较（默认）

顺序模式一次只运行一个模型，默认更安全，也更适合在共享模型服务器上测量各模型表现。

```bash
export MODEL_A_KEY='key-a'
export MODEL_B_KEY='key-b'

python3 -m benchmark_cli compare \
  --target '模型A|http://10.0.0.21:8000/v1|model-a|MODEL_A_KEY' \
  --target '模型B|http://10.0.0.22:8000/v1|model-b|MODEL_B_KEY' \
  --comparison-mode sequential \
  --resource-semantics independent \
  --plan fixed_concurrency \
  --requests 100 \
  --concurrency 10 \
  --seed 42 \
  --format json \
  --format html
```

所有目标复用相同数据集、随机种子和样本顺序。

### 12.2 同步比较

同步模式同时启动所有模型的测试，必须显式增加 `--confirm-synchronous`：

```bash
python3 -m benchmark_cli compare \
  --target '模型A|http://10.0.0.20:8000/v1|model-a|MODEL_A_KEY' \
  --target '模型B|http://10.0.0.20:8000/v1|model-b|MODEL_B_KEY' \
  --comparison-mode synchronous \
  --confirm-synchronous \
  --resource-semantics shared \
  --plan constant_rate \
  --qps 5 \
  --duration 60 \
  --concurrency 10 \
  --seed 42
```

同步模式总负载按模型数量叠加：

```text
聚合并发 = 每模型并发 × 模型数量
聚合目标 QPS = 每模型目标 QPS × 模型数量
```

例如两个模型分别使用并发 10、目标 5 QPS，则聚合并发为 20，聚合目标 QPS 为 10。

### 12.3 资源语义

`--resource-semantics` 必填：

- `independent`：各目标使用独立端点或独立资源池，可解释为独立容量测量；
- `shared`：目标共享 GPU、模型服务、网关或其他资源，结果包含资源竞争效应。

`shared` 结果不能解释为各模型不受干扰时的绝对容量。比较模块只展示各维度差异，不生成不透明的单一总分。

## 13. 报告与导出

`--format` 可重复使用：

```bash
python3 -m benchmark_cli run ... \
  --output-dir reports \
  --format json \
  --format jsonl.gz \
  --format events.jsonl.gz \
  --format html \
  --format xlsx \
  --format csv
```

| 格式 | 内容 |
|---|---|
| `json` | 版本化任务摘要、计划和时间窗口 |
| `jsonl.gz` | gzip 压缩的逐请求样本 |
| `events.jsonl.gz` | gzip 压缩的任务事件和时间序列来源 |
| `html` | 无 CDN、字体或网络依赖的自包含离线报告 |
| `xlsx` | 摘要和逐样本工作表 |
| `csv` | 逐样本表格，使用 UTF-8 BOM，便于 Windows Excel 打开 |

不指定 `--format` 时默认生成 `json` 和 `jsonl.gz`。报告文件名以任务 UUID 为前缀。摘要中的 `artifacts` 会记录格式、相对路径、大小和 SHA-256。

报告、样本和错误信息会经过脱敏处理，但仍可能包含用户自行放入提示词或模型响应中的业务数据。导出前应按内网数据管理要求检查。

## 14. 机器可读输出与 shell 集成

`--json` 让标准输出只包含最终 JSON；进度事件不会写入 stdout。错误仍写入 stderr。

```bash
python3 -m benchmark_cli run \
  --plan smoke \
  --requests 3 \
  --json > result.json
code=$?
echo "exit_code=$code"
```

`--quiet` 只关闭运行中的进度事件；如果没有 `--json`，任务结束后仍会输出人类可读摘要。

阈值门禁示例：

```bash
set +e
python3 -m benchmark_cli run \
  --plan fixed_concurrency \
  --requests 100 \
  --concurrency 10 \
  --threshold 'error_rate:<=:0.01' \
  --threshold 'latency.p95:<=:3' \
  --json > result.json
code=$?
set -e

case "$code" in
  0) echo '压测通过' ;;
  4) echo '压测完成，但阈值未通过' ;;
  130) echo '压测被用户中断，检查部分报告' ;;
  *) echo "压测执行失败，退出码=$code" ;;
esac
```

## 15. JSON 配置文件

下面示例覆盖 CLI 支持的主要配置项。按实际计划删除不需要的字段，不要同时为一个计划填写互相冲突的结束条件。

```json
{
  "name": "nightly-capacity",
  "endpoint": {
    "base_url": "http://10.0.0.20:8000/v1",
    "model": "internal-model",
    "api_key_env": "LLM_BENCHMARK_API_KEY",
    "insecure": false,
    "timeout": 120
  },
  "plan": {
    "type": "stepped",
    "concurrency": 1,
    "warmup": 10,
    "cooldown": 3,
    "stages": [
      {"name": "warm", "concurrency": 2, "requests": 20},
      {"name": "normal", "concurrency": 8, "requests": 100},
      {"name": "peak", "concurrency": 16, "requests": 200, "qps": 12}
    ]
  },
  "workload": {
    "prompt_type": "general",
    "input_size": "short",
    "output_tokens": 128,
    "dataset": "assets/example.jsonl",
    "seed": 42
  },
  "request": {
    "non_stream": false
  },
  "validation": {
    "file": "assertions.json"
  },
  "thresholds": [
    "error_rate:<=:0.01",
    "latency.p95:<=:3",
    "valid_response_rate:>=:0.99"
  ],
  "safety": {
    "risk_confirmed": false,
    "max_error_rate": 0.1,
    "max_p95_latency": 5,
    "max_queue_backlog": 200,
    "max_queue_growth": 50,
    "max_cpu_percent": 90,
    "max_memory_mb": 2048,
    "max_event_loop_lag": 0.5,
    "stop_windows": 3,
    "stop_minimum_samples": 10,
    "stop_window_seconds": 2
  },
  "reports": {
    "formats": ["json", "jsonl.gz", "events.jsonl.gz", "html"],
    "output_dir": "reports"
  }
}
```

运行：

```bash
python3 -m benchmark_cli run --config benchmark.json
```

命令行参数优先于配置文件。例如临时覆盖并发和请求数：

```bash
python3 -m benchmark_cli run \
  --config benchmark.json \
  --plan fixed_concurrency \
  --requests 50 \
  --concurrency 5
```

注意：

- 配置键是 `reports.formats` 和 `reports.output_dir`；
- 配置文件只保存 `endpoint.api_key_env`，不要保存明文 API Key；
- `thresholds` 是字符串数组；
- 阶梯阶段可使用 `requests` 或 `duration`，也可设置 `qps`；
- 历史基线通过命令行 `--baseline` 指定；
- CLI 参数、配置文件值和环境变量共同解析，最终配置会在运行前经过安全校验。

## 16. Ctrl+C、安全中断与退出码

第一次按 `Ctrl+C` 或收到 `SIGTERM` 时，CLI 请求协作式停止：

1. 不再调度新的请求；
2. 尽量等待已开始请求结束；
3. 汇总已完成样本；
4. 写出已配置的部分报告；
5. 返回退出码 `130`。

不要在第一次中断后立即强制杀死进程，否则可能来不及写出部分报告。

稳定退出码：

| 退出码 | 含义 |
|---:|---|
| `0` | 正常完成；也可能是触发自动停止后的受控完成 |
| `2` | 配置、参数、数据集、断言文件、权限或风险确认错误 |
| `3` | 执行异常，或已有完成请求但传输成功数为 0 |
| `4` | 压测执行完成，但至少一个可评估阈值失败 |
| `130` | 用户中断，已尽量保存部分结果 |

脚本应优先依据退出码判断流程，再读取 JSON 中的 `thresholds`、`stopped_reason`、`stop_trigger` 和 `artifacts` 获取详细结论。

## 17. 离线诊断

```bash
python3 -m benchmark_cli diagnose --output-dir reports
```

输出示例：

```json
{
  "python": "3.12.0",
  "platform": "Linux-...",
  "output_dir": "/opt/llm-benchmark/reports",
  "writable": true,
  "network_check": "skipped"
}
```

诊断只检查 Python 版本、平台信息、输出目录创建和可写状态，并明确跳过网络检测。它不会访问公网，也不会探测模型端点。

## 18. 可直接复制的测试流程

### 18.1 第一步：离线环境诊断

```bash
python3 -m benchmark_cli diagnose --output-dir reports
```

### 18.2 第二步：接口冒烟

```bash
python3 -m benchmark_cli run \
  --plan smoke \
  --requests 3 \
  --concurrency 1 \
  --output-tokens 32 \
  --threshold 'error_rate:==:0' \
  --format json \
  --format html
```

### 18.3 第三步：单并发基线

```bash
python3 -m benchmark_cli run \
  --plan baseline \
  --requests 30 \
  --concurrency 1 \
  --seed 42 \
  --format json \
  --format jsonl.gz \
  --format html
```

### 18.4 第四步：固定并发

```bash
python3 -m benchmark_cli run \
  --plan fixed_concurrency \
  --requests 300 \
  --concurrency 20 \
  --seed 42 \
  --max-error-rate 0.10 \
  --max-p95-latency 8 \
  --threshold 'error_rate:<=:0.01' \
  --threshold 'latency.p95:<=:5' \
  --format json \
  --format jsonl.gz \
  --format events.jsonl.gz \
  --format html \
  --format xlsx \
  --format csv
```

### 18.5 第五步：阶梯容量

```bash
python3 -m benchmark_cli run \
  --plan stepped \
  --stage c1:1:20 \
  --stage c4:4:80 \
  --stage c8:8:160 \
  --stage c16:16:240 \
  --stage c32:32:320 \
  --cooldown 3 \
  --seed 42 \
  --max-error-rate 0.10 \
  --max-p95-latency 10 \
  --format json \
  --format events.jsonl.gz \
  --format html
```

### 18.6 第六步：模型顺序比较

```bash
python3 -m benchmark_cli compare \
  --target '模型A|http://10.0.0.21:8000/v1|model-a|MODEL_A_KEY' \
  --target '模型B|http://10.0.0.22:8000/v1|model-b|MODEL_B_KEY' \
  --resource-semantics independent \
  --comparison-mode sequential \
  --plan fixed_concurrency \
  --requests 100 \
  --concurrency 10 \
  --seed 42 \
  --format json \
  --format html
```

## 19. 常见错误与排查

### 缺少 `--base-url` 或 `--model`

检查命令参数、配置文件和环境变量：

```bash
printf '%s\n' "$LLM_BENCHMARK_BASE_URL"
printf '%s\n' "$LLM_BENCHMARK_MODEL"
```

### 401 或 403

- 确认 API Key 环境变量名和值；
- 确认目标服务是否要求 `Bearer` Token；
- 模型比较时确认每个 `--target` 的第四段是环境变量名，不是 API Key 本身。

### 404 或模型不存在

- Base URL 通常应以 `/v1` 结束；
- 确认模型名与服务端注册名称完全一致；
- 确认目标实现兼容 `/chat/completions`。

### 流式协议错误

先尝试小规模 `--non-stream`。如果非流式成功而流式失败，应检查服务端 SSE/流式 Chat Completions 兼容性，而不是直接扩大压测规模。

### TLS 证书错误

优先把正确 CA 或证书链部署到运行环境。只有临时内网验证时才使用 `--insecure`。

### 提示需要确认风险

检查并发、QPS 和时长。确认测试窗口和影响范围后增加 `--risk-confirmed`；如果参数填错，应降低负载而不是确认风险。

### 阈值显示 `not_evaluable`

常见原因：

- 请求数太少；
- 所有请求都失败；
- 非流式模式无法产生 TTFT；
- 服务端不返回 Token usage，相关 Token 指标不可用；
- 未配置断言却检查 `assertion_pass_rate`。

### 报告无法写入

```bash
python3 -m benchmark_cli diagnose --output-dir /目标目录
```

确认目录存在、磁盘空间足够且当前用户有写权限。

### 自动停止过于敏感

适当增加 `--stop-windows`、`--stop-minimum-samples` 或 `--stop-window-seconds`，避免单次尖峰触发停止。自动停止按 **同一指标连续超限** 判断，不会把不同指标的偶发超限相加。

### 同步比较被拒绝

同步模式必须同时提供：

```text
--comparison-mode synchronous
--confirm-synchronous
--resource-semantics independent|shared
```

执行前重新计算聚合并发和聚合 QPS，避免对共享模型服务造成意外冲击。

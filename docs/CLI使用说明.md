# LLM Benchmark CLI 使用说明

CLI 与 Web 服务相互独立，适用于服务器没有浏览器、Web 服务未启动或需要脚本化验证的场景。以下命令均不访问公网；只有实际压测命令会访问你指定的内网 OpenAI 兼容端点。

## 1. 启动方式

在项目目录并激活虚拟环境后，可使用任一形式：

```bash
llm-benchmark --help
python -m benchmark_cli --help
```

推荐通过环境变量提供凭据，避免 API Key 出现在 shell 历史和进程列表中：

```bash
export LLM_BENCHMARK_BASE_URL='http://10.0.0.20:8000/v1'
export LLM_BENCHMARK_MODEL='internal-model'
export LLM_BENCHMARK_API_KEY='replace-me'
```

## 2. 最小冒烟测试

```bash
python -m benchmark_cli run \
  --plan smoke \
  --base-url "$LLM_BENCHMARK_BASE_URL" \
  --model "$LLM_BENCHMARK_MODEL" \
  --requests 3 \
  --concurrency 1 \
  --output-tokens 32 \
  --format json \
  --format html
```

## 3. 六类测试计划

### 冒烟测试

```bash
python -m benchmark_cli run --plan smoke \
  --base-url "$LLM_BENCHMARK_BASE_URL" --model "$LLM_BENCHMARK_MODEL" \
  --requests 3 --concurrency 1
```

### 单并发基线

```bash
python -m benchmark_cli run --plan baseline \
  --base-url "$LLM_BENCHMARK_BASE_URL" --model "$LLM_BENCHMARK_MODEL" \
  --requests 30 --concurrency 1
```

### 固定并发

```bash
python -m benchmark_cli run --plan fixed_concurrency \
  --base-url "$LLM_BENCHMARK_BASE_URL" --model "$LLM_BENCHMARK_MODEL" \
  --requests 200 --concurrency 20
```

### 阶梯容量

```bash
python -m benchmark_cli run --plan stepped \
  --base-url "$LLM_BENCHMARK_BASE_URL" --model "$LLM_BENCHMARK_MODEL" \
  --stage warm:2:20 --stage normal:10:100 --stage peak:30:180 \
  --cooldown 3
```

阶段格式为 `名称:并发:请求数`；需要限制阶段到达率时使用 `名称:并发:请求数:QPS`。

### 恒定到达率

```bash
python -m benchmark_cli run --plan constant_rate \
  --base-url "$LLM_BENCHMARK_BASE_URL" --model "$LLM_BENCHMARK_MODEL" \
  --qps 10 --duration 60 --concurrency 30
```

### 稳定性测试

```bash
python -m benchmark_cli run --plan stability \
  --base-url "$LLM_BENCHMARK_BASE_URL" --model "$LLM_BENCHMARK_MODEL" \
  --duration 1800 --concurrency 8 --warmup 30 --cooldown 10
```

高并发、高 QPS 或长时间运行会要求额外提供 `--risk-confirmed`。这只确认风险，不会绕过硬上限。

## 4. 数据集和断言

校验 OpenAI messages JSONL：

```bash
python -m benchmark_cli validate-dataset assets/example.jsonl
```

JSONL 每行示例：

```json
{"id":"case-1","messages":[{"role":"user","content":"只回答 ok"}],"metadata":{"group":"smoke"}}
```

使用数据集和固定种子：

```bash
python -m benchmark_cli run --plan fixed_concurrency \
  --base-url "$LLM_BENCHMARK_BASE_URL" --model "$LLM_BENCHMARK_MODEL" \
  --dataset assets/example.jsonl --seed 42 --requests 100 --concurrency 10
```

断言文件是 JSON 数组，例如：

```json
[
  {"type":"non_empty"},
  {"type":"contains","value":"ok"},
  {"type":"finish_reason","value":["stop","length"]}
]
```

通过 `--assertions assertions.json` 使用。支持非空、Token 范围、finish reason、contains、regex、exact、JSON、JSON Schema 和响应字段断言。

## 5. 阈值、基线和自动停止

阈值格式为 `指标:运算符:数值`，可重复：

```bash
python -m benchmark_cli run --plan fixed_concurrency \
  --base-url "$LLM_BENCHMARK_BASE_URL" --model "$LLM_BENCHMARK_MODEL" \
  --requests 100 --concurrency 10 \
  --threshold 'latency.p95:<=:2.5' \
  --threshold 'error_rate:<=:0.01' \
  --threshold 'achieved_qps:>=:8'
```

阈值失败返回退出码 4。指标没有数据时状态为 `not_evaluable`，不会伪装成通过。

使用历史 JSON 摘要作为基线：

```bash
python -m benchmark_cli run ... --baseline reports/<task-id>.json
```

运行中持续自动停止：

```bash
python -m benchmark_cli run ... \
  --max-error-rate 0.10 --max-p95-latency 5 --stop-windows 3
```

## 6. 模型比较

目标格式为 `名称|Base URL|模型|API_KEY环境变量`。默认顺序运行，并对每个模型复用相同的数据集、种子和样本顺序：

```bash
export MODEL_A_KEY='key-a'
export MODEL_B_KEY='key-b'
python -m benchmark_cli compare \
  --target '模型A|http://10.0.0.21:8000/v1|model-a|MODEL_A_KEY' \
  --target '模型B|http://10.0.0.22:8000/v1|model-b|MODEL_B_KEY' \
  --resource-semantics independent \
  --plan fixed_concurrency --requests 100 --concurrency 10 --seed 42
```

同步比较必须显式确认总负载叠加：

```bash
python -m benchmark_cli compare \
  --target '模型A|http://10.0.0.20:8000/v1|model-a|MODEL_A_KEY' \
  --target '模型B|http://10.0.0.20:8000/v1|model-b|MODEL_B_KEY' \
  --resource-semantics shared \
  --comparison-mode synchronous --confirm-synchronous \
  --requests 100 --concurrency 10
```

共享资源模式的结果包含资源竞争效应，不应解释成两个独立端点的绝对容量。

## 7. 报告和机器可读输出

可重复使用 `--format`：

```bash
python -m benchmark_cli run ... \
  --output-dir reports \
  --format json --format jsonl.gz --format events.jsonl.gz \
  --format html --format xlsx --format csv
```

- JSON：版本化任务摘要和时间窗口。
- JSONL.GZ：逐请求样本。
- events.JSONL.GZ：任务事件和时间序列来源。
- HTML：无 CDN、字体或网络依赖的自包含离线报告。
- XLSX/CSV：按需导出；CSV 使用 UTF-8 BOM，便于 Windows Excel 打开。

脚本调用时增加 `--json --quiet`，标准输出仅保留 JSON，进度信息不会污染 stdout。

## 8. Ctrl+C 与退出码

第一次按 Ctrl+C 会触发协作式停止：不再调度新请求，等待已开始请求结束，并写出已有样本和部分报告。

| 退出码 | 含义 |
|---:|---|
| 0 | 成功 |
| 2 | 配置、参数、数据集或风险确认错误 |
| 3 | 执行失败，或所有已完成请求均失败 |
| 4 | 至少一个可评估阈值失败 |
| 130 | 用户中断，已尽量保存部分结果 |

## 9. 离线诊断

```bash
python -m benchmark_cli diagnose --output-dir reports
```

诊断只检查 Python、平台和输出目录可写性，明确跳过网络检测，不会访问互联网。

## 10. JSON 配置文件

```json
{
  "name": "nightly-capacity",
  "endpoint": {
    "base_url": "http://10.0.0.20:8000/v1",
    "model": "internal-model",
    "api_key_env": "LLM_BENCHMARK_API_KEY",
    "timeout": 60
  },
  "plan": {
    "type": "fixed_concurrency",
    "requests": 200,
    "concurrency": 20
  },
  "workload": {
    "prompt_type": "general",
    "input_size": "short",
    "output_tokens": 128,
    "seed": 42
  },
  "reports": {
    "output_dir": "reports",
    "formats": ["json", "jsonl.gz", "html"]
  }
}
```

运行：

```bash
python -m benchmark_cli run --config benchmark.json
```

CLI 参数优先于配置文件；API Key 配置只保存环境变量名，不建议写入明文凭据。

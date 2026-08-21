"""旧版单轮压测入口的兼容包装。

新代码应优先使用 ``python -m benchmark_cli run``。本模块保留原有
``run_benchmark`` 异步函数及命令行参数，供旧 Flask/Streamlit 调用方平滑迁移。
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any, Callable

from benchmark_core import (
    BenchmarkPlan,
    BenchmarkScheduler,
    EndpointConfig,
    OpenAIChatClient,
    PlanType,
    RequestConfig,
    WorkloadDimensions,
    build_summary,
    builtin_dataset,
    sample_records,
)

ProgressCallback = Callable[[int, int], object]


async def run_benchmark(
    num_requests: int,
    concurrency: int,
    request_timeout: float,
    output_tokens: int,
    llm_url: str,
    api_key: str,
    model: str,
    use_long_context: bool,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    """使用共享压测核心执行旧版固定并发测试，并返回旧字段结构。"""

    if num_requests <= 0:
        raise ValueError("num_requests 必须大于 0")
    if concurrency <= 0:
        raise ValueError("concurrency 必须大于 0")
    dimensions = WorkloadDimensions(
        prompt_type="general",
        input_size="long" if use_long_context else "short",
        output_size=output_tokens,
    )
    dataset = builtin_dataset(dimensions.prompt_type, dimensions.input_size)
    records, _ = sample_records(dataset, num_requests, seed=1, dimensions=dimensions)
    requests = [record.to_request(dimensions, stream=True) for record in records]
    plan = BenchmarkPlan(
        name="legacy-fixed-concurrency",
        plan_type=PlanType.FIXED_CONCURRENCY,
        concurrency=concurrency,
        total_requests=num_requests,
        metadata={"source": "legacy-wrapper"},
    )
    endpoint = EndpointConfig(
        base_url=llm_url,
        model=model,
        api_key=api_key,
        verify_tls=False,
        timeout_seconds=float(request_timeout),
    )

    async with OpenAIChatClient(
        endpoint,
        max_connections=max(10, concurrency + 10),
    ) as client:

        async def executor(request: RequestConfig, request_id: str, started: float):
            return await client.request(request, request_id=request_id, task_started_at=started)

        async def progress(event):
            if progress_callback and event.event_type == "request_completed":
                progress_callback(int(event.payload.get("completed", 0)), num_requests)

        result = await BenchmarkScheduler(executor).run(plan, requests, event_sink=progress)

    summary = build_summary("legacy", plan, result.samples, result.elapsed)
    return _legacy_result(summary, request_timeout, output_tokens, model, use_long_context)


def _legacy_result(summary, request_timeout, output_tokens, model, use_long_context):
    """把新摘要映射为旧 Web 所需的稳定字段。"""

    def stats(value):
        return {
            "average": value.mean,
            "p50": value.p50,
            "p95": value.p95,
            "p99": value.p99,
        }

    return {
        "total_requests": summary.total_requests,
        "successful_requests": summary.transport_successes,
        "concurrency": summary.plan.concurrency,
        "request_timeout": request_timeout,
        "max_output_tokens": output_tokens,
        "use_long_context": use_long_context,
        "model": model,
        "total_time": summary.elapsed,
        "requests_per_second": summary.achieved_qps or 0,
        "total_output_tokens": summary.total_output_tokens or 0,
        "latency": stats(summary.latency),
        "tokens_per_second": stats(summary.request_output_tps),
        "time_to_first_token": stats(summary.ttft),
    }


def print_results(results: dict[str, Any], output_format: str = "both") -> None:
    """保留旧版 JSON/文本输出行为。"""

    if output_format in {"json", "both"}:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    if output_format in {"line", "both"}:
        print(
            f"请求 {results['successful_requests']}/{results['total_requests']}，"
            f"并发 {results['concurrency']}，QPS {results['requests_per_second']:.2f}，"
            f"P95 延迟 {_format(results['latency']['p95'])} 秒"
        )


def _format(value: Any) -> str:
    return "N/A" if value is None else f"{float(value):.3f}"


def main() -> int:
    parser = argparse.ArgumentParser(description="兼容版 LLM 单轮压测入口")
    parser.add_argument("--num_requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--request_timeout", type=float, default=60)
    parser.add_argument("--output_tokens", type=int, default=128)
    parser.add_argument("--llm_url", required=True)
    parser.add_argument("--api_key", default="")
    parser.add_argument("--model", required=True)
    parser.add_argument("--use_long_context", action="store_true")
    parser.add_argument("--output_format", choices=["json", "line", "both"], default="both")
    args = parser.parse_args()
    result = asyncio.run(
        run_benchmark(
            args.num_requests,
            args.concurrency,
            args.request_timeout,
            args.output_tokens,
            args.llm_url,
            args.api_key,
            args.model,
            args.use_long_context,
        )
    )
    print_results(result, args.output_format)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

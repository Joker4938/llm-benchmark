"""将持久化任务 payload 转换为共享核心压测。"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence

from benchmark_core import (
    AssertionSpec,
    AssertionType,
    BenchmarkPlan,
    BenchmarkScheduler,
    CancellationToken,
    EndpointConfig,
    OpenAIChatClient,
    PlanType,
    RequestConfig,
    RequestSample,
    StageConfig,
    TimeWindowMetrics,
    WorkloadDimensions,
    aggregate_time_windows,
    apply_validation,
    build_summary,
    builtin_dataset,
    custom_dataset,
    load_jsonl,
    sample_records,
    to_jsonable,
    write_report_bundle,
)
from benchmark_core.redaction import redact

from .storage import Repository


def build_web_result_details(
    samples: Sequence[RequestSample],
    windows: Sequence[TimeWindowMetrics],
    *,
    failure_limit: int = 20,
) -> dict[str, Any]:
    """构建供 Web 历史详情使用的脱敏时间序列和失败样本。"""

    failed = [sample for sample in samples if _is_failed_sample(sample)]
    return {
        "time_series": to_jsonable(windows),
        "failed_sample_total": len(failed),
        "failed_samples": [_failed_sample_record(sample) for sample in failed[:failure_limit]],
    }


def _is_failed_sample(sample: RequestSample) -> bool:
    return bool(
        sample.error
        or not sample.transport_success
        or not sample.protocol_valid
        or sample.assertion_passed is False
    )


def _failed_sample_record(sample: RequestSample) -> dict[str, Any]:
    if sample.error:
        category = sample.error.category.value
        message = redact(sample.error.message)
        retryable = sample.error.retryable
    elif sample.assertion_passed is False:
        category = "assertion"
        message = "响应断言未通过"
        retryable = False
    elif not sample.protocol_valid:
        category = "protocol"
        message = "响应不符合 OpenAI Chat Completions 协议"
        retryable = False
    else:
        category = "transport"
        message = "请求未成功完成"
        retryable = True
    return {
        "request_id": sample.request_id,
        "started_at_offset": sample.started_at_offset,
        "status_code": sample.status_code,
        "category": category,
        "message": message,
        "retryable": retryable,
        "latency": sample.timing.latency if sample.timing else None,
        "ttft": sample.timing.ttft if sample.timing else None,
    }


class BenchmarkTaskRunner:
    """执行由 Web API 入队的 OpenAI 兼容压测任务。"""

    def __init__(self, repository: Repository, reports_dir: str | Path) -> None:
        self.repository = repository
        self.reports_dir = Path(reports_dir)

    async def __call__(
        self,
        payload: Mapping[str, Any],
        cancellation: CancellationToken,
        event_sink: Callable[[Any], Awaitable[None]],
    ) -> Mapping[str, Any]:
        endpoint_data = dict(payload.get("endpoint") or {})
        config_id = payload.get("api_config_id")
        if config_id:
            stored = self.repository.get_api_config(str(config_id), reveal_secret=True)
            endpoint_data = {
                "base_url": stored["base_url"], "model": stored["model"],
                "api_key": stored["api_key"], "verify_tls": stored["verify_tls"],
                "timeout_seconds": stored["timeout_seconds"], **endpoint_data,
            }
        endpoint = EndpointConfig(
            base_url=str(endpoint_data["base_url"]),
            model=str(endpoint_data["model"]),
            api_key=str(endpoint_data.get("api_key", "")),
            verify_tls=bool(endpoint_data.get("verify_tls", True)),
            timeout_seconds=float(endpoint_data.get("timeout_seconds", 60)),
        )
        plan_data = dict(payload.get("plan") or {})
        stages = tuple(
            StageConfig(
                str(item.get("name", f"stage-{index + 1}")), int(item["concurrency"]),
                item.get("requests"), item.get("duration_seconds"), item.get("target_qps"),
            )
            for index, item in enumerate(plan_data.get("stages", []))
        )
        plan = BenchmarkPlan(
            str(plan_data.get("name", "web-task")),
            PlanType(plan_data.get("plan_type", "fixed_concurrency")),
            int(plan_data.get("concurrency", 1)),
            plan_data.get("total_requests"), plan_data.get("duration_seconds"),
            plan_data.get("target_qps"), float(plan_data.get("warmup_seconds", 0)),
            float(plan_data.get("cooldown_seconds", 0)), stages,
            {"source": "web"},
        )
        workload = dict(payload.get("workload") or {})
        dimensions = WorkloadDimensions(
            str(workload.get("prompt_type", "general")),
            str(workload.get("input_size", "short")),
            int(workload.get("output_size", 128)),
        )
        dataset = self._dataset(workload, dimensions)
        count = int(plan.total_requests or max(1, len(dataset.records)))
        records, snapshot = sample_records(dataset, count, int(workload.get("seed", 1)), dimensions)
        requests = [record.to_request(dimensions, stream=bool(payload.get("stream", True))) for record in records]
        assertion_payload = list(payload.get("assertions") or [])
        assertions = tuple(
            AssertionSpec(
                AssertionType(item["type"]),
                item.get("value"),
                item.get("options", {}),
            )
            for item in assertion_payload
        )

        async with OpenAIChatClient(endpoint, max_connections=max(10, plan.concurrency + 10)) as client:
            async def execute(request: RequestConfig, request_id: str, started: float):
                sample = await client.request(request, request_id=request_id, task_started_at=started)
                return apply_validation(sample, assertions)[0]

            result = await BenchmarkScheduler(execute).run(
                plan, requests, cancellation=cancellation, event_sink=event_sink
            )
        run_id = uuid.uuid4().hex
        summary = build_summary(run_id, plan, result.samples, result.elapsed)
        windows = aggregate_time_windows(result.samples, total_elapsed=result.elapsed)
        formats = payload.get("formats") or ["json", "jsonl.gz"]
        artifacts = write_report_bundle(
            self.reports_dir, summary, result.samples, result.events, windows, formats
        )
        for artifact in artifacts:
            self.repository.add_report({
                "task_id": payload.get("task_id"), "format": artifact.format,
                "relative_path": artifact.relative_path, "size_bytes": artifact.size_bytes,
                "sha256": artifact.sha256,
            })
        value = summary.to_dict()
        value.update({
            "workload": {
                "dataset": dataset.name,
                "version": dataset.version,
                "sha256": dataset.sha256,
                "seed": snapshot.seed,
                "selected_record_ids": snapshot.selected_record_ids,
                "dimensions": {
                    "prompt_type": dimensions.prompt_type,
                    "input_size": dimensions.input_size,
                    "output_tokens": dimensions.output_size,
                },
            },
            "request": {"model": endpoint.model, "stream": bool(payload.get("stream", True))},
            "validation": {"assertions": assertion_payload},
            "artifacts": [
                {"format": item.format, "path": item.relative_path, "size_bytes": item.size_bytes, "sha256": item.sha256}
                for item in artifacts
            ],
            "stopped_reason": result.stopped_reason,
            **build_web_result_details(result.samples, windows),
        })
        return value

    def _dataset(self, workload: Mapping[str, Any], dimensions: WorkloadDimensions):
        if workload.get("dataset_id"):
            stored = self.repository.get_dataset(str(workload["dataset_id"]))
            if stored.get("content_jsonl"):
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".jsonl", delete=False) as handle:
                    handle.write(stored["content_jsonl"])
                    path = handle.name
                try:
                    return load_jsonl(path, name=stored["name"])
                finally:
                    Path(path).unlink(missing_ok=True)
        if workload.get("user_message"):
            return custom_dataset(workload.get("system_message"), str(workload["user_message"]))
        return builtin_dataset(dimensions.prompt_type, dimensions.input_size)

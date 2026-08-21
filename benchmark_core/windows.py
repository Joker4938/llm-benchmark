"""按固定时间窗口聚合运行指标。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .models import RequestSample
from .stats import describe


@dataclass(frozen=True, slots=True)
class TimeWindowMetrics:
    """一个左闭右开时间窗口的透明指标。"""

    index: int
    start_offset: float
    end_offset: float
    scheduled: int
    completed: int
    transport_successes: int
    valid_responses: int
    errors: int
    output_tokens: int | None
    achieved_qps: float
    aggregate_output_tps: float | None
    latency_p50: float | None
    latency_p95: float | None
    ttft_p95: float | None


def aggregate_time_windows(
    samples: Sequence[RequestSample],
    *,
    window_seconds: float = 1.0,
    total_elapsed: float | None = None,
) -> tuple[TimeWindowMetrics, ...]:
    """按请求完成时间聚合样本，不把缺失 Token 伪装成零。"""

    if window_seconds <= 0:
        raise ValueError("window_seconds 必须大于 0")
    if not samples and not total_elapsed:
        return ()
    completion_offsets = [
        sample.started_at_offset + (sample.timing.latency if sample.timing else 0.0)
        for sample in samples
    ]
    elapsed = total_elapsed if total_elapsed is not None else max(completion_offsets, default=0.0)
    window_count = max(1, int(elapsed // window_seconds) + (1 if elapsed % window_seconds else 0))
    buckets: list[list[RequestSample]] = [[] for _ in range(window_count)]
    for sample, completed_at in zip(samples, completion_offsets):
        index = min(int(completed_at // window_seconds), window_count - 1)
        buckets[index].append(sample)
    result: list[TimeWindowMetrics] = []
    for index, bucket in enumerate(buckets):
        tokens = [sample.token_usage.completion_tokens for sample in bucket if sample.token_usage.completion_tokens is not None]
        output_tokens = sum(tokens) if tokens else None
        latency = describe(sample.timing.latency if sample.timing else None for sample in bucket)
        ttft = describe(sample.timing.ttft if sample.timing else None for sample in bucket)
        result.append(TimeWindowMetrics(
            index=index,
            start_offset=index * window_seconds,
            end_offset=min((index + 1) * window_seconds, elapsed) if elapsed else window_seconds,
            scheduled=len(bucket),
            completed=len(bucket),
            transport_successes=sum(sample.transport_success for sample in bucket),
            valid_responses=sum(sample.protocol_valid for sample in bucket),
            errors=sum(sample.error is not None for sample in bucket),
            output_tokens=output_tokens,
            achieved_qps=len(bucket) / window_seconds,
            aggregate_output_tps=output_tokens / window_seconds if output_tokens is not None else None,
            latency_p50=latency.p50,
            latency_p95=latency.p95,
            ttft_p95=ttft.p95,
        ))
    return tuple(result)

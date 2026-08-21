"""请求样本到任务摘要的聚合。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from .models import BenchmarkPlan, BenchmarkSummary, RequestSample
from .stats import describe
from .tokens import dominant_token_source


def build_summary(
    task_id: str,
    plan: BenchmarkPlan,
    samples: Sequence[RequestSample],
    elapsed: float,
) -> BenchmarkSummary:
    """从完整或部分请求样本构建统一摘要。"""

    transport_successes = sum(sample.transport_success for sample in samples)
    valid_responses = sum(sample.protocol_valid for sample in samples)
    assertion_passes = sum(sample.assertion_passed is True for sample in samples)
    output_tokens = [
        sample.token_usage.completion_tokens
        for sample in samples
        if sample.token_usage.completion_tokens is not None
    ]
    total_output_tokens = sum(output_tokens) if output_tokens else None
    error_counts = Counter(
        sample.error.category.value for sample in samples if sample.error is not None
    )
    achieved_qps = len(samples) / elapsed if elapsed > 0 else None
    aggregate_output_tps = (
        total_output_tokens / elapsed
        if total_output_tokens is not None and elapsed > 0
        else None
    )
    return BenchmarkSummary(
        task_id=task_id,
        plan=plan,
        total_requests=plan.total_requests or len(samples),
        completed_requests=len(samples),
        transport_successes=transport_successes,
        valid_responses=valid_responses,
        assertion_passes=assertion_passes,
        elapsed=elapsed,
        achieved_qps=achieved_qps,
        aggregate_output_tps=aggregate_output_tps,
        total_output_tokens=total_output_tokens,
        token_source=dominant_token_source([sample.token_usage for sample in samples]),
        latency=describe(sample.timing.latency if sample.timing else None for sample in samples),
        ttft=describe(sample.timing.ttft if sample.timing else None for sample in samples),
        generation_duration=describe(
            sample.timing.generation_duration if sample.timing else None for sample in samples
        ),
        request_output_tps=describe(sample.output_tps for sample in samples),
        error_counts=dict(error_counts),
    )

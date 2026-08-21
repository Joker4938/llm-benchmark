"""透明的逐指标阈值与历史基线比较。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

from .models import BenchmarkSummary


class ThresholdStatus(str, Enum):
    """单项阈值的评估状态。"""

    PASSED = "passed"
    FAILED = "failed"
    NOT_EVALUABLE = "not_evaluable"


@dataclass(frozen=True, slots=True)
class ThresholdRule:
    """一个可解释的指标阈值。"""

    metric: str
    operator: str
    value: float


@dataclass(frozen=True, slots=True)
class ThresholdResult:
    """阈值评估结果。"""

    metric: str
    operator: str
    expected: float
    observed: float | None
    status: ThresholdStatus
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "operator": self.operator,
            "expected": self.expected,
            "observed": self.observed,
            "status": self.status.value,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class BaselineMetricDelta:
    """当前任务相对基线的单指标差异。"""

    metric: str
    current: float | None
    baseline: float | None
    absolute_delta: float | None
    percent_delta: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "current": self.current,
            "baseline": self.baseline,
            "absolute_delta": self.absolute_delta,
            "percent_delta": self.percent_delta,
        }


_SUPPORTED_OPERATORS = {"<=", ">=", "<", ">", "=="}
_DEFAULT_METRICS = (
    "latency.mean",
    "latency.p95",
    "ttft.mean",
    "ttft.p95",
    "achieved_qps",
    "aggregate_output_tps",
    "error_rate",
    "valid_response_rate",
    "assertion_pass_rate",
)


def parse_threshold(value: str) -> ThresholdRule:
    """解析 ``metric:operator:value`` 格式。"""

    parts = value.rsplit(":", 2)
    if len(parts) != 3 or parts[1] not in _SUPPORTED_OPERATORS:
        raise ValueError("threshold 格式必须为 指标:运算符:数值，例如 latency.p95:<=:2.5")
    metric = parts[0].strip()
    if not metric:
        raise ValueError("threshold 指标不能为空")
    try:
        expected = float(parts[2])
    except ValueError as exc:
        raise ValueError(f"threshold 数值无效: {parts[2]}") from exc
    return ThresholdRule(metric, parts[1], expected)


def summary_metrics(summary: BenchmarkSummary | Mapping[str, Any]) -> dict[str, float | None]:
    """将摘要转换为可用于阈值和基线比较的稳定指标名。"""

    data = summary.to_dict() if isinstance(summary, BenchmarkSummary) else dict(summary)
    completed = _number(data.get("completed_requests")) or 0.0
    successes = _number(data.get("transport_successes")) or 0.0
    valid = _number(data.get("valid_responses")) or 0.0
    assertion_passes = _number(data.get("assertion_passes")) or 0.0
    return {
        "latency.mean": _nested_number(data, "latency", "mean"),
        "latency.p50": _nested_number(data, "latency", "p50"),
        "latency.p90": _nested_number(data, "latency", "p90"),
        "latency.p95": _nested_number(data, "latency", "p95"),
        "latency.p99": _nested_number(data, "latency", "p99"),
        "ttft.mean": _nested_number(data, "ttft", "mean"),
        "ttft.p50": _nested_number(data, "ttft", "p50"),
        "ttft.p95": _nested_number(data, "ttft", "p95"),
        "ttft.p99": _nested_number(data, "ttft", "p99"),
        "generation_duration.mean": _nested_number(data, "generation_duration", "mean"),
        "request_output_tps.mean": _nested_number(data, "request_output_tps", "mean"),
        "achieved_qps": _number(data.get("achieved_qps")),
        "aggregate_output_tps": _number(data.get("aggregate_output_tps")),
        "error_rate": ((completed - successes) / completed) if completed else None,
        "valid_response_rate": (valid / completed) if completed else None,
        "assertion_pass_rate": (assertion_passes / completed) if completed else None,
    }


def evaluate_thresholds(
    summary: BenchmarkSummary | Mapping[str, Any],
    rules: Sequence[ThresholdRule],
) -> tuple[ThresholdResult, ...]:
    """逐项评估阈值，不把缺失指标误判为通过。"""

    metrics = summary_metrics(summary)
    results: list[ThresholdResult] = []
    for rule in rules:
        observed = metrics.get(rule.metric)
        if rule.metric not in metrics or observed is None:
            results.append(ThresholdResult(
                rule.metric, rule.operator, rule.value, None,
                ThresholdStatus.NOT_EVALUABLE,
                f"指标 {rule.metric} 当前不可评估",
            ))
            continue
        passed = _compare(observed, rule.operator, rule.value)
        status = ThresholdStatus.PASSED if passed else ThresholdStatus.FAILED
        results.append(ThresholdResult(
            rule.metric, rule.operator, rule.value, observed, status,
            f"{rule.metric}={observed:g} {rule.operator} {rule.value:g}",
        ))
    return tuple(results)


def compare_baseline(
    current: Mapping[str, Any],
    baseline: Mapping[str, Any],
    *,
    metrics: Sequence[str] = _DEFAULT_METRICS,
) -> dict[str, Any]:
    """比较两个摘要，并明确报告兼容性与逐指标差异。"""

    incompatibilities = _compatibility_issues(current, baseline)
    current_metrics = summary_metrics(current)
    baseline_metrics = summary_metrics(baseline)
    deltas: list[dict[str, Any]] = []
    for metric in metrics:
        now = current_metrics.get(metric)
        old = baseline_metrics.get(metric)
        absolute = now - old if now is not None and old is not None else None
        percent = (absolute / old * 100.0) if absolute is not None and old not in {None, 0} else None
        deltas.append(BaselineMetricDelta(metric, now, old, absolute, percent).to_dict())
    return {
        "compatible": not incompatibilities,
        "incompatibilities": incompatibilities,
        "metrics": deltas,
    }


def _compatibility_issues(current: Mapping[str, Any], baseline: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    current_plan = current.get("plan") if isinstance(current.get("plan"), Mapping) else {}
    baseline_plan = baseline.get("plan") if isinstance(baseline.get("plan"), Mapping) else {}
    for key in ("plan_type", "concurrency", "target_qps"):
        if current_plan.get(key) != baseline_plan.get(key):
            issues.append(f"plan.{key} 不一致")
    current_workload = current.get("workload") if isinstance(current.get("workload"), Mapping) else {}
    baseline_workload = baseline.get("workload") if isinstance(baseline.get("workload"), Mapping) else {}
    for key in ("sha256", "seed", "selected_record_ids"):
        if current_workload and baseline_workload and current_workload.get(key) != baseline_workload.get(key):
            issues.append(f"workload.{key} 不一致")
    return issues


def _compare(observed: float, operator: str, expected: float) -> bool:
    return {
        "<=": observed <= expected,
        ">=": observed >= expected,
        "<": observed < expected,
        ">": observed > expected,
        "==": observed == expected,
    }[operator]


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _nested_number(data: Mapping[str, Any], parent: str, key: str) -> float | None:
    value = data.get(parent)
    return _number(value.get(key)) if isinstance(value, Mapping) else None

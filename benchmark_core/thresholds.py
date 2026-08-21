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
    """当前任务相对基线的单指标差异与回归判定。"""

    metric: str
    current: float | None
    baseline: float | None
    absolute_delta: float | None
    percent_delta: float | None
    direction: str
    tolerance_percent: float | None
    tolerance_absolute: float
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "current": self.current,
            "baseline": self.baseline,
            "absolute_delta": self.absolute_delta,
            "percent_delta": self.percent_delta,
            "direction": self.direction,
            "tolerance_percent": self.tolerance_percent,
            "tolerance_absolute": self.tolerance_absolute,
            "status": self.status,
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

_LOWER_IS_BETTER = frozenset({
    "latency.mean", "latency.p50", "latency.p90", "latency.p95", "latency.p99",
    "ttft.mean", "ttft.p50", "ttft.p95", "ttft.p99", "generation_duration.mean",
    "error_rate",
})
_RATE_METRICS = frozenset({"error_rate", "valid_response_rate", "assertion_pass_rate"})
_DEFAULT_TOLERANCE_PERCENT = 5.0
_DEFAULT_RATE_TOLERANCE_ABSOLUTE = 0.01


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
    tolerances: Mapping[str, Mapping[str, float | None] | float] | None = None,
) -> dict[str, Any]:
    """比较两个摘要，并明确报告兼容性、逐指标差异和回归结论。"""

    incompatibilities = _compatibility_issues(current, baseline)
    compatible = not incompatibilities
    current_metrics = summary_metrics(current)
    baseline_metrics = summary_metrics(baseline)
    deltas: list[dict[str, Any]] = []
    counts = {"regressed": 0, "improved": 0, "stable": 0, "not_evaluable": 0}
    for metric in metrics:
        now = current_metrics.get(metric)
        old = baseline_metrics.get(metric)
        absolute = now - old if now is not None and old is not None else None
        percent = (absolute / old * 100.0) if absolute is not None and old not in {None, 0} else None
        direction = "lower_is_better" if metric in _LOWER_IS_BETTER else "higher_is_better"
        tolerance_percent, tolerance_absolute = _baseline_tolerance(metric, tolerances)
        status = _baseline_metric_status(
            compatible, now, old, absolute, direction,
            tolerance_percent, tolerance_absolute,
        )
        if status in counts:
            counts[status] += 1
        deltas.append(BaselineMetricDelta(
            metric, now, old, absolute, percent, direction,
            tolerance_percent, tolerance_absolute, status,
        ).to_dict())
    if not compatible:
        conclusion = "incompatible"
    elif counts["regressed"]:
        conclusion = "regressed"
    elif counts["improved"]:
        conclusion = "improved"
    elif counts["not_evaluable"] == len(deltas):
        conclusion = "not_evaluable"
    else:
        conclusion = "stable"
    return {
        "compatible": compatible,
        "incompatibilities": incompatibilities,
        "conclusion": conclusion,
        "counts": counts,
        "metrics": deltas,
    }


def _baseline_tolerance(
    metric: str,
    tolerances: Mapping[str, Mapping[str, float | None] | float] | None,
) -> tuple[float | None, float]:
    configured = tolerances.get(metric) if tolerances else None
    default_absolute = _DEFAULT_RATE_TOLERANCE_ABSOLUTE if metric in _RATE_METRICS else 0.0
    if isinstance(configured, Mapping):
        percent = _number(configured.get("percent"))
        absolute = _number(configured.get("absolute"))
        return (max(0.0, percent) if percent is not None else None), max(0.0, absolute if absolute is not None else default_absolute)
    if configured is not None:
        percent = _number(configured)
        return (max(0.0, percent) if percent is not None else None), default_absolute
    return _DEFAULT_TOLERANCE_PERCENT, default_absolute


def _baseline_metric_status(
    compatible: bool,
    current: float | None,
    baseline: float | None,
    absolute_delta: float | None,
    direction: str,
    tolerance_percent: float | None,
    tolerance_absolute: float,
) -> str:
    if not compatible or current is None or baseline is None or absolute_delta is None:
        return "not_evaluable"
    threshold = tolerance_absolute
    if tolerance_percent is not None:
        threshold = max(threshold, abs(baseline) * tolerance_percent / 100.0)
    adverse_delta = absolute_delta if direction == "lower_is_better" else -absolute_delta
    if adverse_delta > threshold:
        return "regressed"
    if adverse_delta < -threshold:
        return "improved"
    return "stable"


def _compatibility_issues(current: Mapping[str, Any], baseline: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    if current.get("schema_version") != baseline.get("schema_version"):
        issues.append("schema_version 不一致")
    current_plan = current.get("plan") if isinstance(current.get("plan"), Mapping) else {}
    baseline_plan = baseline.get("plan") if isinstance(baseline.get("plan"), Mapping) else {}
    for key in (
        "plan_type", "concurrency", "total_requests", "duration_seconds", "target_qps",
        "warmup_seconds", "cooldown_seconds", "stages",
    ):
        _append_mismatch(issues, f"plan.{key}", current_plan, baseline_plan, key)
    current_workload = current.get("workload") if isinstance(current.get("workload"), Mapping) else {}
    baseline_workload = baseline.get("workload") if isinstance(baseline.get("workload"), Mapping) else {}
    if current_workload or baseline_workload:
        for key in ("sha256", "seed", "selected_record_ids"):
            _append_mismatch(issues, f"workload.{key}", current_workload, baseline_workload, key)
        current_dimensions = current_workload.get("dimensions") if isinstance(current_workload.get("dimensions"), Mapping) else {}
        baseline_dimensions = baseline_workload.get("dimensions") if isinstance(baseline_workload.get("dimensions"), Mapping) else {}
        for key in ("prompt_type", "input_size"):
            _append_mismatch(issues, f"workload.dimensions.{key}", current_dimensions, baseline_dimensions, key)
        _append_alias_mismatch(
            issues, "workload.dimensions.output_tokens", current_dimensions, baseline_dimensions,
            ("output_tokens", "output_size"),
        )
    current_request = current.get("request") if isinstance(current.get("request"), Mapping) else {}
    baseline_request = baseline.get("request") if isinstance(baseline.get("request"), Mapping) else {}
    if current_request or baseline_request:
        for key in ("model", "stream"):
            _append_mismatch(issues, f"request.{key}", current_request, baseline_request, key)
    return issues


def _append_mismatch(
    issues: list[str], label: str,
    current: Mapping[str, Any], baseline: Mapping[str, Any], key: str,
) -> None:
    if key not in current or key not in baseline:
        issues.append(f"{label} 缺失，无法确认兼容性")
    elif current.get(key) != baseline.get(key):
        issues.append(f"{label} 不一致")


def _append_alias_mismatch(
    issues: list[str], label: str,
    current: Mapping[str, Any], baseline: Mapping[str, Any], aliases: Sequence[str],
) -> None:
    current_value = next((current[key] for key in aliases if key in current), None)
    baseline_value = next((baseline[key] for key in aliases if key in baseline), None)
    if not any(key in current for key in aliases) or not any(key in baseline for key in aliases):
        issues.append(f"{label} 缺失，无法确认兼容性")
    elif current_value != baseline_value:
        issues.append(f"{label} 不一致")


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

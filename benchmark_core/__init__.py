"""LLM Benchmark 共享压测核心。"""

from .client import OpenAIChatClient, consume_stream, parse_completion
from .models import (
    BenchmarkEvent,
    BenchmarkPlan,
    BenchmarkSummary,
    EndpointConfig,
    ErrorCategory,
    MetricStats,
    PlanType,
    ReportArtifact,
    RequestConfig,
    RequestError,
    RequestSample,
    StageConfig,
    TaskPhase,
    TimingMetrics,
    TokenSource,
    TokenUsage,
)
from .stats import describe, percentile
from .summary import build_summary

__all__ = [
    "BenchmarkEvent",
    "BenchmarkPlan",
    "BenchmarkSummary",
    "EndpointConfig",
    "ErrorCategory",
    "MetricStats",
    "OpenAIChatClient",
    "PlanType",
    "ReportArtifact",
    "RequestConfig",
    "RequestError",
    "RequestSample",
    "StageConfig",
    "TaskPhase",
    "TimingMetrics",
    "TokenSource",
    "TokenUsage",
    "build_summary",
    "consume_stream",
    "describe",
    "parse_completion",
    "percentile",
]

from .validation import AssertionSpec, AssertionType, ValidationResult, apply_validation, validate_sample
from .workloads import (
    Dataset,
    WorkloadDimensions,
    WorkloadRecord,
    WorkloadSnapshot,
    builtin_dataset,
    custom_dataset,
    load_jsonl,
    sample_records,
)

__all__ += [
    "AssertionSpec", "AssertionType", "ValidationResult", "apply_validation", "validate_sample",
    "Dataset", "WorkloadDimensions", "WorkloadRecord", "WorkloadSnapshot",
    "builtin_dataset", "custom_dataset", "load_jsonl", "sample_records",
]

from .scheduler import (
    AutoStopEvaluation,
    AutoStopEvaluator,
    AutoStopPolicy,
    AutoStopTrigger,
    AutoStopWindow,
    BenchmarkScheduler,
    CancellationToken,
    GeneratorResourceUsage,
    ProcessResourceProbe,
    RunResult,
    SafetyLimits,
    validate_plan,
)
__all__ += [
    "AutoStopEvaluation",
    "AutoStopEvaluator",
    "AutoStopPolicy",
    "AutoStopTrigger",
    "AutoStopWindow",
    "BenchmarkScheduler",
    "CancellationToken",
    "GeneratorResourceUsage",
    "ProcessResourceProbe",
    "RunResult",
    "SafetyLimits",
    "validate_plan",
]

from .windows import TimeWindowMetrics, aggregate_time_windows
__all__ += ["TimeWindowMetrics", "aggregate_time_windows"]

from .reporting import to_jsonable, write_report_bundle
__all__ += ["to_jsonable", "write_report_bundle"]

from .thresholds import (
    BaselineMetricDelta, ThresholdResult, ThresholdRule, ThresholdStatus,
    compare_baseline, evaluate_thresholds, parse_threshold, summary_metrics,
)
__all__ += [
    "BaselineMetricDelta", "ThresholdResult", "ThresholdRule", "ThresholdStatus",
    "compare_baseline", "evaluate_thresholds", "parse_threshold", "summary_metrics",
]

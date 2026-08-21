"""压测核心的版本化领域模型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "1.0"


class PlanType(str, Enum):
    """首期支持的性能测试计划。"""

    SMOKE = "smoke"
    BASELINE = "baseline"
    FIXED_CONCURRENCY = "fixed_concurrency"
    STEPPED = "stepped"
    CONSTANT_RATE = "constant_rate"
    STABILITY = "stability"


class TokenSource(str, Enum):
    """Token 数量的来源。"""

    SERVER = "server"
    ESTIMATED = "estimated"
    UNAVAILABLE = "unavailable"


class ErrorCategory(str, Enum):
    """稳定的请求失败分类。"""

    TIMEOUT = "timeout"
    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    SERVER = "server"
    CLIENT = "client"
    TRANSPORT = "transport"
    PROTOCOL = "protocol"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class TaskPhase(str, Enum):
    """任务运行阶段。"""

    CONNECTING = "connecting"
    WARMUP = "warmup"
    LOAD = "load"
    COOLDOWN = "cooldown"
    FINISHED = "finished"


@dataclass(frozen=True, slots=True)
class EndpointConfig:
    """OpenAI 兼容端点配置。"""

    base_url: str
    model: str
    api_key: str = ""
    verify_tls: bool = True
    timeout_seconds: float = 60.0
    extra_headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RequestConfig:
    """单次 Chat Completions 请求配置。"""

    messages: Sequence[Mapping[str, Any]]
    max_output_tokens: int = 128
    stream: bool = True
    temperature: float | None = None
    top_p: float | None = None
    seed: int | None = None
    extra_body: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StageConfig:
    """阶梯测试中的一个阶段。"""

    name: str
    concurrency: int
    requests: int | None = None
    duration_seconds: float | None = None
    target_qps: float | None = None


@dataclass(frozen=True, slots=True)
class BenchmarkPlan:
    """可序列化的压测计划快照。"""

    name: str
    plan_type: PlanType
    concurrency: int = 1
    total_requests: int | None = None
    duration_seconds: float | None = None
    target_qps: float | None = None
    warmup_seconds: float = 0.0
    cooldown_seconds: float = 0.0
    stages: Sequence[StageConfig] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class TimingMetrics:
    """单次请求的单调时钟计时结果，单位为秒。"""

    latency: float
    ttft: float | None
    generation_duration: float | None


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """输入/输出 Token 统计及其来源。"""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    source: TokenSource = TokenSource.UNAVAILABLE
    estimator: str | None = None


@dataclass(frozen=True, slots=True)
class RequestError:
    """对用户安全的结构化请求错误。"""

    category: ErrorCategory
    message: str
    status_code: int | None = None
    retryable: bool = False
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RequestSample:
    """一次真实请求的完整结果。"""

    request_id: str
    started_at_offset: float
    timing: TimingMetrics | None
    token_usage: TokenUsage
    content: str = ""
    finish_reason: str | None = None
    transport_success: bool = False
    protocol_valid: bool = False
    assertion_passed: bool | None = None
    error: RequestError | None = None
    status_code: int | None = None
    output_tps: float | None = None
    phase: TaskPhase = TaskPhase.LOAD
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class BenchmarkEvent:
    """任务进度与实时图表使用的持久化事件。"""

    sequence: int
    elapsed: float
    phase: TaskPhase
    event_type: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class MetricStats:
    """一个数值指标的描述统计。"""

    count: int
    minimum: float | None
    maximum: float | None
    mean: float | None
    stddev: float | None
    p50: float | None
    p90: float | None
    p95: float | None
    p99: float | None


@dataclass(frozen=True, slots=True)
class BenchmarkSummary:
    """一次压测的版本化摘要。"""

    task_id: str
    plan: BenchmarkPlan
    total_requests: int
    completed_requests: int
    transport_successes: int
    valid_responses: int
    assertion_passes: int
    elapsed: float
    achieved_qps: float | None
    aggregate_output_tps: float | None
    total_output_tokens: int | None
    token_source: TokenSource
    latency: MetricStats
    ttft: MetricStats
    generation_duration: MetricStats
    request_output_tps: MetricStats
    error_counts: Mapping[str, int] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        """返回仅包含 JSON 兼容基础类型的字典。"""

        return _normalise(asdict(self))


@dataclass(frozen=True, slots=True)
class ReportArtifact:
    """报告制品索引。"""

    format: str
    relative_path: str
    size_bytes: int
    sha256: str
    schema_version: str = SCHEMA_VERSION


def _normalise(value: Any) -> Any:
    """递归转换枚举与不可变容器，便于 JSON 序列化。"""

    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _normalise(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalise(item) for item in value]
    return value

"""持续窗口自动停止策略、评估与生成器资源采集。"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass

from typing import Mapping

@dataclass(frozen=True, slots=True)
class AutoStopPolicy:
    """持续窗口自动停止阈值，未配置的指标不会参与判定。"""

    max_error_rate: float | None = None
    max_p95_latency: float | None = None
    max_queue_backlog: int | None = None
    max_queue_growth: int | None = None
    max_cpu_percent: float | None = None
    max_memory_mb: float | None = None
    max_event_loop_lag: float | None = None
    consecutive_windows: int = 3
    minimum_samples: int = 10
    window_seconds: float = 1.0

    def __post_init__(self) -> None:
        if self.max_error_rate is not None and not 0 <= self.max_error_rate <= 1:
            raise ValueError("max_error_rate 必须在 0 到 1 之间")
        non_negative = {
            "max_p95_latency": self.max_p95_latency,
            "max_queue_backlog": self.max_queue_backlog,
            "max_queue_growth": self.max_queue_growth,
            "max_cpu_percent": self.max_cpu_percent,
            "max_memory_mb": self.max_memory_mb,
            "max_event_loop_lag": self.max_event_loop_lag,
        }
        for name, value in non_negative.items():
            if value is not None and value < 0:
                raise ValueError(f"{name} 不能为负数")
        if self.consecutive_windows < 1:
            raise ValueError("consecutive_windows 必须大于 0")
        if self.minimum_samples < 1:
            raise ValueError("minimum_samples 必须大于 0")
        if self.window_seconds <= 0:
            raise ValueError("window_seconds 必须大于 0")

    @property
    def has_conditions(self) -> bool:
        return any(
            value is not None
            for value in (
                self.max_error_rate,
                self.max_p95_latency,
                self.max_queue_backlog,
                self.max_queue_growth,
                self.max_cpu_percent,
                self.max_memory_mb,
                self.max_event_loop_lag,
            )
        )

    @property
    def has_runtime_conditions(self) -> bool:
        return any(
            value is not None
            for value in (
                self.max_queue_backlog,
                self.max_queue_growth,
                self.max_cpu_percent,
                self.max_memory_mb,
                self.max_event_loop_lag,
            )
        )


@dataclass(frozen=True, slots=True)
class AutoStopWindow:
    """一个可独立审计的自动停止评估窗口。"""

    index: int
    source: str
    sample_count: int = 0
    error_rate: float | None = None
    p95_latency: float | None = None
    queue_backlog: int | None = None
    queue_growth: int | None = None
    generator_cpu_percent: float | None = None
    generator_memory_mb: float | None = None
    event_loop_lag: float | None = None

    def metrics(self) -> dict[str, float | int | None]:
        return {
            "sample_count": self.sample_count,
            "error_rate": self.error_rate,
            "p95_latency": self.p95_latency,
            "queue_backlog": self.queue_backlog,
            "queue_growth": self.queue_growth,
            "generator_cpu_percent": self.generator_cpu_percent,
            "generator_memory_mb": self.generator_memory_mb,
            "event_loop_lag": self.event_loop_lag,
        }


@dataclass(frozen=True, slots=True)
class AutoStopTrigger:
    """触发自动停止的具体指标、观测值和阈值。"""

    metric: str
    source: str
    observed: float
    threshold: float
    consecutive_windows: int
    window_index: int
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "metric": self.metric,
            "source": self.source,
            "observed": self.observed,
            "threshold": self.threshold,
            "consecutive_windows": self.consecutive_windows,
            "window_index": self.window_index,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class AutoStopEvaluation:
    """单个窗口的判定结果，用于事件记录与触发决策。"""

    window: AutoStopWindow
    violations: tuple[str, ...]
    streaks: Mapping[str, int]
    trigger: AutoStopTrigger | None = None


class AutoStopEvaluator:
    """逐指标追踪连续违规窗口，避免不同指标或瞬时尖峰相互叠加。"""

    def __init__(self, policy: AutoStopPolicy) -> None:
        self.policy = policy
        self._streaks: dict[str, int] = {}

    def evaluate(self, window: AutoStopWindow) -> AutoStopEvaluation:
        violations: list[str] = []
        trigger: AutoStopTrigger | None = None
        for metric, observed, threshold in self._conditions(window):
            if observed is None:
                if self._belongs_to_source(metric, window.source):
                    self._streaks[metric] = 0
                continue
            if observed > threshold:
                violations.append(metric)
                self._streaks[metric] = self._streaks.get(metric, 0) + 1
            else:
                self._streaks[metric] = 0
            streak = self._streaks[metric]
            if trigger is None and streak >= self.policy.consecutive_windows:
                trigger = AutoStopTrigger(
                    metric=metric,
                    source=window.source,
                    observed=float(observed),
                    threshold=float(threshold),
                    consecutive_windows=streak,
                    window_index=window.index,
                    reason=self._reason(metric, float(observed), float(threshold), streak),
                )
        return AutoStopEvaluation(
            window=window,
            violations=tuple(violations),
            streaks=dict(self._streaks),
            trigger=trigger,
        )

    @staticmethod
    def _belongs_to_source(metric: str, source: str) -> bool:
        sample_metrics = {"error_rate", "p95_latency"}
        return (source == "samples" and metric in sample_metrics) or (
            source == "runtime" and metric not in sample_metrics
        )

    def _conditions(
        self, window: AutoStopWindow
    ) -> tuple[tuple[str, float | int | None, float | int], ...]:
        values = (
            ("error_rate", window.error_rate, self.policy.max_error_rate),
            ("p95_latency", window.p95_latency, self.policy.max_p95_latency),
            ("queue_backlog", window.queue_backlog, self.policy.max_queue_backlog),
            ("queue_growth", window.queue_growth, self.policy.max_queue_growth),
            (
                "generator_cpu_percent",
                window.generator_cpu_percent,
                self.policy.max_cpu_percent,
            ),
            (
                "generator_memory_mb",
                window.generator_memory_mb,
                self.policy.max_memory_mb,
            ),
            ("event_loop_lag", window.event_loop_lag, self.policy.max_event_loop_lag),
        )
        return tuple((metric, observed, threshold) for metric, observed, threshold in values if threshold is not None)

    @staticmethod
    def _reason(metric: str, observed: float, threshold: float, streak: int) -> str:
        if metric == "error_rate":
            detail = f"错误率 {observed:.2%} 超过 {threshold:.2%}"
        elif metric == "p95_latency":
            detail = f"P95 延迟 {observed:.3f}s 超过 {threshold:.3f}s"
        elif metric == "queue_backlog":
            detail = f"队列积压 {observed:.0f} 超过 {threshold:.0f}"
        elif metric == "queue_growth":
            detail = f"队列单窗口增长 {observed:.0f} 超过 {threshold:.0f}"
        elif metric == "generator_cpu_percent":
            detail = f"生成器 CPU {observed:.1f}% 超过 {threshold:.1f}%"
        elif metric == "generator_memory_mb":
            detail = f"生成器内存 {observed:.1f}MB 超过 {threshold:.1f}MB"
        else:
            detail = f"事件循环延迟 {observed:.3f}s 超过 {threshold:.3f}s"
        return f"{detail}，已连续 {streak} 个窗口"


@dataclass(frozen=True, slots=True)
class GeneratorResourceUsage:
    """当前压测生成器进程的轻量资源观测。"""

    cpu_percent: float | None = None
    memory_mb: float | None = None


class ProcessResourceProbe:
    """仅使用标准库采集进程 CPU 与内存，运行时无需外部依赖。"""

    def __init__(self, *, clock=time.perf_counter, cpu_clock=time.process_time) -> None:
        self.clock = clock
        self.cpu_clock = cpu_clock
        self._wall = clock()
        self._cpu = cpu_clock()

    def __call__(self) -> GeneratorResourceUsage:
        wall = self.clock()
        cpu = self.cpu_clock()
        wall_delta = wall - self._wall
        cpu_percent = None if wall_delta <= 0 else max(0.0, (cpu - self._cpu) / wall_delta * 100)
        self._wall = wall
        self._cpu = cpu
        return GeneratorResourceUsage(cpu_percent, _process_memory_mb())


def _process_memory_mb() -> float | None:
    try:
        with open("/proc/self/statm", encoding="ascii") as handle:
            resident_pages = int(handle.read().split()[1])
        return resident_pages * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)
    except (OSError, ValueError, IndexError):
        pass
    try:
        import resource

        value = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        if sys.platform != "darwin":
            value *= 1024
        return value / (1024 * 1024)
    except (ImportError, OSError, ValueError):
        return None

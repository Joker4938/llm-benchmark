"""六类单机压测计划的异步调度与安全控制。"""

from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Awaitable, Callable, Sequence

from .models import BenchmarkEvent, BenchmarkPlan, PlanType, RequestConfig, RequestSample, StageConfig, TaskPhase
from .stats import percentile

RequestExecutor = Callable[[RequestConfig, str, float], Awaitable[RequestSample]]
EventSink = Callable[[BenchmarkEvent], object]


@dataclass(frozen=True, slots=True)
class SafetyLimits:
    max_concurrency: int = 500
    max_qps: float = 1000.0
    max_duration_seconds: float = 86400.0
    max_requests: int = 1_000_000
    max_output_tokens: int = 32768
    risk_concurrency: int = 100
    risk_qps: float = 100.0
    risk_duration_seconds: float = 3600.0


@dataclass(frozen=True, slots=True)
class AutoStopPolicy:
    max_error_rate: float | None = None
    max_p95_latency: float | None = None
    consecutive_windows: int = 3
    minimum_samples: int = 10


@dataclass(slots=True)
class CancellationToken:
    _event: asyncio.Event = field(default_factory=asyncio.Event)

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> None:
        await self._event.wait()


@dataclass(frozen=True, slots=True)
class RunResult:
    samples: tuple[RequestSample, ...]
    warmup_samples: tuple[RequestSample, ...]
    events: tuple[BenchmarkEvent, ...]
    elapsed: float
    stopped_reason: str | None = None


def validate_plan(
    plan: BenchmarkPlan,
    request_configs: Sequence[RequestConfig],
    limits: SafetyLimits,
    *,
    risk_confirmed: bool = False,
) -> list[str]:
    """校验硬限制并返回需要显式确认的风险说明。"""

    concurrencies = [plan.concurrency, *(stage.concurrency for stage in plan.stages)]
    if max(concurrencies, default=1) > limits.max_concurrency:
        raise ValueError(f"并发数超过安全上限 {limits.max_concurrency}")
    qps_values = [value for value in [plan.target_qps, *(stage.target_qps for stage in plan.stages)] if value]
    if qps_values and max(qps_values) > limits.max_qps:
        raise ValueError(f"QPS 超过安全上限 {limits.max_qps}")
    request_values = [value for value in [plan.total_requests, *(stage.requests for stage in plan.stages)] if value]
    if request_values and sum(request_values if plan.stages else request_values[:1]) > limits.max_requests:
        raise ValueError(f"请求数超过安全上限 {limits.max_requests}")
    durations = [value for value in [plan.duration_seconds, *(stage.duration_seconds for stage in plan.stages)] if value]
    if durations and sum(durations if plan.stages else durations[:1]) > limits.max_duration_seconds:
        raise ValueError(f"时长超过安全上限 {limits.max_duration_seconds} 秒")
    if any(config.max_output_tokens > limits.max_output_tokens for config in request_configs):
        raise ValueError(f"输出 Token 超过安全上限 {limits.max_output_tokens}")
    risks: list[str] = []
    if max(concurrencies, default=1) > limits.risk_concurrency:
        risks.append("高并发")
    if qps_values and max(qps_values) > limits.risk_qps:
        risks.append("高 QPS")
    if durations and sum(durations if plan.stages else durations[:1]) > limits.risk_duration_seconds:
        risks.append("长时间运行")
    if risks and not risk_confirmed:
        raise PermissionError("需要确认风险: " + "、".join(risks))
    return risks


class BenchmarkScheduler:
    """按照计划调度请求，并产生可持久化阶段事件。"""

    def __init__(self, executor: RequestExecutor, *, clock=time.perf_counter, sleep=asyncio.sleep):
        self.executor = executor
        self.clock = clock
        self.sleep = sleep

    async def run(
        self,
        plan: BenchmarkPlan,
        requests: Sequence[RequestConfig],
        *,
        cancellation: CancellationToken | None = None,
        event_sink: EventSink | None = None,
        auto_stop: AutoStopPolicy | None = None,
    ) -> RunResult:
        if not requests:
            raise ValueError("至少需要一个请求配置")
        cancellation = cancellation or CancellationToken()
        started = self.clock()
        events: list[BenchmarkEvent] = []
        sequence = 0

        async def emit(phase: TaskPhase, event_type: str, **payload: object) -> None:
            nonlocal sequence
            sequence += 1
            event = BenchmarkEvent(sequence, max(0.0, self.clock() - started), phase, event_type, payload)
            events.append(event)
            if event_sink:
                result = event_sink(event)
                if inspect.isawaitable(result):
                    await result

        warmup_samples: list[RequestSample] = []
        samples: list[RequestSample] = []
        stopped_reason: str | None = None
        stop_evaluator = _StopEvaluator(auto_stop) if auto_stop else None

        async def evaluate_stop(current_samples: Sequence[RequestSample]) -> None:
            nonlocal stopped_reason
            if stop_evaluator is None or stopped_reason is not None:
                return
            reason = stop_evaluator.evaluate(current_samples)
            if reason:
                stopped_reason = reason
                cancellation.cancel()
                await emit(TaskPhase.LOAD, "automatic_stop", reason=reason)
        await emit(TaskPhase.CONNECTING, "plan_started", plan_type=plan.plan_type.value)
        if plan.warmup_seconds > 0 and not cancellation.cancelled:
            await emit(TaskPhase.WARMUP, "phase_started")
            warmup_plan = replace(plan, total_requests=None, duration_seconds=plan.warmup_seconds, concurrency=max(1, plan.concurrency))
            warmup_samples.extend(await self._fixed(warmup_plan, requests, cancellation, started, TaskPhase.WARMUP, emit, None))
        await emit(TaskPhase.LOAD, "phase_started")
        if not cancellation.cancelled:
            if plan.plan_type is PlanType.STEPPED:
                for index, stage in enumerate(plan.stages):
                    if cancellation.cancelled:
                        break
                    await emit(TaskPhase.LOAD, "stage_started", stage=index, name=stage.name, concurrency=stage.concurrency)
                    stage_plan = replace(plan, concurrency=stage.concurrency, total_requests=stage.requests, duration_seconds=stage.duration_seconds, target_qps=stage.target_qps)
                    batch = await (self._constant_rate(stage_plan, requests, cancellation, started, emit, evaluate_stop) if stage.target_qps else self._fixed(stage_plan, requests, cancellation, started, TaskPhase.LOAD, emit, evaluate_stop))
                    samples.extend(batch)
                    await emit(TaskPhase.LOAD, "stage_finished", stage=index, completed=len(batch))
            elif plan.plan_type is PlanType.CONSTANT_RATE:
                samples.extend(await self._constant_rate(plan, requests, cancellation, started, emit, evaluate_stop))
            else:
                effective = plan
                if plan.plan_type is PlanType.SMOKE:
                    effective = replace(plan, concurrency=1, total_requests=min(plan.total_requests or 1, 3))
                elif plan.plan_type is PlanType.BASELINE:
                    effective = replace(plan, concurrency=1)
                samples.extend(await self._fixed(effective, requests, cancellation, started, TaskPhase.LOAD, emit, evaluate_stop))
        if plan.cooldown_seconds > 0:
            await emit(TaskPhase.COOLDOWN, "phase_started")
            await self.sleep(plan.cooldown_seconds)
        await emit(TaskPhase.FINISHED, "plan_finished", cancelled=cancellation.cancelled)
        return RunResult(tuple(samples), tuple(warmup_samples), tuple(events), max(0.0, self.clock() - started), stopped_reason)

    async def _fixed(self, plan, requests, cancellation, started, phase, emit, evaluate_stop):
        count = plan.total_requests
        deadline = self.clock() + plan.duration_seconds if plan.duration_seconds else None
        queue: asyncio.Queue[int] = asyncio.Queue()
        if count is not None:
            for index in range(count):
                queue.put_nowait(index)
        results: list[RequestSample] = []
        next_index = 0
        lock = asyncio.Lock()

        async def worker():
            nonlocal next_index
            while not cancellation.cancelled:
                async with lock:
                    if deadline is not None:
                        if self.clock() >= deadline:
                            return
                        index = next_index
                        next_index += 1
                    else:
                        if queue.empty():
                            return
                        index = queue.get_nowait()
                sample = await self.executor(requests[index % len(requests)], str(uuid.uuid4()), started)
                results.append(sample)
                await emit(phase, "request_completed", completed=len(results), active=0)
                if evaluate_stop is not None:
                    await evaluate_stop(results)

        await asyncio.gather(*(worker() for _ in range(max(1, plan.concurrency))))
        return results

    async def _constant_rate(self, plan, requests, cancellation, started, emit, evaluate_stop):
        if not plan.target_qps or plan.target_qps <= 0:
            raise ValueError("恒定到达率计划需要 target_qps")
        total = plan.total_requests
        deadline = self.clock() + plan.duration_seconds if plan.duration_seconds else None
        semaphore = asyncio.Semaphore(max(1, plan.concurrency))
        tasks: list[asyncio.Task[RequestSample]] = []
        completed: list[RequestSample] = []
        index = 0
        next_arrival = self.clock()

        async def one(item_index):
            async with semaphore:
                sample = await self.executor(requests[item_index % len(requests)], str(uuid.uuid4()), started)
                completed.append(sample)
                if evaluate_stop is not None:
                    await evaluate_stop(completed)
                return sample

        while not cancellation.cancelled and (total is None or index < total) and (deadline is None or self.clock() < deadline):
            delay = next_arrival - self.clock()
            if delay > 0:
                await self.sleep(delay)
            tasks.append(asyncio.create_task(one(index)))
            index += 1
            next_arrival += 1 / plan.target_qps
            await emit(TaskPhase.LOAD, "request_scheduled", scheduled=index, active=sum(not task.done() for task in tasks))
        return list(await asyncio.gather(*tasks)) if tasks else []



class _StopEvaluator:
    """按不重叠窗口确认持续违规，避免瞬时尖峰误停。"""

    def __init__(self, policy: AutoStopPolicy) -> None:
        self.policy = policy
        self._evaluated = 0
        self._consecutive = 0

    def evaluate(self, samples: Sequence[RequestSample]) -> str | None:
        size = max(1, self.policy.minimum_samples)
        if len(samples) - self._evaluated < size:
            return None
        window = samples[self._evaluated:self._evaluated + size]
        self._evaluated += size
        reason = self._window_reason(window)
        self._consecutive = self._consecutive + 1 if reason else 0
        if reason and self._consecutive >= max(1, self.policy.consecutive_windows):
            return reason
        return None

    def _window_reason(self, samples: Sequence[RequestSample]) -> str | None:
        if self.policy.max_error_rate is not None:
            rate = sum(not sample.transport_success for sample in samples) / len(samples)
            if rate > self.policy.max_error_rate:
                return f"错误率 {rate:.2%} 持续超过 {self.policy.max_error_rate:.2%}"
        if self.policy.max_p95_latency is not None:
            p95 = percentile([sample.timing.latency for sample in samples if sample.timing], 95)
            if p95 is not None and p95 > self.policy.max_p95_latency:
                return f"P95 延迟 {p95:.3f}s 持续超过 {self.policy.max_p95_latency:.3f}s"
        return None

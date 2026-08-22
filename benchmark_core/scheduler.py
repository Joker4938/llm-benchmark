"""六类单机压测计划的异步调度与安全控制。"""

from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Awaitable, Callable, Sequence

from .autostop import (
    AutoStopEvaluation,
    AutoStopEvaluator,
    AutoStopPolicy,
    AutoStopTrigger,
    AutoStopWindow,
    GeneratorResourceUsage,
    ProcessResourceProbe,
)
from .models import (
    BenchmarkEvent,
    BenchmarkPlan,
    PlanType,
    RequestConfig,
    RequestSample,
    TaskPhase,
)
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
    stop_trigger: AutoStopTrigger | None = None


@dataclass(slots=True)
class _RuntimeState:
    scheduled: int = 0
    completed: int = 0
    active: int = 0

    @property
    def backlog(self) -> int:
        return max(0, self.scheduled - self.completed - self.active)


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
    qps_values = [
        value
        for value in [plan.target_qps, *(stage.target_qps for stage in plan.stages)]
        if value
    ]
    if qps_values and max(qps_values) > limits.max_qps:
        raise ValueError(f"QPS 超过安全上限 {limits.max_qps}")
    request_values = [
        value
        for value in [plan.total_requests, *(stage.requests for stage in plan.stages)]
        if value
    ]
    if request_values and sum(request_values if plan.stages else request_values[:1]) > limits.max_requests:
        raise ValueError(f"请求数超过安全上限 {limits.max_requests}")
    durations = [
        value
        for value in [plan.duration_seconds, *(stage.duration_seconds for stage in plan.stages)]
        if value
    ]
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

    def __init__(
        self,
        executor: RequestExecutor,
        *,
        clock=time.perf_counter,
        sleep=asyncio.sleep,
        resource_probe: Callable[[], GeneratorResourceUsage] | None = None,
    ):
        self.executor = executor
        self.clock = clock
        self.sleep = sleep
        self.resource_probe = resource_probe or ProcessResourceProbe(clock=clock)

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
            event = BenchmarkEvent(
                sequence,
                max(0.0, self.clock() - started),
                phase,
                event_type,
                payload,
            )
            events.append(event)
            if event_sink:
                result = event_sink(event)
                if inspect.isawaitable(result):
                    await result

        warmup_samples: list[RequestSample] = []
        samples: list[RequestSample] = []
        stopped_reason: str | None = None
        stop_trigger: AutoStopTrigger | None = None
        stop_evaluator = (
            AutoStopEvaluator(auto_stop)
            if auto_stop is not None and auto_stop.has_conditions
            else None
        )
        runtime = _RuntimeState()
        evaluation_lock = asyncio.Lock()
        sample_cursor = 0
        sample_window_index = 0

        async def apply_evaluation(evaluation: AutoStopEvaluation) -> None:
            nonlocal stopped_reason, stop_trigger
            await emit(
                TaskPhase.LOAD,
                "auto_stop_window",
                source=evaluation.window.source,
                window_index=evaluation.window.index,
                metrics=evaluation.window.metrics(),
                violations=list(evaluation.violations),
                streaks=dict(evaluation.streaks),
                required_consecutive_windows=auto_stop.consecutive_windows,
            )
            if evaluation.trigger is not None and stop_trigger is None:
                stop_trigger = evaluation.trigger
                stopped_reason = evaluation.trigger.reason
                cancellation.cancel()
                await emit(
                    TaskPhase.LOAD,
                    "automatic_stop",
                    reason=stopped_reason,
                    condition=evaluation.trigger.to_dict(),
                )

        async def evaluate_samples(current_samples: Sequence[RequestSample]) -> None:
            nonlocal sample_cursor, sample_window_index
            if stop_evaluator is None or auto_stop is None or stop_trigger is not None:
                return
            async with evaluation_lock:
                size = auto_stop.minimum_samples
                while len(current_samples) - sample_cursor >= size and stop_trigger is None:
                    window_samples = current_samples[sample_cursor:sample_cursor + size]
                    sample_cursor += size
                    latencies = [
                        sample.timing.latency
                        for sample in window_samples
                        if sample.timing is not None
                    ]
                    window = AutoStopWindow(
                        index=sample_window_index,
                        source="samples",
                        sample_count=len(window_samples),
                        error_rate=(
                            sum(not sample.transport_success for sample in window_samples)
                            / len(window_samples)
                        ),
                        p95_latency=percentile(latencies, 95),
                    )
                    sample_window_index += 1
                    await apply_evaluation(stop_evaluator.evaluate(window))

        async def monitor_runtime() -> None:
            if stop_evaluator is None or auto_stop is None:
                return
            previous_backlog = 0
            window_index = 0
            expected = self.clock() + auto_stop.window_seconds
            while not cancellation.cancelled:
                await self.sleep(auto_stop.window_seconds)
                observed_at = self.clock()
                if cancellation.cancelled:
                    break
                try:
                    usage = self.resource_probe()
                except Exception:
                    usage = GeneratorResourceUsage()
                backlog = runtime.backlog
                window = AutoStopWindow(
                    index=window_index,
                    source="runtime",
                    queue_backlog=backlog,
                    queue_growth=backlog - previous_backlog,
                    generator_cpu_percent=usage.cpu_percent,
                    generator_memory_mb=usage.memory_mb,
                    event_loop_lag=max(0.0, observed_at - expected),
                )
                previous_backlog = backlog
                window_index += 1
                expected = observed_at + auto_stop.window_seconds
                async with evaluation_lock:
                    if stop_trigger is None:
                        await apply_evaluation(stop_evaluator.evaluate(window))

        await emit(TaskPhase.CONNECTING, "plan_started", plan_type=plan.plan_type.value)
        if plan.warmup_seconds > 0 and not cancellation.cancelled:
            await emit(TaskPhase.WARMUP, "phase_started")
            warmup_plan = replace(
                plan,
                total_requests=None,
                duration_seconds=plan.warmup_seconds,
                concurrency=max(1, plan.concurrency),
            )
            warmup_samples.extend(
                await self._fixed(
                    warmup_plan,
                    requests,
                    cancellation,
                    started,
                    TaskPhase.WARMUP,
                    emit,
                    None,
                    _RuntimeState(),
                )
            )
        await emit(TaskPhase.LOAD, "phase_started")
        monitor_task = None
        if (
            stop_evaluator is not None
            and auto_stop is not None
            and auto_stop.has_runtime_conditions
            and not cancellation.cancelled
        ):
            monitor_task = asyncio.create_task(monitor_runtime())
        try:
            if not cancellation.cancelled:
                if plan.plan_type is PlanType.STEPPED:
                    for index, stage in enumerate(plan.stages):
                        if cancellation.cancelled:
                            break
                        await emit(
                            TaskPhase.LOAD,
                            "stage_started",
                            stage=index,
                            name=stage.name,
                            concurrency=stage.concurrency,
                        )
                        stage_plan = replace(
                            plan,
                            concurrency=stage.concurrency,
                            total_requests=stage.requests,
                            duration_seconds=stage.duration_seconds,
                            target_qps=stage.target_qps,
                        )
                        if stage.target_qps:
                            batch = await self._constant_rate(
                                stage_plan,
                                requests,
                                cancellation,
                                started,
                                emit,
                                evaluate_samples,
                                runtime,
                            )
                        else:
                            batch = await self._fixed(
                                stage_plan,
                                requests,
                                cancellation,
                                started,
                                TaskPhase.LOAD,
                                emit,
                                evaluate_samples,
                                runtime,
                            )
                        samples.extend(batch)
                        await emit(
                            TaskPhase.LOAD,
                            "stage_finished",
                            stage=index,
                            completed=len(batch),
                        )
                elif plan.plan_type is PlanType.CONSTANT_RATE:
                    samples.extend(
                        await self._constant_rate(
                            plan,
                            requests,
                            cancellation,
                            started,
                            emit,
                            evaluate_samples,
                            runtime,
                        )
                    )
                else:
                    effective = plan
                    if plan.plan_type is PlanType.SMOKE:
                        effective = replace(
                            plan,
                            concurrency=1,
                            total_requests=min(plan.total_requests or 1, 3),
                        )
                    elif plan.plan_type is PlanType.BASELINE:
                        effective = replace(plan, concurrency=1)
                    samples.extend(
                        await self._fixed(
                            effective,
                            requests,
                            cancellation,
                            started,
                            TaskPhase.LOAD,
                            emit,
                            evaluate_samples,
                            runtime,
                        )
                    )
        finally:
            if monitor_task is not None:
                monitor_task.cancel()
                await asyncio.gather(monitor_task, return_exceptions=True)
        if plan.cooldown_seconds > 0 and not cancellation.cancelled:
            await emit(TaskPhase.COOLDOWN, "phase_started")
            await self.sleep(plan.cooldown_seconds)
        await emit(
            TaskPhase.FINISHED,
            "plan_finished",
            cancelled=cancellation.cancelled,
            stopped_reason=stopped_reason,
        )
        return RunResult(
            tuple(samples),
            tuple(warmup_samples),
            tuple(events),
            max(0.0, self.clock() - started),
            stopped_reason,
            stop_trigger,
        )

    async def _fixed(
        self,
        plan,
        requests,
        cancellation,
        started,
        phase,
        emit,
        evaluate_stop,
        runtime,
    ):
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
                    runtime.scheduled += 1
                runtime.active += 1
                try:
                    sample = await self.executor(
                        requests[index % len(requests)],
                        str(uuid.uuid4()),
                        started,
                    )
                finally:
                    runtime.active -= 1
                results.append(sample)
                runtime.completed += 1
                await emit(
                    phase,
                    "request_completed",
                    completed=len(results),
                    active=runtime.active,
                )
                if evaluate_stop is not None:
                    await evaluate_stop(results)

        await asyncio.gather(*(worker() for _ in range(max(1, plan.concurrency))))
        return results

    async def _constant_rate(
        self,
        plan,
        requests,
        cancellation,
        started,
        emit,
        evaluate_stop,
        runtime,
    ):
        if not plan.target_qps or plan.target_qps <= 0:
            raise ValueError("恒定到达率计划需要 target_qps")
        total = plan.total_requests
        deadline = self.clock() + plan.duration_seconds if plan.duration_seconds else None
        semaphore = asyncio.Semaphore(max(1, plan.concurrency))
        tasks: list[asyncio.Task[RequestSample | None]] = []
        completed: list[RequestSample] = []
        index = 0
        next_arrival = self.clock()

        async def one(item_index):
            async with semaphore:
                if cancellation.cancelled:
                    return None
                runtime.active += 1
                try:
                    sample = await self.executor(
                        requests[item_index % len(requests)],
                        str(uuid.uuid4()),
                        started,
                    )
                finally:
                    runtime.active -= 1
                completed.append(sample)
                runtime.completed += 1
                if evaluate_stop is not None:
                    await evaluate_stop(completed)
                return sample

        while (
            not cancellation.cancelled
            and (total is None or index < total)
            and (deadline is None or self.clock() < deadline)
        ):
            delay = next_arrival - self.clock()
            if delay > 0:
                await self.sleep(delay)
            if cancellation.cancelled:
                break
            runtime.scheduled += 1
            tasks.append(asyncio.create_task(one(index)))
            index += 1
            next_arrival += 1 / plan.target_qps
            await emit(
                TaskPhase.LOAD,
                "request_scheduled",
                scheduled=index,
                active=runtime.active,
                backlog=runtime.backlog,
            )
        gathered = await asyncio.gather(*tasks) if tasks else []
        return [sample for sample in gathered if sample is not None]

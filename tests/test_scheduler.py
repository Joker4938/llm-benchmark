import asyncio
import unittest

from benchmark_core.models import (
    BenchmarkPlan,
    PlanType,
    RequestConfig,
    RequestSample,
    StageConfig,
    TimingMetrics,
    TokenUsage,
)
from benchmark_core.scheduler import (
    AutoStopEvaluator,
    AutoStopPolicy,
    AutoStopWindow,
    BenchmarkScheduler,
    CancellationToken,
    SafetyLimits,
    validate_plan,
)


def config():
    return RequestConfig([{"role": "user", "content": "x"}], max_output_tokens=10)


class FakeExecutor:
    def __init__(self, success=True, delay=0):
        self.calls = 0
        self.success = success
        self.delay = delay

    async def __call__(self, request, request_id, started):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return RequestSample(
            request_id=request_id,
            started_at_offset=0,
            timing=TimingMetrics(0.01, 0.002, 0.008),
            token_usage=TokenUsage(completion_tokens=4),
            content="ok" if self.success else "",
            transport_success=self.success,
            protocol_valid=self.success,
            output_tps=500,
        )


class SequenceExecutor(FakeExecutor):
    def __init__(self, outcomes, delay=0):
        super().__init__(delay=delay)
        self.outcomes = list(outcomes)

    async def __call__(self, request, request_id, started):
        self.success = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        return await super().__call__(request, request_id, started)


class SafetyTests(unittest.TestCase):
    def test_hard_limit_rejected(self):
        plan = BenchmarkPlan("x", PlanType.FIXED_CONCURRENCY, concurrency=11, total_requests=1)
        with self.assertRaisesRegex(ValueError, "并发数"):
            validate_plan(plan, [config()], SafetyLimits(max_concurrency=10))

    def test_risk_requires_confirmation(self):
        plan = BenchmarkPlan("x", PlanType.FIXED_CONCURRENCY, concurrency=5, total_requests=1)
        with self.assertRaises(PermissionError):
            validate_plan(plan, [config()], SafetyLimits(risk_concurrency=2))
        self.assertEqual(
            ["高并发"],
            validate_plan(
                plan,
                [config()],
                SafetyLimits(risk_concurrency=2),
                risk_confirmed=True,
            ),
        )


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_smoke_is_bounded(self):
        executor = FakeExecutor()
        result = await BenchmarkScheduler(executor).run(
            BenchmarkPlan("smoke", PlanType.SMOKE, concurrency=10, total_requests=100), [config()]
        )
        self.assertEqual(3, len(result.samples))
        self.assertEqual("connecting", result.events[0].phase.value)
        self.assertEqual("finished", result.events[-1].phase.value)

    async def test_warmup_is_excluded_from_primary_samples(self):
        executor = FakeExecutor(delay=0.001)
        result = await BenchmarkScheduler(executor).run(
            BenchmarkPlan("baseline", PlanType.BASELINE, total_requests=2, warmup_seconds=0.003), [config()]
        )
        self.assertEqual(2, len(result.samples))
        self.assertGreater(len(result.warmup_samples), 0)

    async def test_stepped_plan_emits_stage_boundaries(self):
        result = await BenchmarkScheduler(FakeExecutor()).run(
            BenchmarkPlan(
                "step", PlanType.STEPPED,
                stages=(StageConfig("one", 1, requests=2), StageConfig("two", 2, requests=3)),
            ), [config()]
        )
        self.assertEqual(5, len(result.samples))
        self.assertEqual(2, sum(event.event_type == "stage_started" for event in result.events))

    async def test_constant_rate_schedules_requested_count(self):
        result = await BenchmarkScheduler(FakeExecutor()).run(
            BenchmarkPlan("rate", PlanType.CONSTANT_RATE, concurrency=2, total_requests=4, target_qps=1000), [config()]
        )
        self.assertEqual(4, len(result.samples))
        self.assertEqual(4, sum(event.event_type == "request_scheduled" for event in result.events))

    async def test_stability_uses_duration_boundary(self):
        result = await BenchmarkScheduler(FakeExecutor(delay=0.001)).run(
            BenchmarkPlan("stable", PlanType.STABILITY, concurrency=2, duration_seconds=0.005), [config()]
        )
        self.assertGreater(len(result.samples), 0)

    async def test_sustained_auto_stop_cancels_new_scheduling(self):
        result = await BenchmarkScheduler(FakeExecutor(success=False, delay=0.0001)).run(
            BenchmarkPlan("load", PlanType.FIXED_CONCURRENCY, concurrency=1, total_requests=50),
            [config()], auto_stop=AutoStopPolicy(max_error_rate=0.5, minimum_samples=2, consecutive_windows=2)
        )
        self.assertLess(len(result.samples), 50)
        self.assertIsNotNone(result.stopped_reason)
        self.assertTrue(any(event.event_type == "automatic_stop" for event in result.events))

    async def test_single_error_spike_does_not_stop_the_run(self):
        executor = SequenceExecutor([False, False, True, True, True, True])
        result = await BenchmarkScheduler(executor).run(
            BenchmarkPlan("load", PlanType.FIXED_CONCURRENCY, concurrency=1, total_requests=6),
            [config()],
            auto_stop=AutoStopPolicy(
                max_error_rate=0.5,
                minimum_samples=2,
                consecutive_windows=2,
            ),
        )
        self.assertEqual(6, len(result.samples))
        self.assertIsNone(result.stop_trigger)

    async def test_queue_backlog_sustained_windows_stop_new_arrivals(self):
        result = await BenchmarkScheduler(FakeExecutor(delay=0.02)).run(
            BenchmarkPlan(
                "rate",
                PlanType.CONSTANT_RATE,
                concurrency=1,
                total_requests=100,
                target_qps=1000,
            ),
            [config()],
            auto_stop=AutoStopPolicy(
                max_queue_backlog=2,
                consecutive_windows=2,
                window_seconds=0.005,
            ),
        )
        self.assertLess(len(result.samples), 100)
        self.assertEqual("queue_backlog", result.stop_trigger.metric)
        event = next(item for item in result.events if item.event_type == "automatic_stop")
        self.assertEqual("queue_backlog", event.payload["condition"]["metric"])

    async def test_pre_cancelled_token_runs_no_requests(self):
        token = CancellationToken()
        token.cancel()
        result = await BenchmarkScheduler(FakeExecutor()).run(
            BenchmarkPlan("load", PlanType.FIXED_CONCURRENCY, total_requests=5), [config()], cancellation=token
        )
        self.assertEqual(0, len(result.samples))


class AutoStopEvaluatorTests(unittest.TestCase):
    def test_different_rule_spikes_do_not_share_a_streak(self):
        evaluator = AutoStopEvaluator(
            AutoStopPolicy(
                max_cpu_percent=80,
                max_memory_mb=100,
                consecutive_windows=2,
            )
        )

        first = evaluator.evaluate(
            AutoStopWindow(0, "runtime", generator_cpu_percent=90, generator_memory_mb=50)
        )
        second = evaluator.evaluate(
            AutoStopWindow(1, "runtime", generator_cpu_percent=70, generator_memory_mb=120)
        )
        third = evaluator.evaluate(
            AutoStopWindow(2, "runtime", generator_cpu_percent=70, generator_memory_mb=120)
        )

        self.assertIsNone(first.trigger)
        self.assertIsNone(second.trigger)
        self.assertEqual("generator_memory_mb", third.trigger.metric)
        self.assertEqual(2, third.trigger.consecutive_windows)

    def test_event_loop_lag_records_observed_and_threshold_values(self):
        evaluator = AutoStopEvaluator(
            AutoStopPolicy(max_event_loop_lag=0.1, consecutive_windows=1)
        )
        evaluation = evaluator.evaluate(
            AutoStopWindow(0, "runtime", event_loop_lag=0.25)
        )

        self.assertEqual("event_loop_lag", evaluation.trigger.metric)
        self.assertEqual(0.25, evaluation.trigger.observed)
        self.assertEqual(0.1, evaluation.trigger.threshold)


if __name__ == "__main__":
    unittest.main()

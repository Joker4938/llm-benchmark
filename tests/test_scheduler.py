import asyncio
import unittest

from benchmark_core.models import BenchmarkPlan, PlanType, RequestConfig, RequestSample, TimingMetrics, TokenUsage, StageConfig
from benchmark_core.scheduler import AutoStopPolicy, BenchmarkScheduler, CancellationToken, SafetyLimits, validate_plan


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


class SafetyTests(unittest.TestCase):
    def test_hard_limit_rejected(self):
        plan = BenchmarkPlan("x", PlanType.FIXED_CONCURRENCY, concurrency=11, total_requests=1)
        with self.assertRaisesRegex(ValueError, "并发数"):
            validate_plan(plan, [config()], SafetyLimits(max_concurrency=10))

    def test_risk_requires_confirmation(self):
        plan = BenchmarkPlan("x", PlanType.FIXED_CONCURRENCY, concurrency=5, total_requests=1)
        with self.assertRaises(PermissionError):
            validate_plan(plan, [config()], SafetyLimits(risk_concurrency=2))
        self.assertEqual(["高并发"], validate_plan(plan, [config()], SafetyLimits(risk_concurrency=2), risk_confirmed=True))


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

    async def test_pre_cancelled_token_runs_no_requests(self):
        token = CancellationToken()
        token.cancel()
        result = await BenchmarkScheduler(FakeExecutor()).run(
            BenchmarkPlan("load", PlanType.FIXED_CONCURRENCY, total_requests=5), [config()], cancellation=token
        )
        self.assertEqual(0, len(result.samples))


if __name__ == "__main__":
    unittest.main()

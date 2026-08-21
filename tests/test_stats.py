import unittest

from benchmark_core.models import BenchmarkPlan, PlanType, RequestSample, TimingMetrics, TokenSource, TokenUsage
from benchmark_core.stats import describe, percentile
from benchmark_core.summary import build_summary


class StatsTests(unittest.TestCase):
    def test_percentiles_are_normal_ascending_percentiles(self):
        values = [1, 2, 3, 4, 100]
        self.assertEqual(3.0, percentile(values, 50))
        self.assertGreater(percentile(values, 95), percentile(values, 90))
        self.assertGreater(percentile(values, 99), percentile(values, 95))

    def test_describe_returns_none_for_empty_metric(self):
        stats = describe([None])
        self.assertEqual(0, stats.count)
        self.assertIsNone(stats.mean)
        self.assertIsNone(stats.p99)

    def test_describe_includes_all_required_statistics(self):
        stats = describe([1.0, 2.0, 3.0])
        self.assertEqual(3, stats.count)
        self.assertEqual(1.0, stats.minimum)
        self.assertEqual(3.0, stats.maximum)
        self.assertEqual(2.0, stats.mean)
        self.assertAlmostEqual((2 / 3) ** 0.5, stats.stddev)
        self.assertEqual(2.0, stats.p50)

    def test_summary_uses_generation_tps_and_server_token_source(self):
        plan = BenchmarkPlan("test", PlanType.FIXED_CONCURRENCY, total_requests=1)
        sample = RequestSample(
            request_id="r1",
            started_at_offset=0,
            timing=TimingMetrics(latency=3.0, ttft=1.0, generation_duration=2.0),
            token_usage=TokenUsage(completion_tokens=20, total_tokens=25, source=TokenSource.SERVER),
            transport_success=True,
            protocol_valid=True,
            output_tps=10.0,
        )
        summary = build_summary("task", plan, [sample], elapsed=4.0)
        self.assertEqual(10.0, summary.request_output_tps.mean)
        self.assertEqual(5.0, summary.aggregate_output_tps)
        self.assertEqual(TokenSource.SERVER, summary.token_source)
        self.assertEqual("fixed_concurrency", summary.to_dict()["plan"]["plan_type"])


if __name__ == "__main__":
    unittest.main()

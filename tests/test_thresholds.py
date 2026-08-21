import unittest

from benchmark_core import (
    BenchmarkPlan,
    PlanType,
    RequestSample,
    ThresholdStatus,
    TimingMetrics,
    TokenSource,
    TokenUsage,
    build_summary,
    compare_baseline,
    evaluate_thresholds,
    parse_threshold,
)


class ThresholdTests(unittest.TestCase):
    def setUp(self):
        plan = BenchmarkPlan("threshold", PlanType.FIXED_CONCURRENCY, concurrency=2, total_requests=2)
        samples = (
            RequestSample("1", 0, TimingMetrics(1.0, 0.2, 0.8), TokenUsage(1, 8, 9, TokenSource.SERVER), transport_success=True, protocol_valid=True, assertion_passed=True),
            RequestSample("2", 0, TimingMetrics(2.0, 0.3, 1.7), TokenUsage(1, 10, 11, TokenSource.SERVER), transport_success=False),
        )
        self.summary = build_summary("threshold-task", plan, samples, 2.0)

    def test_parse_and_evaluate_pass_fail_and_not_evaluable(self):
        rules = (
            parse_threshold("latency.p95:<=:2.0"),
            parse_threshold("error_rate:<:0.2"),
            parse_threshold("unknown.metric:>=:1"),
        )
        results = evaluate_thresholds(self.summary, rules)
        self.assertEqual(ThresholdStatus.PASSED, results[0].status)
        self.assertEqual(ThresholdStatus.FAILED, results[1].status)
        self.assertEqual(ThresholdStatus.NOT_EVALUABLE, results[2].status)

    def test_baseline_compatibility_and_deltas(self):
        current = self.summary.to_dict()
        baseline = self.summary.to_dict()
        result = compare_baseline(current, baseline)
        self.assertTrue(result["compatible"])
        self.assertEqual(0.0, result["metrics"][0]["absolute_delta"])
        baseline["plan"]["concurrency"] = 1
        result = compare_baseline(current, baseline)
        self.assertFalse(result["compatible"])
        self.assertIn("plan.concurrency 不一致", result["incompatibilities"])

    def test_invalid_threshold_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_threshold("latency.p95=2")


if __name__ == "__main__":
    unittest.main()

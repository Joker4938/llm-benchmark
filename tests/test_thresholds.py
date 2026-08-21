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

    def test_rate_thresholds_use_completed_requests_and_report_observed_values(self):
        rules = (
            parse_threshold("error_rate:<=:0.5"),
            parse_threshold("valid_response_rate:>=:0.5"),
            parse_threshold("assertion_pass_rate:>:0.5"),
        )

        results = evaluate_thresholds(self.summary, rules)

        self.assertEqual([0.5, 0.5, 0.5], [result.observed for result in results])
        self.assertEqual(
            [ThresholdStatus.PASSED, ThresholdStatus.PASSED, ThresholdStatus.FAILED],
            [result.status for result in results],
        )

    def test_empty_run_rates_are_not_evaluable(self):
        summary = self.summary.to_dict()
        summary.update({
            "completed_requests": 0,
            "transport_successes": 0,
            "valid_responses": 0,
            "assertion_passes": 0,
        })
        results = evaluate_thresholds(summary, (
            parse_threshold("error_rate:<=:0.1"),
            parse_threshold("valid_response_rate:>=:0.9"),
            parse_threshold("assertion_pass_rate:>=:0.9"),
        ))

        self.assertTrue(all(result.observed is None for result in results))
        self.assertTrue(all(result.status is ThresholdStatus.NOT_EVALUABLE for result in results))

    def test_baseline_compatibility_and_deltas(self):
        current = self.summary.to_dict()
        baseline = self.summary.to_dict()
        result = compare_baseline(current, baseline)
        self.assertTrue(result["compatible"])
        self.assertEqual("stable", result["conclusion"])
        self.assertEqual(0.0, result["metrics"][0]["absolute_delta"])
        baseline["plan"]["concurrency"] = 1
        result = compare_baseline(current, baseline)
        self.assertFalse(result["compatible"])
        self.assertEqual("incompatible", result["conclusion"])
        self.assertIn("plan.concurrency 不一致", result["incompatibilities"])
        self.assertTrue(all(item["status"] == "not_evaluable" for item in result["metrics"]))

    def test_baseline_regression_uses_metric_direction_and_tolerance(self):
        baseline = self.summary.to_dict()
        current = self.summary.to_dict()
        baseline.update({
            "completed_requests": 100,
            "transport_successes": 100,
            "valid_responses": 100,
            "assertion_passes": 100,
            "achieved_qps": 100.0,
            "aggregate_output_tps": 100.0,
        })
        current.update({
            "completed_requests": 100,
            "transport_successes": 98,
            "valid_responses": 98,
            "assertion_passes": 98,
            "achieved_qps": 95.0,
            "aggregate_output_tps": 110.0,
        })
        baseline["latency"]["p95"] = 2.0
        current["latency"]["p95"] = 2.2

        result = compare_baseline(current, baseline)
        by_metric = {item["metric"]: item for item in result["metrics"]}
        self.assertEqual("regressed", result["conclusion"])
        self.assertEqual("regressed", by_metric["latency.p95"]["status"])
        self.assertAlmostEqual(0.2, by_metric["latency.p95"]["absolute_delta"])
        self.assertAlmostEqual(10.0, by_metric["latency.p95"]["percent_delta"])
        self.assertEqual("stable", by_metric["achieved_qps"]["status"])
        self.assertEqual("improved", by_metric["aggregate_output_tps"]["status"])
        self.assertEqual("regressed", by_metric["error_rate"]["status"])
        self.assertEqual(2, result["counts"]["regressed"])

    def test_baseline_accepts_custom_tolerance(self):
        baseline = self.summary.to_dict()
        current = self.summary.to_dict()
        baseline["latency"]["p95"] = 2.0
        current["latency"]["p95"] = 2.2
        result = compare_baseline(
            current,
            baseline,
            metrics=("latency.p95",),
            tolerances={"latency.p95": {"percent": 15.0, "absolute": 0.0}},
        )
        self.assertEqual("stable", result["conclusion"])
        self.assertEqual("stable", result["metrics"][0]["status"])
        self.assertEqual(15.0, result["metrics"][0]["tolerance_percent"])

    def test_baseline_checks_workload_and_request_dimensions(self):
        current = self.summary.to_dict()
        baseline = self.summary.to_dict()
        current["workload"] = {
            "sha256": "same", "seed": 1, "selected_record_ids": ["1"],
            "dimensions": {"prompt_type": "general", "input_size": "short", "output_tokens": 128},
        }
        baseline["workload"] = {
            "sha256": "same", "seed": 1, "selected_record_ids": ["1"],
            "dimensions": {"prompt_type": "general", "input_size": "short", "output_size": 256},
        }
        current["request"] = {"model": "model-a", "stream": True}
        baseline["request"] = {"model": "model-a", "stream": False}
        result = compare_baseline(current, baseline)
        self.assertFalse(result["compatible"])
        self.assertIn("workload.dimensions.output_tokens 不一致", result["incompatibilities"])
        self.assertIn("request.stream 不一致", result["incompatibilities"])

    def test_invalid_threshold_is_rejected(self):
        for value in ("latency.p95=2", "latency.p95:<=:nan", "latency.p95:<=:inf"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_threshold(value)


if __name__ == "__main__":
    unittest.main()

import unittest

from benchmark_core import (
    ErrorCategory,
    RequestError,
    RequestSample,
    TimingMetrics,
    TokenUsage,
    aggregate_time_windows,
)
from benchmark_server.runner import build_web_result_details


class WebResultDetailsTests(unittest.TestCase):
    def test_time_series_and_failed_samples_are_limited_and_redacted(self):
        samples = (
            RequestSample(
                "success",
                0.0,
                TimingMetrics(0.5, 0.1, 0.4),
                TokenUsage(completion_tokens=4),
                transport_success=True,
                protocol_valid=True,
                assertion_passed=True,
            ),
            RequestSample(
                "failure-1",
                0.2,
                TimingMetrics(1.0, None, None),
                TokenUsage(),
                error=RequestError(
                    ErrorCategory.SERVER,
                    "Bearer abcdefghijk sk-abcdefghijk",
                    status_code=500,
                    retryable=True,
                ),
                status_code=500,
            ),
            RequestSample(
                "failure-2",
                0.4,
                None,
                TokenUsage(),
                transport_success=True,
                protocol_valid=True,
                assertion_passed=False,
            ),
        )
        windows = aggregate_time_windows(samples, total_elapsed=2.0)

        details = build_web_result_details(samples, windows, failure_limit=1)

        self.assertEqual(2, details["failed_sample_total"])
        self.assertEqual(1, len(details["failed_samples"]))
        self.assertGreaterEqual(len(details["time_series"]), 1)
        failure = details["failed_samples"][0]
        self.assertEqual("server", failure["category"])
        self.assertTrue(failure["retryable"])
        self.assertNotIn("abcdefghijk", failure["message"])

    def test_assertion_failure_uses_stable_fallback_message(self):
        sample = RequestSample(
            "assertion-failure",
            0.0,
            None,
            TokenUsage(),
            transport_success=True,
            protocol_valid=True,
            assertion_passed=False,
        )

        details = build_web_result_details((sample,), ())

        self.assertEqual("assertion", details["failed_samples"][0]["category"])
        self.assertEqual("响应断言未通过", details["failed_samples"][0]["message"])


if __name__ == "__main__":
    unittest.main()

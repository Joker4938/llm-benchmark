import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmark_core import (
    CancellationToken,
    ErrorCategory,
    RequestError,
    RequestSample,
    TimingMetrics,
    TokenSource,
    TokenUsage,
    aggregate_time_windows,
)
from benchmark_server.database import Database
from benchmark_server.runner import BenchmarkTaskRunner, build_web_result_details
from benchmark_server.security import SecretBox
from benchmark_server.storage import Repository


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


class FakeOpenAIClient:
    def __init__(self, endpoint, max_connections):
        self.endpoint = endpoint
        self.max_connections = max_connections

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def request(self, request, request_id, task_started_at):
        return RequestSample(
            request_id,
            0.0,
            TimingMetrics(0.1, 0.02, 0.08),
            TokenUsage(3, 8, 11, TokenSource.SERVER),
            content="ok",
            transport_success=True,
            protocol_valid=True,
        )


class BenchmarkTaskRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_result_preserves_request_and_workload_dimensions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = Database(root)
            database.initialize()
            repository = Repository(database, SecretBox.load(root))
            runner = BenchmarkTaskRunner(repository, root / "reports")
            payload = {
                "task_id": None,
                "endpoint": {
                    "base_url": "http://local/v1",
                    "model": "snapshot-model",
                    "api_key": "secret",
                },
                "plan": {
                    "name": "snapshot",
                    "plan_type": "fixed_concurrency",
                    "concurrency": 1,
                    "total_requests": 1,
                },
                "workload": {
                    "prompt_type": "structured",
                    "input_size": "short",
                    "output_size": 64,
                    "seed": 7,
                },
                "stream": False,
                "formats": ["json"],
            }

            async def sink(event):
                return None

            with patch("benchmark_server.runner.OpenAIChatClient", FakeOpenAIClient):
                result = await runner(payload, CancellationToken(), sink)

            self.assertEqual({"model": "snapshot-model", "stream": False}, result["request"])
            self.assertEqual(
                {"prompt_type": "structured", "input_size": "short", "output_tokens": 64},
                result["workload"]["dimensions"],
            )
            self.assertEqual(7, result["workload"]["seed"])
            self.assertEqual(1, len(result["artifacts"]))


if __name__ == "__main__":
    unittest.main()

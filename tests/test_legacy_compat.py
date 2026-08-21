import unittest
from unittest.mock import patch

import llm_benchmark
from benchmark_core import RequestSample, TimingMetrics, TokenSource, TokenUsage


class FakeClient:
    def __init__(self, endpoint, max_connections):
        self.endpoint = endpoint

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def request(self, request, request_id, task_started_at):
        return RequestSample(
            request_id,
            0,
            TimingMetrics(1.0, 0.2, 0.8),
            TokenUsage(2, 8, 10, TokenSource.SERVER),
            transport_success=True,
            protocol_valid=True,
            output_tps=10.0,
        )


class LegacyCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_run_benchmark_schema_and_progress_are_preserved(self):
        progress = []
        with patch('llm_benchmark.OpenAIChatClient', FakeClient):
            result = await llm_benchmark.run_benchmark(
                2, 1, 30, 32, 'http://local/v1', 'secret', 'demo', False,
                progress_callback=lambda completed, total: progress.append((completed, total)),
            )
        self.assertEqual(2, result['total_requests'])
        self.assertEqual(2, result['successful_requests'])
        self.assertEqual('demo', result['model'])
        self.assertIn('average', result['latency'])
        self.assertEqual([(1, 2), (2, 2)], progress)


if __name__ == '__main__':
    unittest.main()

import unittest

from benchmark_core.models import RequestSample, TimingMetrics, TokenSource, TokenUsage
from benchmark_core.windows import aggregate_time_windows


def sample(offset, latency, tokens=None, success=True):
    return RequestSample(
        request_id=str(offset),
        started_at_offset=offset,
        timing=TimingMetrics(latency, latency / 2, latency / 2),
        token_usage=TokenUsage(completion_tokens=tokens, source=TokenSource.SERVER if tokens is not None else TokenSource.UNAVAILABLE),
        content="ok" if success else "",
        transport_success=success,
        protocol_valid=success,
    )


class WindowTests(unittest.TestCase):
    def test_aggregates_by_completion_time(self):
        windows = aggregate_time_windows([
            sample(0.0, 0.2, 10),
            sample(0.5, 0.2, 20),
            sample(1.0, 0.3, 30),
        ], window_seconds=1.0, total_elapsed=2.0)
        self.assertEqual(2, len(windows))
        self.assertEqual(2, windows[0].completed)
        self.assertEqual(30, windows[0].output_tokens)
        self.assertEqual(30.0, windows[0].aggregate_output_tps)
        self.assertEqual(1, windows[1].completed)

    def test_missing_tokens_remain_unavailable(self):
        window = aggregate_time_windows([sample(0, 0.1)], total_elapsed=1)[0]
        self.assertIsNone(window.output_tokens)
        self.assertIsNone(window.aggregate_output_tps)

    def test_empty_with_elapsed_produces_empty_windows(self):
        windows = aggregate_time_windows([], window_seconds=1, total_elapsed=2)
        self.assertEqual(2, len(windows))
        self.assertEqual(0, windows[0].completed)


if __name__ == "__main__":
    unittest.main()

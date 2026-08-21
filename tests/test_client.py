import asyncio
import unittest
from types import SimpleNamespace

from benchmark_core.client import OpenAIChatClient, consume_stream, parse_completion
from benchmark_core.models import EndpointConfig, ErrorCategory, RequestConfig, TokenSource


class SequenceClock:
    def __init__(self, *values):
        self.values = iter(values)

    def __call__(self):
        return next(self.values)


async def stream_chunks(chunks):
    for chunk in chunks:
        yield chunk


class StreamParsingTests(unittest.IsolatedAsyncioTestCase):
    async def test_usage_after_finish_reason_is_consumed(self):
        chunks = [
            {"choices": [{"delta": {"content": "你"}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "好"}, "finish_reason": "stop"}]},
            {
                "choices": [],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
            },
        ]
        parsed = await consume_stream(stream_chunks(chunks), SequenceClock(11.0, 13.0))
        self.assertEqual("你好", parsed.content)
        self.assertEqual("stop", parsed.finish_reason)
        self.assertEqual(2, parsed.usage.completion_tokens)
        self.assertEqual(11.0, parsed.first_content_at)
        self.assertEqual(13.0, parsed.completed_at)

    async def test_empty_stream_is_protocol_parseable_but_empty(self):
        parsed = await consume_stream(stream_chunks([]), SequenceClock(2.0))
        self.assertEqual("", parsed.content)
        self.assertIsNone(parsed.usage)
        self.assertIsNone(parsed.first_content_at)


class FakeCompletions:
    def __init__(self, response=None, delay=0):
        self.response = response
        self.delay = delay
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.response


class FakeSdk:
    def __init__(self, completions):
        self.chat = SimpleNamespace(completions=completions)
        self.closed = False

    async def close(self):
        self.closed = True


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_tps_excludes_ttft(self):
        chunks = [
            {"choices": [{"delta": {"content": "a"}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "b"}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"completion_tokens": 20, "total_tokens": 20}},
        ]
        fake = FakeSdk(FakeCompletions(stream_chunks(chunks)))
        # task offset start, request start, first content, stream complete
        client = OpenAIChatClient(
            EndpointConfig("http://localhost:8000/v1", "model"),
            client=fake,
            clock=SequenceClock(100.0, 101.0, 102.0, 104.0),
        )
        sample = await client.request(RequestConfig([{"role": "user", "content": "hi"}]))
        self.assertEqual(3.0, sample.timing.latency)
        self.assertEqual(1.0, sample.timing.ttft)
        self.assertEqual(2.0, sample.timing.generation_duration)
        self.assertEqual(10.0, sample.output_tps)
        self.assertEqual(TokenSource.SERVER, sample.token_usage.source)
        self.assertTrue(sample.protocol_valid)

    async def test_timeout_is_classified_and_not_retried(self):
        completions = FakeCompletions(response=None, delay=0.05)
        client = OpenAIChatClient(
            EndpointConfig("http://localhost:8000", "model", timeout_seconds=0.001),
            client=FakeSdk(completions),
        )
        sample = await client.request(RequestConfig([{"role": "user", "content": "hi"}]))
        self.assertEqual(ErrorCategory.TIMEOUT, sample.error.category)
        self.assertFalse(sample.transport_success)
        self.assertIsNotNone(completions.kwargs)

    def test_non_stream_completion(self):
        response = {
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        parsed = parse_completion(response, 5.0)
        self.assertEqual("ok", parsed.content)
        self.assertEqual("stop", parsed.finish_reason)
        self.assertEqual(5.0, parsed.first_content_at)
        self.assertEqual(1, parsed.usage.completion_tokens)


if __name__ == "__main__":
    unittest.main()

import unittest

from benchmark_core.models import TokenSource
from benchmark_core.redaction import mask_secret, redact, redact_text
from benchmark_core.tokens import resolve_token_usage, usage_from_object


class TokenTests(unittest.TestCase):
    def test_server_usage_has_priority(self):
        server = usage_from_object({"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5})
        resolved = resolve_token_usage(server, "ignored", lambda _: 999)
        self.assertEqual(TokenSource.SERVER, resolved.source)
        self.assertEqual(3, resolved.completion_tokens)

    def test_estimate_is_explicitly_marked(self):
        resolved = resolve_token_usage(None, "abcd", len, "characters")
        self.assertEqual(TokenSource.ESTIMATED, resolved.source)
        self.assertEqual(4, resolved.completion_tokens)
        self.assertEqual("characters", resolved.estimator)

    def test_no_estimator_does_not_fall_back_to_chunk_count(self):
        resolved = resolve_token_usage(None, "many streamed chunks")
        self.assertEqual(TokenSource.UNAVAILABLE, resolved.source)
        self.assertIsNone(resolved.completion_tokens)


class RedactionTests(unittest.TestCase):
    def test_recursive_redaction(self):
        value = redact({"api_key": "sk-abcdefghijk", "nested": {"authorization": "Bearer abc.def"}})
        self.assertEqual("***", value["api_key"])
        self.assertEqual("***", value["nested"]["authorization"])

    def test_text_redaction(self):
        text = redact_text("Authorization: Bearer abc.def key=sk-abcdefghijk")
        self.assertNotIn("abc.def", text)
        self.assertNotIn("sk-abcdefghijk", text)

    def test_mask_secret(self):
        self.assertEqual("sk-******7890", mask_secret("sk-1234567890"))
        self.assertEqual("***", mask_secret("short"))


if __name__ == "__main__":
    unittest.main()

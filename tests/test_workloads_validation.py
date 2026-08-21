import json
import tempfile
import unittest
from pathlib import Path

from benchmark_core.models import RequestSample, TimingMetrics, TokenSource, TokenUsage
from benchmark_core.validation import AssertionSpec, AssertionType, validate_sample
from benchmark_core.workloads import WorkloadDimensions, builtin_dataset, load_jsonl, sample_records


class WorkloadTests(unittest.TestCase):
    def test_seeded_sampling_is_reproducible(self):
        dataset = builtin_dataset("general", "short")
        dimensions = WorkloadDimensions("general", "short", 64)
        first, first_snapshot = sample_records(dataset, 10, 42, dimensions)
        second, second_snapshot = sample_records(dataset, 10, 42, dimensions)
        self.assertEqual([item.record_id for item in first], [item.record_id for item in second])
        self.assertEqual(first_snapshot, second_snapshot)
        self.assertEqual(10, len(first_snapshot.selected_record_ids))

    def test_jsonl_reports_invalid_line(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.jsonl"
            path.write_text(json.dumps({"messages": [{"role": "bad", "content": "x"}]}) + "\n")
            with self.assertRaisesRegex(ValueError, "第 1 行"):
                load_jsonl(path)

    def test_jsonl_records_hash_and_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.jsonl"
            path.write_text(json.dumps({"id": "a", "messages": [{"role": "user", "content": "x"}]}) + "\n")
            dataset = load_jsonl(path)
            self.assertEqual("a", dataset.records[0].record_id)
            self.assertEqual(64, len(dataset.sha256))


class ValidationTests(unittest.TestCase):
    def make_sample(self, content='{"status":"ok","items":[1,2,3]}'):
        return RequestSample(
            request_id="r1",
            started_at_offset=0,
            timing=TimingMetrics(1, 0.2, 0.8),
            token_usage=TokenUsage(completion_tokens=12, source=TokenSource.SERVER),
            content=content,
            finish_reason="stop",
            transport_success=True,
            protocol_valid=True,
        )

    def test_no_assertions_is_not_applicable(self):
        result = validate_sample(self.make_sample(), [])
        self.assertFalse(result.applicable)
        self.assertIsNone(result.passed)

    def test_all_deterministic_assertion_families(self):
        assertions = [
            AssertionSpec(AssertionType.NON_EMPTY),
            AssertionSpec(AssertionType.TOKEN_RANGE, options={"min": 10, "max": 20}),
            AssertionSpec(AssertionType.FINISH_REASON, "stop"),
            AssertionSpec(AssertionType.CONTAINS, "status"),
            AssertionSpec(AssertionType.REGEX, r'"items"\s*:'),
            AssertionSpec(AssertionType.JSON_PARSE),
            AssertionSpec(AssertionType.JSON_SCHEMA, {"type": "object", "required": ["status"]}),
            AssertionSpec(AssertionType.RESPONSE_FIELD, options={"path": "items.1", "equals": 2}),
        ]
        result = validate_sample(self.make_sample(), assertions)
        self.assertTrue(result.passed)

    def test_exact_failure_does_not_change_transport_state(self):
        sample = self.make_sample("actual")
        result = validate_sample(sample, [AssertionSpec(AssertionType.EXACT, "expected")])
        self.assertFalse(result.passed)
        self.assertTrue(sample.transport_success)
        self.assertTrue(sample.protocol_valid)


if __name__ == "__main__":
    unittest.main()

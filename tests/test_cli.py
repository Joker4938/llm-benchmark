import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmark_cli.main import (
    EXIT_INTERRUPTED,
    EXIT_OK,
    EXIT_THRESHOLD,
    build_parser,
    execute_compare,
    execute_run,
)
from benchmark_core import CancellationToken, RequestSample, TimingMetrics, TokenSource, TokenUsage


class FakeClient:
    def __init__(self, endpoint, max_connections):
        self.endpoint = endpoint
        self.max_connections = max_connections

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def request(self, request, request_id, task_started_at):
        return RequestSample(
            request_id,
            0.0,
            TimingMetrics(0.1, 0.02, 0.08),
            TokenUsage(3, 8, 11, TokenSource.SERVER),
            content='ok',
            finish_reason='stop',
            transport_success=True,
            protocol_valid=True,
            output_tps=100.0,
        )


def parse_run(*extra):
    return build_parser().parse_args([
        'run', '--base-url', 'http://127.0.0.1:9999/v1', '--model', 'demo',
        '--requests', '2', '--quiet', *extra,
    ])


class CliParserTests(unittest.TestCase):
    def test_all_six_plans_parse(self):
        parser = build_parser()
        cases = {
            'smoke': ['--requests', '1'],
            'baseline': ['--requests', '1'],
            'fixed_concurrency': ['--requests', '1'],
            'stepped': ['--stage', 's1:1:1'],
            'constant_rate': ['--qps', '1', '--requests', '1'],
            'stability': ['--duration', '1'],
        }
        for plan, values in cases.items():
            args = parser.parse_args(['run', '--plan', plan, '--base-url', 'http://x/v1', '--model', 'm', *values])
            self.assertEqual(plan, args.plan)

    def test_compare_requires_resource_semantics(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args(['compare', '--target', 'a|http://x/v1|m'])


class CliExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_generates_reports_and_threshold_exit_code(self):
        with tempfile.TemporaryDirectory() as directory, patch('benchmark_cli.main.OpenAIChatClient', FakeClient):
            args = parse_run(
                '--output-dir', directory,
                '--format', 'json', '--format', 'html',
                '--threshold', 'achieved_qps:>:999999',
            )
            code, payload = await execute_run(args, install_signal_handlers=False)
            self.assertEqual(EXIT_THRESHOLD, code)
            self.assertEqual('failed', payload['thresholds'][0]['status'])
            self.assertEqual(2, len(payload['artifacts']))
            for artifact in payload['artifacts']:
                self.assertTrue((Path(directory) / artifact['path']).exists())

    async def test_pre_cancelled_run_preserves_partial_report(self):
        with tempfile.TemporaryDirectory() as directory, patch('benchmark_cli.main.OpenAIChatClient', FakeClient):
            token = CancellationToken()
            token.cancel()
            args = parse_run('--output-dir', directory, '--format', 'json')
            code, payload = await execute_run(
                args,
                cancellation=token,
                install_signal_handlers=False,
            )
            self.assertEqual(EXIT_INTERRUPTED, code)
            self.assertEqual(0, payload['completed_requests'])
            self.assertTrue((Path(directory) / payload['artifacts'][0]['path']).exists())

    async def test_sequential_compare_reuses_workload_order(self):
        with tempfile.TemporaryDirectory() as directory, patch('benchmark_cli.main.OpenAIChatClient', FakeClient):
            args = build_parser().parse_args([
                'compare', '--target', 'a|http://a/v1|m1', '--target', 'b|http://b/v1|m2',
                '--resource-semantics', 'independent', '--requests', '2', '--seed', '42',
                '--output-dir', directory, '--format', 'json', '--quiet',
            ])
            code, payload = await execute_compare(args)
            self.assertEqual(EXIT_OK, code)
            first = payload['runs'][0]['result']['workload']['selected_record_ids']
            second = payload['runs'][1]['result']['workload']['selected_record_ids']
            self.assertEqual(first, second)
            self.assertTrue(payload['same_workload_order'])

    async def test_synchronous_compare_requires_confirmation(self):
        args = build_parser().parse_args([
            'compare', '--target', 'a|http://a/v1|m1', '--target', 'b|http://b/v1|m2',
            '--resource-semantics', 'shared', '--comparison-mode', 'synchronous',
        ])
        with self.assertRaises(PermissionError):
            await execute_compare(args)


class CliOfflineCommandsTests(unittest.TestCase):
    def test_config_uses_environment_secret_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text(json.dumps({'endpoint': {'api_key_env': 'PRIVATE_KEY'}}), encoding='utf-8')
            from benchmark_cli.config import load_config, resolve_secret
            with patch.dict('os.environ', {'PRIVATE_KEY': 'secret-value'}):
                self.assertEqual('secret-value', resolve_secret(None, load_config(str(path))))


if __name__ == '__main__':
    unittest.main()

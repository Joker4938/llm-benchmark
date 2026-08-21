import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from benchmark_server.api import AppSettings, COOKIE_NAME, create_app
from benchmark_server.auth import SessionSigner


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.frontend = self.root / 'frontend'
        (self.frontend / 'assets').mkdir(parents=True)
        (self.frontend / 'index.html').write_text('<!doctype html><title>LLM Benchmark</title>', encoding='utf-8')
        (self.frontend / 'assets' / 'app.js').write_text('window.__BENCHMARK__ = true', encoding='utf-8')
        self.settings = AppSettings(
            data_dir=self.root,
            username='bench',
            password='correct-password',
            session_secret='0123456789abcdef-session-secret',
            session_hours=12,
            frontend_dir=self.frontend,
        )
        self.env = patch.dict(os.environ, {
            'LLM_BENCHMARK_LEGACY_CONFIG': str(self.root / 'missing-config.json'),
            'LLM_BENCHMARK_LEGACY_REPORTS': str(self.root / 'missing-reports'),
        })
        self.env.start()
        self.app = create_app(self.settings)
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    @property
    def repository(self):
        return self.app.state.repository

    @property
    def database(self):
        return self.app.state.database

    def login(self):
        response = self.client.post('/api/auth/login', json={
            'username': 'bench', 'password': 'correct-password',
        })
        self.assertEqual(200, response.status_code, response.text)
        return response

    def valid_task(self, **overrides):
        value = {
            'name': 'smoke',
            'endpoint': {
                'base_url': 'http://127.0.0.1:11434/v1',
                'model': 'local-model',
                'api_key': 'sk-inline-secret',
            },
            'plan': {'plan_type': 'smoke', 'concurrency': 1, 'total_requests': 2},
            'workload': {'output_size': 16},
            'formats': ['json'],
        }
        value.update(overrides)
        return value


class AuthenticationApiTests(ApiTestCase):
    def test_unauthenticated_login_verify_logout_and_cookie_security(self):
        response = self.client.get('/api/configs')
        self.assertEqual(401, response.status_code)
        self.assertEqual('http_error', response.json()['error']['code'])

        failed = self.client.post('/api/auth/login', json={'username': 'bench', 'password': 'wrong'})
        self.assertEqual(401, failed.status_code)
        self.assertEqual('用户名或密码错误', failed.json()['error']['message'])

        login = self.login()
        cookie = login.headers['set-cookie'].lower()
        self.assertIn('httponly', cookie)
        self.assertIn('samesite=strict', cookie)
        self.assertIn('max-age=43200', cookie)
        self.assertIn('expires_at', login.json())

        verified = self.client.get('/api/auth/verify')
        self.assertEqual(200, verified.status_code)
        self.assertEqual('bench', verified.json()['user'])
        self.assertIn('expires_at', verified.json())

        logout = self.client.post('/api/auth/logout')
        self.assertEqual(200, logout.status_code)
        self.assertEqual(401, self.client.get('/api/auth/verify').status_code)

    def test_tampered_and_expired_sessions_are_rejected(self):
        self.client.cookies.set(COOKIE_NAME, 'tampered.value')
        self.assertEqual(401, self.client.get('/api/auth/verify').status_code)
        expired = SessionSigner(self.settings.session_secret, ttl_seconds=1).issue('bench', now=int(time.time()) - 2)
        self.client.cookies.set(COOKIE_NAME, expired)
        self.assertEqual(401, self.client.get('/api/auth/verify').status_code)

    def test_request_id_security_headers_and_validation_shape(self):
        response = self.client.get('/health/live', headers={'X-Request-ID': 'known-request'})
        self.assertEqual('known-request', response.headers['x-request-id'])
        self.assertEqual('no-store', response.headers['cache-control'])
        self.assertEqual('nosniff', response.headers['x-content-type-options'])

        response = self.client.post('/api/auth/login', json={})
        body = response.json()
        self.assertEqual(422, response.status_code)
        self.assertEqual('validation_error', body['error']['code'])
        self.assertIsInstance(body['error']['details'], list)
        self.assertTrue(body['request_id'])

    def test_frontend_dist_is_served_without_shadowing_api_routes(self):
        index = self.client.get('/')
        self.assertEqual(200, index.status_code)
        self.assertIn('LLM Benchmark', index.text)
        self.assertEqual('window.__BENCHMARK__ = true', self.client.get('/assets/app.js').text)
        self.assertIn('LLM Benchmark', self.client.get('/history').text)
        missing_api = self.client.get('/api/not-found')
        self.assertEqual(404, missing_api.status_code)
        self.assertEqual('接口不存在', missing_api.json()['error']['message'])

    def test_unexpected_exception_is_redacted(self):
        @self.app.get('/api/test-error')
        def test_error():
            raise RuntimeError('api_key=sk-do-not-leak')

        response = self.client.get('/api/test-error')
        self.assertEqual(500, response.status_code)
        text = response.text
        self.assertNotIn('sk-do-not-leak', text)
        self.assertEqual('服务内部错误', response.json()['error']['message'])


class ResourceApiTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.login()

    def test_api_config_crud_masks_encrypts_and_retains_secret(self):
        created = self.client.post('/api/configs', json={
            'name': 'local', 'base_url': 'http://local/v1', 'model': 'm',
            'api_key': 'sk-super-secret-value', 'verify_tls': False,
        })
        self.assertEqual(201, created.status_code, created.text)
        item = created.json()
        self.assertNotEqual('sk-super-secret-value', item['api_key'])
        self.assertNotIn(b'sk-super-secret-value', self.database.path.read_bytes())

        updated = self.client.put(f"/api/configs/{item['id']}", json={
            'name': 'local-renamed', 'base_url': 'http://local/v1', 'model': 'm2',
            'verify_tls': True, 'timeout_seconds': 30,
        })
        self.assertEqual(200, updated.status_code, updated.text)
        revealed = self.repository.get_api_config(item['id'], reveal_secret=True)
        self.assertEqual('sk-super-secret-value', revealed['api_key'])
        self.assertEqual('m2', revealed['model'])

        deleted = self.client.delete(f"/api/configs/{item['id']}")
        self.assertEqual(204, deleted.status_code)
        self.assertEqual('', deleted.text)

    def test_dataset_validation_plan_templates_settings_and_thresholds(self):
        invalid = self.client.post('/api/datasets', json={'name': 'bad', 'content_jsonl': '{broken'})
        self.assertEqual(422, invalid.status_code)
        self.assertIn('不是合法 JSON', invalid.text)

        content = json.dumps({'messages': [{'role': 'user', 'content': '你好'}]}, ensure_ascii=False)
        created = self.client.post('/api/datasets', json={'name': '问答', 'content_jsonl': content})
        self.assertEqual(201, created.status_code, created.text)
        dataset_id = created.json()['id']
        self.assertNotIn('content_jsonl', created.json())
        detail = self.client.get(f'/api/datasets/{dataset_id}?include_content=true')
        self.assertEqual(content, detail.json()['content_jsonl'])

        plan = self.client.post('/api/plans', json={
            'name': '基线', 'plan': {'plan_type': 'baseline', 'concurrency': 2, 'total_requests': 10},
        })
        self.assertEqual(201, plan.status_code, plan.text)
        self.assertEqual(1, len(self.client.get('/api/plans').json()))

        settings = self.client.put('/api/settings', json={'theme': 'dark', 'api_key': 'hide-me'})
        self.assertEqual('***', settings.json()['api_key'])

        threshold = self.client.post('/api/thresholds', json={
            'name': '生产门槛', 'rules': [{'metric': 'latency.p95', 'operator': '<=', 'value': 2}],
        })
        self.assertEqual(201, threshold.status_code, threshold.text)
        threshold_id = threshold.json()['id']
        updated = self.client.put(f'/api/thresholds/{threshold_id}', json={
            'name': '严格门槛', 'rules': [{'metric': 'error_rate', 'operator': '<=', 'value': 0.01}],
        })
        self.assertEqual('严格门槛', updated.json()['name'])


class TaskApiTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.login()

    def test_preflight_hard_limits_risk_confirmation_and_plan_semantics(self):
        invalid = self.valid_task(plan={'plan_type': 'constant_rate', 'concurrency': 2, 'total_requests': 10})
        self.assertEqual(422, self.client.post('/api/plans/preflight', json=invalid).status_code)

        too_high = self.valid_task(plan={'plan_type': 'fixed_concurrency', 'concurrency': 501, 'total_requests': 10})
        response = self.client.post('/api/plans/preflight', json=too_high)
        self.assertEqual(422, response.status_code)
        self.assertIn('安全上限', response.text)

        risky = self.valid_task(plan={'plan_type': 'fixed_concurrency', 'concurrency': 101, 'total_requests': 10})
        self.assertEqual(409, self.client.post('/api/plans/preflight', json=risky).status_code)
        risky['risk_confirmed'] = True
        accepted = self.client.post('/api/plans/preflight', json=risky)
        self.assertEqual(200, accepted.status_code, accepted.text)
        self.assertIn('高并发', accepted.json()['risks'])

        stepped = self.valid_task(plan={
            'plan_type': 'stepped', 'concurrency': 1,
            'stages': [
                {'name': '一档', 'concurrency': 2, 'requests': 3},
                {'name': '二档', 'concurrency': 4, 'requests': 5},
            ],
        })
        response = self.client.post('/api/plans/preflight', json=stepped)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(8, response.json()['estimated_requests'])
        self.assertEqual(4, response.json()['estimated_max_concurrency'])

    def test_task_queue_lifecycle_and_inline_secret_is_not_persisted(self):
        first = self.client.post('/api/tasks', json=self.valid_task(name='first'))
        second = self.client.post('/api/tasks', json=self.valid_task(name='second'))
        self.assertEqual(202, first.status_code, first.text)
        self.assertEqual(1, first.json()['queue_position'])
        self.assertEqual(2, second.json()['queue_position'])
        self.assertNotIn(b'sk-inline-secret', self.database.path.read_bytes())
        self.assertNotIn('api_key', json.dumps(first.json()['payload']))
        self.assertEqual('local-model', first.json()['payload']['endpoint']['model'])

        stored = self.repository.save_api_config({
            'name': 'snapshot-source', 'base_url': 'http://snapshot/v1',
            'model': 'snapshot-model', 'api_key': 'sk-snapshot-secret',
        })
        snapshotted = self.client.post('/api/tasks', json=self.valid_task(
            name='snapshotted', endpoint=None, api_config_id=stored['id'],
        ))
        self.assertEqual(202, snapshotted.status_code, snapshotted.text)
        snapshot_payload = snapshotted.json()['payload']
        self.assertEqual('snapshot-model', snapshot_payload['endpoint']['model'])
        self.assertNotIn('api_key', snapshot_payload['endpoint'])

        queue = self.client.get('/api/queue').json()
        self.assertEqual([1, 2, 3], [item['queue_position'] for item in queue['queued']])
        task_id = first.json()['id']
        cancelled = self.client.post(f'/api/tasks/{task_id}/cancel')
        self.assertEqual(202, cancelled.status_code)
        self.assertEqual('cancelled', cancelled.json()['status'])

        claimed = self.repository.claim_next_task('test-worker')
        self.assertEqual(second.json()['id'], claimed['id'])
        stopping = self.client.post(f"/api/tasks/{claimed['id']}/cancel")
        self.assertEqual('stopping', stopping.json()['status'])
        status_response = self.client.get(f"/api/tasks/{claimed['id']}/status")
        self.assertEqual('stopping', status_response.json()['status'])

    def test_sse_last_event_id_resume_and_polling_fallback(self):
        task_id = self.repository.create_task('events', {})
        self.repository.append_events(task_id, [
            {'sequence': 1, 'elapsed': 0.1, 'phase': 'load', 'event_type': 'one', 'payload': {'value': 1}},
            {'sequence': 2, 'elapsed': 0.2, 'phase': 'load', 'event_type': 'two', 'payload': {'value': 2}},
        ])
        with self.database.transaction(immediate=True) as connection:
            connection.execute("UPDATE tasks SET status='completed',finished_at=?,updated_at=? WHERE id=?", (time.strftime('%Y-%m-%dT%H:%M:%SZ'), time.strftime('%Y-%m-%dT%H:%M:%SZ'), task_id))

        response = self.client.get(
            f'/api/tasks/{task_id}/events?follow=false', headers={'Last-Event-ID': '1'},
        )
        self.assertEqual(200, response.status_code)
        self.assertNotIn('event: one', response.text)
        self.assertIn('id: 2', response.text)
        self.assertIn('event: two', response.text)
        self.assertIn('event: terminal', response.text)
        self.assertEqual('completed', self.client.get(f'/api/tasks/{task_id}/status').json()['status'])
        self.assertEqual(422, self.client.get(f'/api/tasks/{task_id}/events', headers={'Last-Event-ID': 'bad'}).status_code)


class ReportAndDiagnosticApiTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.login()

    def completed_task(self, name='done', result=None):
        task_id = self.repository.create_task(name, {})
        claimed = self.repository.claim_next_task('worker')
        self.repository.finish_task(
            task_id,
            claimed['claim_token'],
            status='completed',
            result=result or {
                'task_id': task_id,
                'plan': {'name': '完成任务', 'plan_type': 'smoke', 'concurrency': 1},
                'completed_requests': 2,
                'transport_successes': 2,
                'latency': {'p95': 0.5},
                'api_key': 'must-not-export',
            },
        )
        return task_id

    @staticmethod
    def comparable_result(*, model='model-a', latency_p95=1.0, concurrency=2):
        return {
            'schema_version': '2.0',
            'plan': {
                'plan_type': 'fixed_concurrency',
                'concurrency': concurrency,
                'total_requests': 100,
                'duration_seconds': None,
                'target_qps': None,
                'warmup_seconds': 0,
                'cooldown_seconds': 0,
                'stages': [],
            },
            'request': {'model': model, 'stream': True},
            'workload': {
                'sha256': 'dataset-hash',
                'seed': 7,
                'selected_record_ids': ['record-1'],
                'dimensions': {
                    'prompt_type': 'structured',
                    'input_size': 'short',
                    'output_tokens': 64,
                },
            },
            'completed_requests': 100,
            'transport_successes': 100,
            'valid_responses': 100,
            'assertion_passes': 100,
            'latency': {'p95': latency_p95},
            'achieved_qps': 20.0,
            'aggregate_output_tps': 1000.0,
            'api_key': 'must-not-leak',
        }

    def test_report_generate_download_delete_and_path_traversal(self):
        task_id = self.completed_task()
        generated = self.client.post(f'/api/tasks/{task_id}/reports', json={'format': 'html'})
        self.assertEqual(201, generated.status_code, generated.text)
        report = generated.json()
        download = self.client.get(f"/api/reports/{report['id']}/download")
        self.assertEqual(200, download.status_code)
        self.assertIn('text/html', download.headers['content-type'])
        self.assertNotIn(b'must-not-export', download.content)

        malicious_id = self.repository.add_report({
            'task_id': task_id, 'format': 'json', 'relative_path': '../outside.json',
            'size_bytes': 1, 'sha256': 'bad',
        })
        traversal = self.client.get(f'/api/reports/{malicious_id}/download')
        self.assertEqual(400, traversal.status_code)
        self.assertEqual(204, self.client.delete(f"/api/reports/{report['id']}").status_code)

    def test_readiness_and_diagnostics_are_offline_and_secret_free(self):
        ready = self.client.get('/health/ready')
        self.assertEqual(200, ready.status_code)
        self.assertEqual('ok', ready.json()['configuration'])
        diagnostics = self.client.get('/api/diagnostics')
        self.assertEqual(200, diagnostics.status_code)
        body = diagnostics.json()
        self.assertEqual('skipped', body['network_check'])
        self.assertIn('generator', body)
        text = diagnostics.text
        self.assertNotIn(self.settings.password, text)
        self.assertNotIn(self.settings.session_secret, text)

    def test_synchronous_comparison_requires_confirmation(self):
        first = self.completed_task()
        second = self.completed_task()
        payload = {
            'name': '同步对比', 'mode': 'synchronous', 'resource_semantics': 'shared',
            'task_ids': [first, second],
        }
        self.assertEqual(422, self.client.post('/api/comparisons', json=payload).status_code)
        payload['confirm_synchronous'] = True
        created = self.client.post('/api/comparisons', json=payload)
        self.assertEqual(201, created.status_code, created.text)
        self.assertEqual('shared', created.json()['resource_semantics'])

    def test_baseline_candidates_persistence_tolerance_and_delete(self):
        baseline_id = self.completed_task(
            '历史基线', self.comparable_result(latency_p95=1.0),
        )
        current_id = self.completed_task(
            '当前任务', self.comparable_result(latency_p95=1.2),
        )

        candidates = self.client.get(f'/api/tasks/{current_id}/baseline-candidates')
        self.assertEqual(200, candidates.status_code, candidates.text)
        candidate = next(item for item in candidates.json() if item['id'] == baseline_id)
        self.assertTrue(candidate['compatible'])
        self.assertEqual('regressed', candidate['conclusion'])

        saved = self.client.put(f'/api/tasks/{current_id}/baseline', json={
            'baseline_id': baseline_id,
            'tolerances': {'latency.p95': {'percent': 25, 'absolute': 0}},
        })
        self.assertEqual(200, saved.status_code, saved.text)
        self.assertEqual('stable', saved.json()['comparison']['conclusion'])
        self.assertNotIn('must-not-leak', saved.text)
        fetched = self.client.get(f'/api/tasks/{current_id}/baseline')
        self.assertEqual(baseline_id, fetched.json()['baseline_task_id'])

        self.assertEqual(204, self.client.delete(f'/api/tasks/{current_id}/baseline').status_code)
        self.assertEqual(404, self.client.get(f'/api/tasks/{current_id}/baseline').status_code)

    def test_baseline_rejects_self_missing_result_and_incompatible_task(self):
        baseline_id = self.completed_task('历史基线', self.comparable_result())
        current_id = self.completed_task(
            '当前任务', self.comparable_result(model='model-b'),
        )
        incompatible = self.client.put(f'/api/tasks/{current_id}/baseline', json={
            'baseline_id': baseline_id,
        })
        self.assertEqual(409, incompatible.status_code)
        self.assertIn('request.model 不一致', incompatible.text)

        self.assertEqual(422, self.client.put(f'/api/tasks/{current_id}/baseline', json={
            'baseline_id': current_id,
        }).status_code)

        pending_id = self.repository.create_task('尚未运行', {})
        no_result = self.client.put(f'/api/tasks/{current_id}/baseline', json={
            'baseline_id': pending_id,
        })
        self.assertEqual(409, no_result.status_code)

        invalid_tolerance = self.client.put(f'/api/tasks/{current_id}/baseline', json={
            'baseline_id': baseline_id,
            'tolerances': {'latency.p95': {'percent': -1}},
        })
        self.assertEqual(422, invalid_tolerance.status_code)


class AppSettingsTests(unittest.TestCase):
    def test_environment_configuration_is_strict(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                AppSettings.from_env()
        with self.assertRaises(ValueError):
            AppSettings(Path('/tmp'), 'admin', 'pass', 'short')


if __name__ == '__main__':
    unittest.main()

import asyncio
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from benchmark_server.database import Database
from benchmark_server.executor import LocalExecutor
from benchmark_server.migration import LegacyMigrator
from benchmark_server.security import SecretBox
from benchmark_server.storage import Repository


class StorageTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = Database(self.root)
        self.database.initialize()
        self.secrets = SecretBox.load(self.root)
        self.repository = Repository(self.database, self.secrets)

    def tearDown(self):
        self.temp.cleanup()


class DatabaseTests(StorageTestCase):
    def test_wal_schema_and_transaction_rollback(self):
        with self.database.connect() as connection:
            self.assertEqual('wal', connection.execute('PRAGMA journal_mode').fetchone()[0].lower())
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn('tasks', tables)
        with self.assertRaises(RuntimeError):
            with self.database.transaction() as connection:
                connection.execute("INSERT INTO settings VALUES('x','1','now')")
                raise RuntimeError('rollback')
        self.assertEqual('missing', self.repository.get_setting('x', 'missing'))

    def test_encrypted_api_key_mask_and_rotation(self):
        saved = self.repository.save_api_config({
            'name': 'local', 'base_url': 'http://local/v1', 'model': 'm',
            'api_key': 'sk-super-secret-value', 'is_default': True,
        })
        self.assertNotEqual('sk-super-secret-value', saved['api_key'])
        raw = self.database.path.read_bytes()
        self.assertNotIn(b'sk-super-secret-value', raw)
        self.assertEqual('sk-super-secret-value', self.repository.get_api_config(saved['id'], reveal_secret=True)['api_key'])
        replacement = SecretBox.load(self.root / 'replacement')
        self.assertEqual(1, self.repository.rotate_secret_box(replacement))
        self.assertEqual('sk-super-secret-value', self.repository.get_api_config(saved['id'], reveal_secret=True)['api_key'])

    def test_atomic_claim_queue_position_and_restart_recovery(self):
        first = self.repository.create_task('first', {'x': 1})
        second = self.repository.create_task('second', {'x': 2})
        self.assertEqual(1, self.repository.get_task(first)['queue_position'])
        self.assertEqual(2, self.repository.get_task(second)['queue_position'])
        claimed = self.repository.claim_next_task('worker')
        self.assertEqual(first, claimed['id'])
        self.assertEqual(1, self.repository.recover_interrupted_tasks())
        self.assertEqual('interrupted', self.repository.get_task(first)['status'])
        self.assertEqual(second, self.repository.claim_next_task('worker')['id'])


    def test_corrupt_database_recovers_without_blocking_startup(self):
        other = self.root / 'recovery'
        other.mkdir()
        path = other / 'llm-benchmark.db'
        path.write_bytes(b'not-a-sqlite-database')
        recovery_db = Database(other)
        result = recovery_db.initialize(resilient=True)
        self.assertTrue(result.recovered)
        self.assertTrue(Path(result.backup_path).exists())
        with recovery_db.connect() as connection:
            self.assertEqual(1, connection.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0])

    def test_concurrent_claim_never_claims_same_task_twice(self):
        task_id = self.repository.create_task('only', {})
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda index: self.repository.claim_next_task(f'w{index}'), range(4)))
        claimed = [item for item in results if item]
        self.assertEqual(1, len(claimed))
        self.assertEqual(task_id, claimed[0]['id'])

    def test_resource_repositories_round_trip(self):
        dataset_id = self.repository.save_dataset({
            'name': 'd', 'version': '1', 'sha256': 'abc', 'content_jsonl': '{"messages":[{"role":"user","content":"x"}]}',
        })
        plan_id = self.repository.save_plan({'name': 'p', 'plan_type': 'smoke'})
        threshold_id = self.repository.save_threshold('safe', [{'metric': 'error_rate'}])
        comparison_id = self.repository.save_comparison({
            'name': 'c', 'mode': 'sequential', 'resource_semantics': 'independent', 'task_ids': []
        })
        self.assertEqual(dataset_id, self.repository.get_dataset(dataset_id)['id'])
        self.assertEqual(plan_id, self.repository.get_plan(plan_id)['id'])
        self.assertTrue(self.repository.delete_resource('thresholds', threshold_id))
        self.assertTrue(self.repository.delete_resource('comparisons', comparison_id))

    def test_events_are_idempotent_and_resume_after_sequence(self):
        task = self.repository.create_task('events', {})
        events = [{'sequence': 1, 'elapsed': 0.1, 'phase': 'load', 'event_type': 'one', 'payload': {'api_key': 'x'}}]
        self.repository.append_events(task, events)
        self.repository.append_events(task, events)
        rows = self.repository.events_after(task, 0)
        self.assertEqual(1, len(rows))
        self.assertEqual('***', rows[0]['payload']['api_key'])
        self.assertEqual([], self.repository.events_after(task, 1))


class MigrationTests(StorageTestCase):
    def test_migration_is_non_blocking_idempotent_and_preserves_sources(self):
        configs = self.root / 'api_configs.json'
        original = json.dumps([{'name': 'old', 'llm_url': 'http://old/v1', 'model': 'm', 'api_key': 'secret'}])
        configs.write_text(original, encoding='utf-8')
        reports = self.root / 'legacy-reports'
        reports.mkdir()
        good = reports / 'good.json'
        good.write_text('{"ok":true}', encoding='utf-8')
        bad = reports / 'bad.json'
        bad.write_text('{broken', encoding='utf-8')
        migrator = LegacyMigrator(self.repository)
        first = migrator.migrate(api_configs_path=configs, reports_dir=reports)
        self.assertEqual({'migrated': 2, 'skipped': 0, 'failed': 1}, first)
        self.assertEqual(original, configs.read_text(encoding='utf-8'))
        self.assertTrue(good.exists())
        second = migrator.migrate(api_configs_path=configs, reports_dir=reports)
        self.assertEqual(2, second['skipped'])
        self.assertEqual(1, second['failed'])


class ExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        database = Database(root)
        database.initialize()
        self.repository = Repository(database, SecretBox.load(root))

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_executor_persists_events_and_result(self):
        task_id = self.repository.create_task('run', {'value': 7})
        claimed = self.repository.claim_next_task('test')

        async def runner(payload, cancellation, event_sink):
            await event_sink({'sequence': 1, 'elapsed': 0, 'phase': 'load', 'event_type': 'started', 'payload': {}})
            return {'answer': payload['value'], 'api_key': 'must-hide'}

        executor = LocalExecutor(self.repository, runner, heartbeat_seconds=0.01, event_batch_size=1)
        await executor.run_claimed(claimed)
        task = self.repository.get_task(task_id)
        self.assertEqual('completed', task['status'])
        self.assertEqual('***', task['result']['api_key'])
        self.assertEqual(1, len(self.repository.events_after(task_id)))

    async def test_stop_request_causes_partial_cancelled_result(self):
        task_id = self.repository.create_task('stop', {})
        claimed = self.repository.claim_next_task('test')

        async def runner(payload, cancellation, event_sink):
            while not cancellation.cancelled:
                await asyncio.sleep(0.005)
            return {'partial': True}

        executor = LocalExecutor(self.repository, runner, heartbeat_seconds=0.005)
        running = asyncio.create_task(executor.run_claimed(claimed))
        await asyncio.sleep(0.01)
        self.assertTrue(self.repository.request_stop(task_id))
        await asyncio.wait_for(running, 1)
        task = self.repository.get_task(task_id)
        self.assertEqual('cancelled', task['status'])
        self.assertTrue(task['result']['partial'])


if __name__ == '__main__':
    unittest.main()

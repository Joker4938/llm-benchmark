"""SQLite 数据目录、WAL、事务与可恢复迁移。"""

from __future__ import annotations

import os
import shutil
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True, slots=True)
class DatabaseRecovery:
    """数据库初始化或恢复结果。"""

    recovered: bool = False
    backup_path: str | None = None
    error: str | None = None


_MIGRATIONS = (
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS api_configs (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            base_url TEXT NOT NULL,
            model TEXT NOT NULL,
            api_key_ciphertext TEXT NOT NULL,
            key_fingerprint TEXT NOT NULL,
            verify_tls INTEGER NOT NULL DEFAULT 1,
            timeout_seconds REAL NOT NULL DEFAULT 60,
            is_default INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ux_api_configs_name ON api_configs(name);
        CREATE TABLE IF NOT EXISTS datasets (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            version TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            source_path TEXT,
            content_jsonl TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ux_datasets_hash ON datasets(sha256);
        CREATE TABLE IF NOT EXISTS plans (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            plan_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            status TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 0,
            payload_json TEXT NOT NULL,
            result_json TEXT,
            error_message TEXT,
            stopped_reason TEXT,
            claim_token TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            heartbeat_at TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_tasks_queue ON tasks(status, priority DESC, created_at ASC);
        CREATE TABLE IF NOT EXISTS task_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            sequence INTEGER NOT NULL,
            elapsed REAL NOT NULL,
            phase TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(task_id, sequence)
        );
        CREATE TABLE IF NOT EXISTS thresholds (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            rules_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS comparisons (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            mode TEXT NOT NULL,
            resource_semantics TEXT NOT NULL,
            task_ids_json TEXT NOT NULL,
            result_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reports (
            id TEXT PRIMARY KEY,
            task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
            format TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(relative_path, sha256)
        );
        CREATE TABLE IF NOT EXISTS migration_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_path TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            item_type TEXT NOT NULL,
            status TEXT NOT NULL,
            target_id TEXT,
            error_message TEXT,
            attempted_at TEXT NOT NULL,
            UNIQUE(source_path, content_sha256)
        );
        CREATE TABLE IF NOT EXISTS executor_state (
            name TEXT PRIMARY KEY,
            pid INTEGER,
            heartbeat_at TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        """,
    ),
    (
        2,
        """
        CREATE TABLE IF NOT EXISTS task_baselines (
            task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
            baseline_task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            tolerances_json TEXT NOT NULL DEFAULT '{}',
            comparison_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK(task_id <> baseline_task_id)
        );
        CREATE INDEX IF NOT EXISTS ix_task_baselines_baseline ON task_baselines(baseline_task_id);
        """,
    ),
    (
        3,
        """
        ALTER TABLE tasks ADD COLUMN comparison_id TEXT;
        ALTER TABLE tasks ADD COLUMN comparison_index INTEGER;
        ALTER TABLE tasks ADD COLUMN comparison_target_name TEXT;
        CREATE INDEX IF NOT EXISTS ix_tasks_comparison
            ON tasks(comparison_id, comparison_index, status);
        """,
    ),
)


class Database:
    """为同机 API 与 executor 提供短事务 SQLite 连接。"""

    def __init__(self, data_dir: str | Path, filename: str = "llm-benchmark.db") -> None:
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / filename

    def initialize(self, *, resilient: bool = True) -> DatabaseRecovery:
        """应用 schema 迁移；失败时保留原库并启用全新数据库。"""

        self.data_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._apply_migrations()
            return DatabaseRecovery()
        except Exception as exc:
            if not resilient:
                raise
            backup = None
            if self.path.exists():
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                failed = self.path.with_name(f"{self.path.name}.migration-failed-{stamp}")
                os.replace(self.path, failed)
                backup = str(failed)
                for suffix in ("-wal", "-shm"):
                    sidecar = Path(str(self.path) + suffix)
                    if sidecar.exists():
                        os.replace(sidecar, Path(str(failed) + suffix))
            self._apply_migrations()
            return DatabaseRecovery(True, backup, str(exc))

    def backup(self, destination: str | Path) -> Path:
        """使用 SQLite backup API 创建一致性备份。"""

        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as source, sqlite3.connect(target) as output:
            source.backup(output)
        return target

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _apply_migrations(self) -> None:
        with self.transaction(immediate=True) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            applied = {
                row["version"]
                for row in connection.execute("SELECT version FROM schema_migrations")
            }
            for version, script in _MIGRATIONS:
                if version in applied:
                    continue
                for statement in script.split(";"):
                    if statement.strip():
                        connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (version, utc_now()),
                )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

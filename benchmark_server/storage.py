"""SQLite 仓储和持久任务队列。"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping

from benchmark_core.redaction import redact

from .database import Database, utc_now
from .security import SecretBox


class Repository:
    """单机应用所需资源与任务仓储。"""

    def __init__(self, database: Database, secrets: SecretBox) -> None:
        self.database = database
        self.secrets = secrets

    def set_setting(self, key: str, value: Any) -> None:
        now = utc_now()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO settings(key,value_json,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
                (key, _json(value), now),
            )

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.database.connect() as connection:
            row = connection.execute("SELECT value_json FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row["value_json"]) if row else default

    def save_api_config(self, value: Mapping[str, Any], config_id: str | None = None) -> dict[str, Any]:
        config_id = config_id or uuid.uuid4().hex
        secret = str(value.get("api_key") or "")
        now = utc_now()
        with self.database.transaction(immediate=True) as connection:
            if value.get("is_default"):
                connection.execute("UPDATE api_configs SET is_default=0")
            connection.execute(
                """INSERT INTO api_configs(
                    id,name,base_url,model,api_key_ciphertext,key_fingerprint,verify_tls,
                    timeout_seconds,is_default,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,base_url=excluded.base_url,model=excluded.model,
                    api_key_ciphertext=excluded.api_key_ciphertext,key_fingerprint=excluded.key_fingerprint,
                    verify_tls=excluded.verify_tls,timeout_seconds=excluded.timeout_seconds,
                    is_default=excluded.is_default,updated_at=excluded.updated_at""",
                (
                    config_id,
                    str(value["name"]),
                    str(value["base_url"]),
                    str(value["model"]),
                    self.secrets.encrypt(secret),
                    self.secrets.fingerprint(secret),
                    1 if value.get("verify_tls", True) else 0,
                    float(value.get("timeout_seconds", 60)),
                    1 if value.get("is_default") else 0,
                    now,
                    now,
                ),
            )
        return self.get_api_config(config_id)

    def get_api_config(self, config_id: str, *, reveal_secret: bool = False) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM api_configs WHERE id=?", (config_id,)).fetchone()
        if not row:
            raise KeyError(config_id)
        return self._api_config(row, reveal_secret)

    def list_api_configs(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM api_configs ORDER BY is_default DESC, updated_at DESC"
            ).fetchall()
        return [self._api_config(row, False) for row in rows]

    def delete_api_config(self, config_id: str) -> bool:
        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute("DELETE FROM api_configs WHERE id=?", (config_id,))
            return cursor.rowcount > 0

    def rotate_secret_box(self, replacement: SecretBox) -> int:
        with self.database.transaction(immediate=True) as connection:
            rows = connection.execute("SELECT id,api_key_ciphertext FROM api_configs").fetchall()
            for row in rows:
                plain = self.secrets.decrypt(row["api_key_ciphertext"])
                connection.execute(
                    "UPDATE api_configs SET api_key_ciphertext=?,updated_at=? WHERE id=?",
                    (replacement.encrypt(plain), utc_now(), row["id"]),
                )
        self.secrets = replacement
        return len(rows)

    def save_dataset(self, value: Mapping[str, Any], dataset_id: str | None = None) -> str:
        return self._save_json_resource("datasets", value, dataset_id, dataset=True)

    def save_plan(self, value: Mapping[str, Any], plan_id: str | None = None) -> str:
        resource_id = plan_id or uuid.uuid4().hex
        now = utc_now()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO plans(id,name,plan_json,created_at,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name,plan_json=excluded.plan_json,updated_at=excluded.updated_at",
                (resource_id, str(value.get("name", resource_id)), _json(value), now, now),
            )
        return resource_id

    def save_threshold(self, name: str, rules: Any, threshold_id: str | None = None) -> str:
        resource_id = threshold_id or uuid.uuid4().hex
        now = utc_now()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO thresholds(id,name,rules_json,created_at,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name,rules_json=excluded.rules_json,updated_at=excluded.updated_at",
                (resource_id, name, _json(rules), now, now),
            )
        return resource_id

    def create_task(self, name: str, payload: Mapping[str, Any], *, priority: int = 0) -> str:
        task_id = uuid.uuid4().hex
        now = utc_now()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO tasks(id,name,status,priority,payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (task_id, name, "queued", priority, _json(payload), now, now),
            )
        return task_id

    def list_tasks(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 1000)),)
            ).fetchall()
        return [self._task(row) for row in rows]

    def get_task(self, task_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise KeyError(task_id)
        task = self._task(row)
        if task["status"] == "queued":
            with self.database.connect() as connection:
                position = connection.execute(
                    """SELECT COUNT(*) + 1 AS position FROM tasks
                    WHERE status='queued' AND (
                        priority > ? OR (priority = ? AND created_at < ?)
                    )""",
                    (row["priority"], row["priority"], row["created_at"]),
                ).fetchone()["position"]
            task["queue_position"] = position
        return task

    def save_task_baseline(
        self,
        task_id: str,
        baseline_task_id: str,
        tolerances: Mapping[str, Any],
        comparison: Mapping[str, Any],
    ) -> dict[str, Any]:
        """保存任务选择的历史基线及当时的比较快照。"""

        now = utc_now()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """INSERT INTO task_baselines(
                    task_id,baseline_task_id,tolerances_json,comparison_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET
                baseline_task_id=excluded.baseline_task_id,
                tolerances_json=excluded.tolerances_json,
                comparison_json=excluded.comparison_json,
                updated_at=excluded.updated_at""",
                (
                    task_id,
                    baseline_task_id,
                    _json(redact(tolerances)),
                    _json(redact(comparison)),
                    now,
                    now,
                ),
            )
        return self.get_task_baseline(task_id)

    def get_task_baseline(self, task_id: str) -> dict[str, Any]:
        """读取任务持久化的基线关系和比较快照。"""

        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM task_baselines WHERE task_id=?", (task_id,),
            ).fetchone()
        if not row:
            raise KeyError(task_id)
        return {
            "task_id": row["task_id"],
            "baseline_task_id": row["baseline_task_id"],
            "tolerances": json.loads(row["tolerances_json"]),
            "comparison": json.loads(row["comparison_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def delete_task_baseline(self, task_id: str) -> bool:
        """删除任务选择的历史基线。"""

        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute("DELETE FROM task_baselines WHERE task_id=?", (task_id,))
            return cursor.rowcount > 0

    def claim_next_task(self, worker: str) -> dict[str, Any] | None:
        token = f"{worker}:{uuid.uuid4().hex}"
        now = utc_now()
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT id FROM tasks WHERE status='queued' ORDER BY priority DESC,created_at ASC LIMIT 1"
            ).fetchone()
            if not row:
                return None
            cursor = connection.execute(
                """UPDATE tasks SET status='running',claim_token=?,started_at=?,heartbeat_at=?,updated_at=?
                WHERE id=? AND status='queued'""",
                (token, now, now, now, row["id"]),
            )
            if cursor.rowcount != 1:
                return None
            claimed = connection.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone()
        return self._task(claimed)

    def claim_next_task_batch(self, worker: str) -> list[dict[str, Any]]:
        """领取下一项工作；同步比较会在同一事务中领取整组任务。"""

        now = utc_now()
        with self.database.transaction(immediate=True) as connection:
            first = connection.execute(
                """SELECT tasks.id,tasks.comparison_id,comparisons.mode AS comparison_mode
                FROM tasks LEFT JOIN comparisons ON comparisons.id=tasks.comparison_id
                WHERE tasks.status='queued' AND (
                    tasks.comparison_id IS NULL
                    OR comparisons.mode='synchronous'
                    OR NOT EXISTS (
                        SELECT 1 FROM tasks AS prior
                        WHERE prior.comparison_id=tasks.comparison_id
                        AND prior.comparison_index < tasks.comparison_index
                        AND prior.status NOT IN ('completed','failed','cancelled','interrupted')
                    )
                )
                ORDER BY tasks.priority DESC,tasks.created_at ASC,tasks.comparison_index ASC
                LIMIT 1"""
            ).fetchone()
            if not first:
                return []
            if first["comparison_mode"] == "synchronous":
                rows = connection.execute(
                    """SELECT id FROM tasks
                    WHERE comparison_id=? AND status='queued'
                    ORDER BY comparison_index ASC""",
                    (first["comparison_id"],),
                ).fetchall()
            else:
                rows = [first]

            claimed_ids: list[str] = []
            for row in rows:
                task_id = str(row["id"])
                token = f"{worker}:{uuid.uuid4().hex}"
                cursor = connection.execute(
                    """UPDATE tasks SET status='running',claim_token=?,started_at=?,heartbeat_at=?,updated_at=?
                    WHERE id=? AND status='queued'""",
                    (token, now, now, now, task_id),
                )
                if cursor.rowcount == 1:
                    claimed_ids.append(task_id)
            if not claimed_ids:
                return []
            placeholders = ",".join("?" for _ in claimed_ids)
            claimed = connection.execute(
                f"SELECT * FROM tasks WHERE id IN ({placeholders}) ORDER BY comparison_index ASC",
                claimed_ids,
            ).fetchall()
        return [self._task(row) for row in claimed]

    def heartbeat(self, task_id: str, claim_token: str) -> bool:
        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute(
                "UPDATE tasks SET heartbeat_at=?,updated_at=? WHERE id=? AND claim_token=? AND status IN ('running','stopping')",
                (utc_now(), utc_now(), task_id, claim_token),
            )
            return cursor.rowcount == 1

    def request_stop(self, task_id: str) -> bool:
        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute(
                "UPDATE tasks SET status='stopping',updated_at=? WHERE id=? AND status='running'",
                (utc_now(), task_id),
            )
            return cursor.rowcount == 1

    def finish_task(
        self,
        task_id: str,
        claim_token: str,
        *,
        status: str,
        result: Mapping[str, Any] | None = None,
        error: str | None = None,
        stopped_reason: str | None = None,
    ) -> bool:
        if status not in {"completed", "failed", "cancelled", "interrupted"}:
            raise ValueError("非法任务终态")
        now = utc_now()
        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute(
                """UPDATE tasks SET status=?,result_json=?,error_message=?,stopped_reason=?,
                finished_at=?,heartbeat_at=?,updated_at=? WHERE id=? AND claim_token=?""",
                (
                    status,
                    _json(redact(result)) if result is not None else None,
                    redact(error) if error else None,
                    stopped_reason,
                    now,
                    now,
                    now,
                    task_id,
                    claim_token,
                ),
            )
            return cursor.rowcount == 1

    def recover_interrupted_tasks(self) -> int:
        now = utc_now()
        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute(
                """UPDATE tasks SET status='interrupted',stopped_reason='executor restarted',
                finished_at=?,updated_at=? WHERE status IN ('running','stopping')""",
                (now, now),
            )
            return cursor.rowcount

    def append_events(self, task_id: str, events: Iterable[Mapping[str, Any]]) -> int:
        rows = [
            (
                task_id,
                int(event["sequence"]),
                float(event.get("elapsed", 0)),
                str(event.get("phase", "load")),
                str(event["event_type"]),
                _json(redact(event.get("payload", {}))),
                utc_now(),
            )
            for event in events
        ]
        if not rows:
            return 0
        with self.database.transaction(immediate=True) as connection:
            connection.executemany(
                """INSERT OR IGNORE INTO task_events(
                    task_id,sequence,elapsed,phase,event_type,payload_json,created_at
                ) VALUES(?,?,?,?,?,?,?)""",
                rows,
            )
        return len(rows)

    def events_after(self, task_id: str, sequence: int = 0, limit: int = 1000) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM task_events WHERE task_id=? AND sequence>? ORDER BY sequence LIMIT ?",
                (task_id, sequence, max(1, min(limit, 5000))),
            ).fetchall()
        return [
            {
                "sequence": row["sequence"],
                "elapsed": row["elapsed"],
                "phase": row["phase"],
                "event_type": row["event_type"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def add_report(self, value: Mapping[str, Any], report_id: str | None = None) -> str:
        report_id = report_id or uuid.uuid4().hex
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """INSERT OR IGNORE INTO reports(
                    id,task_id,format,relative_path,size_bytes,sha256,created_at
                ) VALUES(?,?,?,?,?,?,?)""",
                (
                    report_id,
                    value.get("task_id"),
                    str(value["format"]),
                    str(value["relative_path"]),
                    int(value["size_bytes"]),
                    str(value["sha256"]),
                    utc_now(),
                ),
            )
        return report_id

    def list_datasets(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM datasets ORDER BY updated_at DESC").fetchall()
        return [self._dataset(row) for row in rows]

    def get_dataset(self, dataset_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM datasets WHERE id=?", (dataset_id,)).fetchone()
        if not row:
            raise KeyError(dataset_id)
        return self._dataset(row)

    def list_plans(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM plans ORDER BY updated_at DESC").fetchall()
        return [{"id": row["id"], "name": row["name"], "plan": json.loads(row["plan_json"]), "updated_at": row["updated_at"]} for row in rows]

    def get_plan(self, plan_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
        if not row:
            raise KeyError(plan_id)
        return {"id": row["id"], "name": row["name"], "plan": json.loads(row["plan_json"]), "updated_at": row["updated_at"]}

    def save_comparison(self, value: Mapping[str, Any], comparison_id: str | None = None) -> str:
        comparison_id = comparison_id or uuid.uuid4().hex
        now = utc_now()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """INSERT INTO comparisons(id,name,mode,resource_semantics,task_ids_json,result_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,mode=excluded.mode,resource_semantics=excluded.resource_semantics,
                task_ids_json=excluded.task_ids_json,result_json=excluded.result_json,updated_at=excluded.updated_at""",
                (comparison_id, str(value["name"]), str(value["mode"]), str(value["resource_semantics"]),
                 _json(value.get("task_ids", [])), _json(redact(value.get("result"))) if value.get("result") is not None else None,
                 now, now),
            )
        return comparison_id

    def list_thresholds(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM thresholds ORDER BY updated_at DESC").fetchall()
        return [{"id": row["id"], "name": row["name"], "rules": json.loads(row["rules_json"]), "updated_at": row["updated_at"]} for row in rows]

    def get_threshold(self, threshold_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM thresholds WHERE id=?", (threshold_id,)).fetchone()
        if not row:
            raise KeyError(threshold_id)
        return {"id": row["id"], "name": row["name"], "rules": json.loads(row["rules_json"]), "updated_at": row["updated_at"]}

    def get_comparison(self, comparison_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM comparisons WHERE id=?", (comparison_id,)).fetchone()
        if not row:
            raise KeyError(comparison_id)
        return {"id": row["id"], "name": row["name"], "mode": row["mode"],
                "resource_semantics": row["resource_semantics"], "task_ids": json.loads(row["task_ids_json"]),
                "result": json.loads(row["result_json"]) if row["result_json"] else None,
                "created_at": row["created_at"], "updated_at": row["updated_at"]}

    def list_comparisons(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT id FROM comparisons ORDER BY created_at DESC").fetchall()
        return [self.get_comparison(row["id"]) for row in rows]

    def get_report(self, report_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
        if not row:
            raise KeyError(report_id)
        return dict(row)

    def list_reports(self, task_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM reports"
        params: tuple[Any, ...] = ()
        if task_id:
            query += " WHERE task_id=?"
            params = (task_id,)
        query += " ORDER BY created_at DESC"
        with self.database.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def delete_resource(self, table: str, resource_id: str) -> bool:
        if table not in {"datasets", "plans", "thresholds", "comparisons", "reports"}:
            raise ValueError("不允许删除该资源")
        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute(f"DELETE FROM {table} WHERE id=?", (resource_id,))
            return cursor.rowcount > 0

    def _dataset(self, row) -> dict[str, Any]:
        return {
            "id": row["id"], "name": row["name"], "version": row["version"],
            "sha256": row["sha256"], "source_path": row["source_path"],
            "content_jsonl": row["content_jsonl"], "metadata": json.loads(row["metadata_json"]),
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    def _api_config(self, row, reveal_secret: bool) -> dict[str, Any]:
        secret = self.secrets.decrypt(row["api_key_ciphertext"])
        return {
            "id": row["id"],
            "name": row["name"],
            "base_url": row["base_url"],
            "model": row["model"],
            "api_key": secret if reveal_secret else self.secrets.masked(secret),
            "key_fingerprint": row["key_fingerprint"],
            "verify_tls": bool(row["verify_tls"]),
            "timeout_seconds": row["timeout_seconds"],
            "is_default": bool(row["is_default"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _task(self, row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "status": row["status"],
            "priority": row["priority"],
            "payload": json.loads(row["payload_json"]),
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "error_message": row["error_message"],
            "stopped_reason": row["stopped_reason"],
            "claim_token": row["claim_token"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "heartbeat_at": row["heartbeat_at"],
            "updated_at": row["updated_at"],
            "comparison_id": row["comparison_id"],
            "comparison_index": row["comparison_index"],
            "comparison_target_name": row["comparison_target_name"],
        }

    def _save_json_resource(self, table, value, resource_id, *, dataset=False):
        resource_id = resource_id or uuid.uuid4().hex
        now = utc_now()
        if dataset:
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    """INSERT INTO datasets(id,name,version,sha256,source_path,content_jsonl,metadata_json,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,version=excluded.version,sha256=excluded.sha256,
                    source_path=excluded.source_path,content_jsonl=excluded.content_jsonl,
                    metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
                    (
                        resource_id,
                        str(value["name"]),
                        str(value["version"]),
                        str(value["sha256"]),
                        value.get("source_path"),
                        value.get("content_jsonl"),
                        _json(value.get("metadata", {})),
                        now,
                        now,
                    ),
                )
        return resource_id


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)

"""旧 JSON 配置和报告的幂等、非阻塞迁移。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from benchmark_core.redaction import redact

from .database import utc_now
from .storage import Repository


class LegacyMigrator:
    """迁移旧文件；单项失败只记录，不中断后续项目或启动。"""

    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def migrate(
        self,
        *,
        api_configs_path: str | Path | None = None,
        reports_dir: str | Path | None = None,
    ) -> dict[str, int]:
        counts = {"migrated": 0, "skipped": 0, "failed": 0}
        if api_configs_path:
            self._migrate_configs(Path(api_configs_path), counts)
        if reports_dir:
            root = Path(reports_dir)
            if root.exists():
                for path in sorted(root.iterdir()):
                    if path.is_file():
                        self._migrate_report(path, counts)
        return counts

    def retry_failed(self) -> dict[str, int]:
        """清除失败标记，允许下一次扫描重新尝试。"""

        with self.repository.database.transaction(immediate=True) as connection:
            count = connection.execute(
                "DELETE FROM migration_items WHERE status='failed'"
            ).rowcount
        return {"cleared": count}

    def _migrate_configs(self, path: Path, counts: dict[str, int]) -> None:
        if not path.exists():
            return
        try:
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if self._already_done(path, digest):
                counts["skipped"] += 1
                return
            data = json.loads(raw.decode("utf-8-sig"))
            if not isinstance(data, list):
                raise ValueError("旧 API 配置根节点必须是数组")
            migrated_ids = []
            for index, item in enumerate(data):
                if not isinstance(item, dict):
                    raise ValueError(f"第 {index + 1} 项不是对象")
                migrated_ids.append(
                    self.repository.save_api_config(
                        {
                            "name": item.get("name") or f"legacy-{index + 1}",
                            "base_url": item.get("llm_url") or item.get("base_url") or "",
                            "model": item.get("model") or "",
                            "api_key": item.get("api_key") or "",
                            "verify_tls": item.get("verify_tls", False),
                            "timeout_seconds": item.get("timeout", 60),
                            "is_default": bool(item.get("is_default")),
                        },
                        config_id=str(item.get("id")) if item.get("id") else None,
                    )["id"]
                )
            self._record(path, digest, "api_configs", "migrated", ",".join(migrated_ids), None)
            counts["migrated"] += 1
        except Exception as exc:
            self._record_failure(path, "api_configs", exc)
            counts["failed"] += 1

    def _migrate_report(self, path: Path, counts: dict[str, int]) -> None:
        try:
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if self._already_done(path, digest):
                counts["skipped"] += 1
                return
            suffix = "".join(path.suffixes).lower().lstrip(".") or "binary"
            if path.suffix.lower() == ".json":
                json.loads(raw.decode("utf-8-sig"))
            report_id = self.repository.add_report(
                {
                    "task_id": None,
                    "format": suffix,
                    "relative_path": str(path.resolve()),
                    "size_bytes": len(raw),
                    "sha256": digest,
                }
            )
            self._record(path, digest, "report", "migrated", report_id, None)
            counts["migrated"] += 1
        except Exception as exc:
            self._record_failure(path, "report", exc)
            counts["failed"] += 1

    def _already_done(self, path: Path, digest: str) -> bool:
        with self.repository.database.connect() as connection:
            row = connection.execute(
                "SELECT status FROM migration_items WHERE source_path=? AND content_sha256=?",
                (str(path.resolve()), digest),
            ).fetchone()
        return bool(row and row["status"] == "migrated")

    def _record_failure(self, path: Path, item_type: str, exc: Exception) -> None:
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "missing"
        except OSError:
            digest = "unreadable"
        self._record(path, digest, item_type, "failed", None, redact(str(exc)))

    def _record(
        self,
        path: Path,
        digest: str,
        item_type: str,
        status: str,
        target_id: str | None,
        error: str | None,
    ) -> None:
        with self.repository.database.transaction(immediate=True) as connection:
            connection.execute(
                """INSERT INTO migration_items(
                    source_path,content_sha256,item_type,status,target_id,error_message,attempted_at
                ) VALUES(?,?,?,?,?,?,?) ON CONFLICT(source_path,content_sha256) DO UPDATE SET
                    status=excluded.status,target_id=excluded.target_id,
                    error_message=excluded.error_message,attempted_at=excluded.attempted_at""",
                (str(path.resolve()), digest, item_type, status, target_id, error, utc_now()),
            )

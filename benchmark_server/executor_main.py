"""独立本地 executor 进程入口。"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from .database import Database
from .executor import LocalExecutor
from .migration import LegacyMigrator
from .runner import BenchmarkTaskRunner
from .security import SecretBox
from .storage import Repository


def main() -> int:
    data_dir = Path(os.environ.get("LLM_BENCHMARK_DATA_DIR", "data"))
    database = Database(data_dir)
    recovery = database.initialize(resilient=True)
    repository = Repository(database, SecretBox.load(data_dir))
    LegacyMigrator(repository).migrate(
        api_configs_path=os.environ.get("LLM_BENCHMARK_LEGACY_CONFIG", "backend/api_configs.json"),
        reports_dir=os.environ.get("LLM_BENCHMARK_LEGACY_REPORTS", "reports"),
    )
    if recovery.recovered:
        repository.set_setting("database_recovery", {
            "backup_path": recovery.backup_path, "error": recovery.error,
        })
    runner = BenchmarkTaskRunner(repository, data_dir / "reports")
    asyncio.run(LocalExecutor(repository, runner).run_forever())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

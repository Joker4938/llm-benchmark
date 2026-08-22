"""持久队列的本地单任务 executor。"""

from __future__ import annotations

import asyncio
import os
import signal
from dataclasses import asdict, is_dataclass
from typing import Any, Awaitable, Callable, Mapping

from benchmark_core import CancellationToken
from benchmark_core.redaction import redact

from .database import utc_now
from .storage import Repository

Runner = Callable[[Mapping[str, Any], CancellationToken, Callable[[Any], Awaitable[None]]], Awaitable[Mapping[str, Any]]]


class LocalExecutor:
    """默认一次只领取一个任务，安全停止并批量持久化事件。"""

    def __init__(
        self,
        repository: Repository,
        runner: Runner,
        *,
        name: str = "local-executor",
        poll_seconds: float = 1.0,
        heartbeat_seconds: float = 5.0,
        event_batch_size: int = 20,
    ) -> None:
        self.repository = repository
        self.runner = runner
        self.name = name
        self.poll_seconds = poll_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.event_batch_size = event_batch_size
        self.shutdown = CancellationToken()

    async def run_forever(self) -> None:
        self.repository.recover_interrupted_tasks()
        self._install_signal_handlers()
        while not self.shutdown.cancelled:
            tasks = self.repository.claim_next_task_batch(self.name)
            if not tasks:
                await self._record_executor_heartbeat()
                try:
                    await asyncio.wait_for(self.shutdown.wait(), timeout=self.poll_seconds)
                except asyncio.TimeoutError:
                    pass
                continue
            await self.run_claimed_batch(tasks)

    async def run_claimed_batch(self, tasks: list[Mapping[str, Any]]) -> None:
        """运行一项普通工作，或并发运行一次同步模型比较的全部子任务。"""

        if len(tasks) == 1:
            await self.run_claimed(tasks[0])
            return
        await asyncio.gather(*(self.run_claimed(task) for task in tasks))

    async def run_claimed(self, task: Mapping[str, Any]) -> None:
        cancellation = CancellationToken()
        pending_events: list[dict[str, Any]] = []
        claim_token = str(task["claim_token"])

        async def flush() -> None:
            if pending_events:
                self.repository.append_events(str(task["id"]), pending_events[:])
                pending_events.clear()

        async def event_sink(event: Any) -> None:
            value = asdict(event) if is_dataclass(event) else dict(event)
            phase = value.get("phase")
            if hasattr(phase, "value"):
                value["phase"] = phase.value
            pending_events.append(value)
            if len(pending_events) >= self.event_batch_size:
                await flush()

        async def monitor() -> None:
            while not cancellation.cancelled and not self.shutdown.cancelled:
                await asyncio.sleep(self.heartbeat_seconds)
                current = self.repository.get_task(str(task["id"]))
                if current["status"] == "stopping" or self.shutdown.cancelled:
                    cancellation.cancel()
                self.repository.heartbeat(str(task["id"]), claim_token)
                await self._record_executor_heartbeat()

        monitor_task = asyncio.create_task(monitor())
        try:
            payload = dict(task["payload"])
            payload.setdefault("task_id", task["id"])
            result = await self.runner(payload, cancellation, event_sink)
            await flush()
            automatic_reason = result.get("stopped_reason")
            stopped = bool(automatic_reason) or cancellation.cancelled or self.shutdown.cancelled
            if automatic_reason:
                stopped_reason = str(automatic_reason)
            elif self.shutdown.cancelled:
                stopped_reason = "executor shutdown"
            elif cancellation.cancelled:
                stopped_reason = "user requested stop"
            else:
                stopped_reason = None
            self.repository.finish_task(
                str(task["id"]),
                claim_token,
                status="cancelled" if stopped else "completed",
                result=redact(result),
                stopped_reason=stopped_reason,
            )
        except asyncio.CancelledError:
            cancellation.cancel()
            await flush()
            self.repository.finish_task(
                str(task["id"]), claim_token, status="interrupted", stopped_reason="executor cancelled"
            )
            raise
        except Exception as exc:
            await flush()
            self.repository.finish_task(
                str(task["id"]), claim_token, status="failed", error=redact(str(exc))
            )
        finally:
            cancellation.cancel()
            monitor_task.cancel()
            await asyncio.gather(monitor_task, return_exceptions=True)

    async def _record_executor_heartbeat(self) -> None:
        with self.repository.database.transaction(immediate=True) as connection:
            connection.execute(
                """INSERT INTO executor_state(name,pid,heartbeat_at,metadata_json) VALUES(?,?,?,'{}')
                ON CONFLICT(name) DO UPDATE SET pid=excluded.pid,heartbeat_at=excluded.heartbeat_at""",
                (self.name, os.getpid(), utc_now()),
            )

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, self.shutdown.cancel)
            except (NotImplementedError, RuntimeError):
                pass

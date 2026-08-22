"""在单个容器内监督 FastAPI 与本地 executor 两个独立进程。"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Sequence

POLL_SECONDS = 0.2
DEFAULT_GRACE_SECONDS = 15.0


def build_commands() -> list[list[str]]:
    """根据环境变量构造 executor 与 API 的稳定启动命令。"""

    host = os.environ.get("LLM_BENCHMARK_HOST", "0.0.0.0")
    port = os.environ.get("LLM_BENCHMARK_PORT", "8080")
    return [
        [sys.executable, "-m", "benchmark_server.executor_main"],
        [
            sys.executable,
            "-m",
            "uvicorn",
            "benchmark_server.main:app",
            "--host",
            host,
            "--port",
            port,
        ],
    ]


def run_supervisor(
    commands: Sequence[Sequence[str]],
    *,
    grace_seconds: float = DEFAULT_GRACE_SECONDS,
) -> int:
    """启动并监督子进程；任一进程退出时回收其余进程。"""

    if grace_seconds < 0:
        raise ValueError("grace_seconds 不能为负数")
    processes: list[subprocess.Popen] = []
    shutdown_requested = False

    def request_shutdown(signum, frame) -> None:
        del signum, frame
        nonlocal shutdown_requested
        shutdown_requested = True

    previous_handlers = {
        signum: signal.getsignal(signum)
        for signum in (signal.SIGINT, signal.SIGTERM)
    }
    for signum in previous_handlers:
        signal.signal(signum, request_shutdown)

    try:
        processes = [subprocess.Popen(list(command)) for command in commands]
        while not shutdown_requested:
            for process in processes:
                returncode = process.poll()
                if returncode is not None:
                    return returncode if returncode != 0 else 1
            time.sleep(POLL_SECONDS)
        return 0
    finally:
        _stop_processes(processes, grace_seconds)
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


def _stop_processes(
    processes: Sequence[subprocess.Popen],
    grace_seconds: float,
) -> None:
    """先发送 TERM，超出宽限期后再强制终止剩余子进程。"""

    for process in processes:
        if process.poll() is None:
            process.terminate()

    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if all(process.poll() is not None for process in processes):
            break
        time.sleep(min(POLL_SECONDS, max(0.0, deadline - time.monotonic())))

    for process in processes:
        if process.poll() is None:
            process.kill()
        process.wait()


def main() -> int:
    """容器 PID 1 入口。"""

    grace_seconds = float(
        os.environ.get(
            "LLM_BENCHMARK_SHUTDOWN_GRACE_SECONDS",
            str(DEFAULT_GRACE_SECONDS),
        )
    )
    return run_supervisor(build_commands(), grace_seconds=grace_seconds)


if __name__ == "__main__":
    raise SystemExit(main())

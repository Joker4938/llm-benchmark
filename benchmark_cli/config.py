"""CLI 配置文件加载与参数合并。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def load_config(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取配置文件 {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("配置文件根节点必须是 JSON 对象")
    return value


def resolve(cli_value: Any, config: dict[str, Any], path: str, default: Any = None) -> Any:
    if cli_value is not None:
        return cli_value
    current: Any = config
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def resolve_secret(cli_value: str | None, config: dict[str, Any]) -> str:
    """凭据优先来自参数或环境变量；配置文件只允许引用环境变量名。"""

    if cli_value is not None:
        return cli_value
    env_name = resolve(None, config, "endpoint.api_key_env", "LLM_BENCHMARK_API_KEY")
    return os.environ.get(env_name, "")

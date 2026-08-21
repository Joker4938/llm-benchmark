"""Token 用量来源解析。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from .models import TokenSource, TokenUsage

TokenEstimator = Callable[[str], int]


def usage_from_object(usage: Any) -> TokenUsage | None:
    """从 SDK 对象或字典读取 usage，缺失时返回 ``None``。"""

    if usage is None:
        return None
    getter = usage.get if isinstance(usage, dict) else lambda name, default=None: getattr(usage, name, default)
    prompt = getter("prompt_tokens")
    completion = getter("completion_tokens")
    total = getter("total_tokens")
    if prompt is None and completion is None and total is None:
        return None
    return TokenUsage(
        prompt_tokens=_as_int(prompt),
        completion_tokens=_as_int(completion),
        total_tokens=_as_int(total),
        source=TokenSource.SERVER,
    )


def resolve_token_usage(
    server_usage: TokenUsage | None,
    content: str,
    estimator: TokenEstimator | None = None,
    estimator_name: str | None = None,
) -> TokenUsage:
    """优先返回服务端 usage，否则可选估算输出 Token。"""

    if server_usage is not None:
        return server_usage
    if estimator is None:
        return TokenUsage(source=TokenSource.UNAVAILABLE)
    completion = max(0, int(estimator(content)))
    return TokenUsage(
        completion_tokens=completion,
        total_tokens=completion,
        source=TokenSource.ESTIMATED,
        estimator=estimator_name or getattr(estimator, "__name__", "custom"),
    )


def dominant_token_source(usages: Sequence[TokenUsage]) -> TokenSource:
    """汇总时返回最保守的 Token 来源标签。"""

    sources = {usage.source for usage in usages}
    if not sources or sources == {TokenSource.UNAVAILABLE}:
        return TokenSource.UNAVAILABLE
    if TokenSource.ESTIMATED in sources or TokenSource.UNAVAILABLE in sources:
        return TokenSource.ESTIMATED
    return TokenSource.SERVER


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

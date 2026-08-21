"""不依赖第三方统计库的指标聚合。"""

from __future__ import annotations

import math
from collections.abc import Iterable

from .models import MetricStats


def percentile(values: Iterable[float], percent: float) -> float | None:
    """按线性插值计算常规升序分位数。"""

    if not 0 <= percent <= 100:
        raise ValueError("percent 必须位于 0 到 100 之间")
    ordered = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percent / 100
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def describe(values: Iterable[float | None]) -> MetricStats:
    """生成统一的描述统计，空集合使用 ``None`` 而不是伪造零值。"""

    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if not clean:
        return MetricStats(0, None, None, None, None, None, None, None, None)
    mean = sum(clean) / len(clean)
    variance = sum((value - mean) ** 2 for value in clean) / len(clean)
    return MetricStats(
        count=len(clean),
        minimum=min(clean),
        maximum=max(clean),
        mean=mean,
        stddev=math.sqrt(variance),
        p50=percentile(clean, 50),
        p90=percentile(clean, 90),
        p95=percentile(clean, 95),
        p99=percentile(clean, 99),
    )

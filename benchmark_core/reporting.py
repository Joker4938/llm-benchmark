"""统一报告模型及离线导出。"""

from __future__ import annotations

import csv
import gzip
import hashlib
import html
import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Sequence

from openpyxl import Workbook

from .models import BenchmarkEvent, BenchmarkSummary, ReportArtifact, RequestSample
from .redaction import redact
from .windows import TimeWindowMetrics


def to_jsonable(value: Any) -> Any:
    """把领域模型转换为脱敏的 JSON 基础类型。"""

    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return redact({str(key): to_jsonable(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return redact(value)


def write_json(path: str | Path, summary: BenchmarkSummary, extra: dict[str, Any] | None = None) -> ReportArtifact:
    payload = summary.to_dict()
    if extra:
        payload.update(to_jsonable(extra))
    return _write_bytes(path, json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"), "json")


def write_jsonl_gz(path: str | Path, records: Iterable[Any], *, format_name: str = "jsonl.gz") -> ReportArtifact:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(target, "wt", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(to_jsonable(record), ensure_ascii=False, separators=(",", ":")) + "\n")
    return _artifact(target, format_name)


def write_csv(path: str | Path, samples: Sequence[RequestSample]) -> ReportArtifact:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "request_id", "started_at_offset", "transport_success", "protocol_valid", "assertion_passed",
        "latency", "ttft", "generation_duration", "output_tps", "prompt_tokens", "completion_tokens",
        "total_tokens", "token_source", "finish_reason", "error_category", "error_message",
    ]
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for sample in samples:
            writer.writerow(_sample_row(sample))
    return _artifact(target, "csv")


def write_xlsx(path: str | Path, summary: BenchmarkSummary, samples: Sequence[RequestSample]) -> ReportArtifact:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    overview = workbook.active
    overview.title = "摘要"
    overview.append(["字段", "值"])
    for key, value in _flatten(summary.to_dict()):
        overview.append([key, json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value])
    details = workbook.create_sheet("请求样本")
    rows = [_sample_row(sample) for sample in samples]
    headers = list(rows[0]) if rows else list(_sample_row(_empty_sample()))
    details.append(headers)
    for row in rows:
        details.append([row[name] for name in headers])
    workbook.save(target)
    return _artifact(target, "xlsx")


def write_offline_html(
    path: str | Path,
    summary: BenchmarkSummary,
    windows: Sequence[TimeWindowMetrics] = (),
) -> ReportArtifact:
    data = json.dumps({"summary": summary.to_dict(), "windows": to_jsonable(windows)}, ensure_ascii=False).replace("</", "<\\/")
    title = html.escape(f"{summary.plan.name} · {summary.task_id}")
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>
:root{{--navy:#15242D;--paper:#F3F6F6;--graphite:#263840;--teal:#008F92;--amber:#D99022;--red:#C84A46}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--graphite);font:14px/1.5 Arial,"Microsoft YaHei",sans-serif}}
header{{background:var(--navy);color:white;padding:28px max(24px,6vw)}}main{{max-width:1200px;margin:auto;padding:24px}}h1{{margin:0;font-size:28px}}.meta{{opacity:.72}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}}.card{{background:white;border:1px solid #d7e0e1;padding:16px;border-radius:6px}}
.value{{font:700 24px Consolas,monospace;color:var(--teal)}}table{{width:100%;border-collapse:collapse;background:white}}th,td{{padding:9px;border-bottom:1px solid #dde5e6;text-align:right}}th:first-child,td:first-child{{text-align:left}}
.ruler{{display:flex;height:28px;margin:18px 0;background:#dce6e6;border-radius:4px;overflow:hidden}}.segment{{background:var(--teal);border-right:1px solid white;min-width:2px}}
</style></head><body><header><h1>{title}</h1><div class="meta">离线性能测试报告 · schema {summary.schema_version}</div></header>
<main><div id="cards" class="grid"></div><h2>负载轨迹</h2><div id="ruler" class="ruler"></div><h2>时间窗口</h2><table><thead><tr><th>窗口</th><th>完成</th><th>QPS</th><th>P95 延迟</th><th>输出 TPS</th></tr></thead><tbody id="rows"></tbody></table></main>
<script>const D={data};const S=D.summary;const metrics=[['请求数',S.completed_requests],['传输成功',S.transport_successes],['有效响应',S.valid_responses],['实际 QPS',S.achieved_qps],['聚合 TPS',S.aggregate_output_tps],['P95 延迟',S.latency.p95]];
document.getElementById('cards').innerHTML=metrics.map(x=>`<div class="card"><div>${{x[0]}}</div><div class="value">${{x[1]??'N/A'}}</div></div>`).join('');
const total=Math.max(1,D.windows.reduce((a,w)=>a+w.completed,0));document.getElementById('ruler').innerHTML=D.windows.map(w=>`<div class="segment" style="flex:${{Math.max(1,w.completed)}}" title="窗口 ${{w.index}} · ${{w.completed}} 请求"></div>`).join('');
document.getElementById('rows').innerHTML=D.windows.map(w=>`<tr><td>${{w.start_offset.toFixed(1)}}–${{w.end_offset.toFixed(1)}}s</td><td>${{w.completed}}</td><td>${{w.achieved_qps.toFixed(2)}}</td><td>${{w.latency_p95?.toFixed(3)??'N/A'}}</td><td>${{w.aggregate_output_tps?.toFixed(2)??'N/A'}}</td></tr>`).join('');</script></body></html>"""
    return _write_bytes(path, document.encode("utf-8"), "html")


def write_report_bundle(
    directory: str | Path,
    summary: BenchmarkSummary,
    samples: Sequence[RequestSample],
    events: Sequence[BenchmarkEvent],
    windows: Sequence[TimeWindowMetrics],
    formats: Sequence[str],
) -> tuple[ReportArtifact, ...]:
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    stem = summary.task_id
    artifacts: list[ReportArtifact] = []
    for format_name in dict.fromkeys(formats):
        if format_name == "json": artifacts.append(write_json(target / f"{stem}.json", summary, {"windows": windows}))
        elif format_name == "jsonl.gz": artifacts.append(write_jsonl_gz(target / f"{stem}.samples.jsonl.gz", samples))
        elif format_name == "events.jsonl.gz": artifacts.append(write_jsonl_gz(target / f"{stem}.events.jsonl.gz", events, format_name="events.jsonl.gz"))
        elif format_name == "csv": artifacts.append(write_csv(target / f"{stem}.csv", samples))
        elif format_name == "xlsx": artifacts.append(write_xlsx(target / f"{stem}.xlsx", summary, samples))
        elif format_name == "html": artifacts.append(write_offline_html(target / f"{stem}.html", summary, windows))
        else: raise ValueError(f"不支持的报告格式: {format_name}")
    return tuple(ReportArtifact(item.format, Path(item.relative_path).name, item.size_bytes, item.sha256) for item in artifacts)


def _sample_row(sample: RequestSample) -> dict[str, Any]:
    return {
        "request_id": sample.request_id, "started_at_offset": sample.started_at_offset,
        "transport_success": sample.transport_success, "protocol_valid": sample.protocol_valid,
        "assertion_passed": sample.assertion_passed,
        "latency": sample.timing.latency if sample.timing else None,
        "ttft": sample.timing.ttft if sample.timing else None,
        "generation_duration": sample.timing.generation_duration if sample.timing else None,
        "output_tps": sample.output_tps, "prompt_tokens": sample.token_usage.prompt_tokens,
        "completion_tokens": sample.token_usage.completion_tokens, "total_tokens": sample.token_usage.total_tokens,
        "token_source": sample.token_usage.source.value, "finish_reason": sample.finish_reason,
        "error_category": sample.error.category.value if sample.error else None,
        "error_message": redact(sample.error.message) if sample.error else None,
    }


def _empty_sample() -> RequestSample:
    from .models import TokenUsage
    return RequestSample("", 0, None, TokenUsage())


def _flatten(value: dict[str, Any], prefix: str = ""):
    for key, item in value.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(item, dict): yield from _flatten(item, name)
        else: yield name, item


def _write_bytes(path: str | Path, content: bytes, format_name: str) -> ReportArtifact:
    target = Path(path); target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(content)
    return _artifact(target, format_name)


def _artifact(path: Path, format_name: str) -> ReportArtifact:
    raw = path.read_bytes()
    return ReportArtifact(format_name, str(path), len(raw), hashlib.sha256(raw).hexdigest())

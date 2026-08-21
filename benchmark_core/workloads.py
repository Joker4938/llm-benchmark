"""内置负载、JSONL 数据集与可复现抽样。"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .models import RequestConfig

_ALLOWED_ROLES = {"system", "user", "assistant", "tool"}


@dataclass(frozen=True, slots=True)
class WorkloadDimensions:
    """替代旧 long-context 开关的显式负载维度。"""

    prompt_type: str = "general"
    input_size: str = "short"
    output_size: int = 128


@dataclass(frozen=True, slots=True)
class WorkloadRecord:
    """一个可抽样的 OpenAI messages 记录。"""

    record_id: str
    messages: tuple[Mapping[str, Any], ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_request(self, dimensions: WorkloadDimensions, *, stream: bool = True) -> RequestConfig:
        return RequestConfig(
            messages=self.messages,
            max_output_tokens=dimensions.output_size,
            stream=stream,
        )


@dataclass(frozen=True, slots=True)
class Dataset:
    """数据集内容及其不可变身份信息。"""

    name: str
    version: str
    sha256: str
    records: tuple[WorkloadRecord, ...]
    source: str


@dataclass(frozen=True, slots=True)
class WorkloadSnapshot:
    """记录一次任务实际使用的样本顺序。"""

    dataset_name: str
    dataset_version: str
    dataset_sha256: str
    seed: int
    selected_record_ids: tuple[str, ...]
    dimensions: WorkloadDimensions


_BUILTIN_PROMPTS = {
    "general": {
        "short": [
            "用三句话解释什么是人工智能。",
            "说明 HTTP 状态码 429 的含义。",
            "列出提高专注力的三个实用方法。",
            "9.11 和 9.9 哪个大？只回答结果并简要说明。",
        ],
        "medium": [
            "比较同步与异步编程的差异、适用场景和常见风险，并给出一个简短示例。",
            "解释数据库索引如何提高查询性能，以及索引过多可能带来的写入成本。",
        ],
        "long": [
            "请从可靠性、可观测性、容量规划和故障恢复四个方面，制定一份企业内网大模型服务上线检查清单。每项包含目标、检查方法和失败处理建议。",
        ],
    },
    "structured": {
        "short": ["返回 JSON：包含字段 status='ok'、items=[1,2,3]，不要输出 Markdown。"],
        "medium": ["以 JSON 数组返回三个压测指标，每项包含 name、unit、description，不要输出 Markdown。"],
        "long": ["生成一个 JSON 对象，包含 benchmark、metrics、thresholds、notes 四部分，并保证所有字段可被标准 JSON 解析器读取。"],
    },
    "reasoning": {
        "short": ["一个任务每秒处理 8 个请求，持续 15 秒，最多处理多少请求？"],
        "medium": ["有两个模型端点，平均延迟分别为 1.2 秒和 0.8 秒，吞吐分别为 20 QPS 和 15 QPS。说明不能只凭单一指标判断优劣的原因。"],
        "long": ["分析恒定并发与恒定到达率压测的实验差异，讨论排队、背压、尾延迟和容量拐点，并给出选择建议。"],
    },
}


def builtin_dataset(prompt_type: str = "general", input_size: str = "short") -> Dataset:
    """返回随代码版本固定的标准负载。"""

    try:
        prompts = _BUILTIN_PROMPTS[prompt_type][input_size]
    except KeyError as exc:
        raise ValueError(f"未知内置负载维度: {prompt_type}/{input_size}") from exc
    records = tuple(
        WorkloadRecord(f"builtin-{prompt_type}-{input_size}-{index}", ({"role": "user", "content": prompt},))
        for index, prompt in enumerate(prompts, 1)
    )
    canonical = json.dumps([record.messages for record in records], ensure_ascii=False, sort_keys=True).encode()
    digest = hashlib.sha256(canonical).hexdigest()
    return Dataset(f"builtin-{prompt_type}-{input_size}", f"1-{digest[:12]}", digest, records, "builtin")


def custom_dataset(system_message: str | None, user_message: str) -> Dataset:
    """把自定义 System/User 消息转换为单记录数据集。"""

    if not user_message.strip():
        raise ValueError("User 消息不能为空")
    messages: list[Mapping[str, Any]] = []
    if system_message and system_message.strip():
        messages.append({"role": "system", "content": system_message})
    messages.append({"role": "user", "content": user_message})
    raw = json.dumps(messages, ensure_ascii=False, sort_keys=True).encode()
    digest = hashlib.sha256(raw).hexdigest()
    return Dataset("custom", digest[:12], digest, (WorkloadRecord("custom-1", tuple(messages)),), "custom")


def load_jsonl(path: str | Path, *, name: str | None = None) -> Dataset:
    """加载并逐行校验 OpenAI messages JSONL。"""

    source_path = Path(path)
    raw = source_path.read_bytes()
    records: list[WorkloadRecord] = []
    for line_number, line in enumerate(raw.decode("utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"第 {line_number} 行不是有效 JSON: {exc.msg}") from exc
        messages = payload.get("messages") if isinstance(payload, dict) else None
        _validate_messages(messages, line_number)
        record_id = str(payload.get("id") or f"line-{line_number}")
        metadata = payload.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise ValueError(f"第 {line_number} 行 metadata 必须是对象")
        records.append(WorkloadRecord(record_id, tuple(messages), metadata))
    if not records:
        raise ValueError("JSONL 数据集没有有效记录")
    digest = hashlib.sha256(raw).hexdigest()
    return Dataset(name or source_path.stem, digest[:12], digest, tuple(records), str(source_path))


def sample_records(
    dataset: Dataset,
    count: int,
    seed: int,
    dimensions: WorkloadDimensions,
) -> tuple[list[WorkloadRecord], WorkloadSnapshot]:
    """使用固定种子产生稳定的样本顺序；请求数可超过数据集大小。"""

    if count <= 0:
        raise ValueError("count 必须大于 0")
    rng = random.Random(seed)
    selected: list[WorkloadRecord] = []
    while len(selected) < count:
        cycle = list(dataset.records)
        rng.shuffle(cycle)
        selected.extend(cycle[: count - len(selected)])
    snapshot = WorkloadSnapshot(
        dataset.name,
        dataset.version,
        dataset.sha256,
        seed,
        tuple(record.record_id for record in selected),
        dimensions,
    )
    return selected, snapshot


def _validate_messages(messages: Any, line_number: int) -> None:
    if not isinstance(messages, list) or not messages:
        raise ValueError(f"第 {line_number} 行 messages 必须是非空数组")
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise ValueError(f"第 {line_number} 行 messages[{index}] 必须是对象")
        if message.get("role") not in _ALLOWED_ROLES:
            raise ValueError(f"第 {line_number} 行 messages[{index}].role 无效")
        if "content" not in message:
            raise ValueError(f"第 {line_number} 行 messages[{index}] 缺少 content")

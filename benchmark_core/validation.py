"""不依赖 LLM-as-a-Judge 的确定性响应断言。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator

from .models import RequestSample


class AssertionType(str, Enum):
    NON_EMPTY = "non_empty"
    TOKEN_RANGE = "token_range"
    FINISH_REASON = "finish_reason"
    CONTAINS = "contains"
    REGEX = "regex"
    EXACT = "exact"
    JSON_PARSE = "json_parse"
    JSON_SCHEMA = "json_schema"
    RESPONSE_FIELD = "response_field"


@dataclass(frozen=True, slots=True)
class AssertionSpec:
    type: AssertionType
    value: Any = None
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AssertionResult:
    type: AssertionType
    passed: bool
    message: str


@dataclass(frozen=True, slots=True)
class ValidationResult:
    applicable: bool
    passed: bool | None
    results: tuple[AssertionResult, ...]


def validate_sample(sample: RequestSample, assertions: Sequence[AssertionSpec]) -> ValidationResult:
    """逐项执行断言；无断言时返回 not-applicable。"""

    if not assertions:
        return ValidationResult(False, None, ())
    results = tuple(_evaluate(sample, assertion) for assertion in assertions)
    return ValidationResult(True, all(result.passed for result in results), results)


def _evaluate(sample: RequestSample, assertion: AssertionSpec) -> AssertionResult:
    content = sample.content
    try:
        if assertion.type is AssertionType.NON_EMPTY:
            return _result(assertion, bool(content.strip()), "响应内容非空")
        if assertion.type is AssertionType.TOKEN_RANGE:
            count = sample.token_usage.completion_tokens
            minimum = assertion.options.get("min")
            maximum = assertion.options.get("max")
            passed = count is not None and (minimum is None or count >= minimum) and (maximum is None or count <= maximum)
            return _result(assertion, passed, f"输出 Token={count}")
        if assertion.type is AssertionType.FINISH_REASON:
            allowed = assertion.value if isinstance(assertion.value, list) else [assertion.value]
            return _result(assertion, sample.finish_reason in allowed, f"finish_reason={sample.finish_reason}")
        if assertion.type is AssertionType.CONTAINS:
            return _result(assertion, str(assertion.value) in content, "响应包含指定文本")
        if assertion.type is AssertionType.REGEX:
            flags = re.IGNORECASE if assertion.options.get("ignore_case") else 0
            return _result(assertion, re.search(str(assertion.value), content, flags) is not None, "响应匹配正则表达式")
        if assertion.type is AssertionType.EXACT:
            actual = content.strip() if assertion.options.get("strip", True) else content
            expected = str(assertion.value).strip() if assertion.options.get("strip", True) else str(assertion.value)
            return _result(assertion, actual == expected, "响应精确匹配")
        if assertion.type is AssertionType.JSON_PARSE:
            json.loads(content)
            return _result(assertion, True, "响应是有效 JSON")
        if assertion.type is AssertionType.JSON_SCHEMA:
            payload = json.loads(content)
            validator = Draft202012Validator(assertion.value)
            errors = sorted(validator.iter_errors(payload), key=lambda item: list(item.path))
            return _result(assertion, not errors, "JSON Schema 通过" if not errors else errors[0].message)
        if assertion.type is AssertionType.RESPONSE_FIELD:
            payload = json.loads(content)
            actual = _resolve_path(payload, str(assertion.options.get("path", assertion.value)))
            expected = assertion.options.get("equals")
            passed = actual is not None if "equals" not in assertion.options else actual == expected
            return _result(assertion, passed, f"字段值={actual!r}")
    except (json.JSONDecodeError, re.error, TypeError, ValueError) as exc:
        return _result(assertion, False, f"断言无法完成: {exc}")
    return _result(assertion, False, "未知断言类型")


def _resolve_path(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


def _result(spec: AssertionSpec, passed: bool, message: str) -> AssertionResult:
    return AssertionResult(spec.type, bool(passed), message[:500])


def apply_validation(sample: RequestSample, assertions: Sequence[AssertionSpec]) -> tuple[RequestSample, ValidationResult]:
    """返回带断言结论的新样本，同时保留传输与协议状态。"""

    from dataclasses import replace

    result = validate_sample(sample, assertions)
    return replace(sample, assertion_passed=result.passed), result

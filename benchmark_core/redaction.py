"""日志、错误、报告和 API 响应共用的敏感信息脱敏工具。"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

_SECRET_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "proxy-authorization",
    "x-api-key",
    "access_token",
    "refresh_token",
    "password",
    "secret",
}
_BEARER_RE = re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+/=-]+")
_OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")


def mask_secret(value: str | None) -> str:
    """保留少量首尾字符用于识别，但不泄露完整秘密。"""

    if not value:
        return ""
    if len(value) <= 7:
        return "***"
    return f"{value[:3]}{'*' * min(8, len(value) - 7)}{value[-4:]}"


def redact_text(value: str) -> str:
    """从任意文本中移除常见 Bearer 与 OpenAI 风格密钥。"""

    value = _BEARER_RE.sub(r"\1***", value)
    return _OPENAI_KEY_RE.sub("sk-***", value)


def redact(value: Any) -> Any:
    """递归脱敏字典、序列和文本。"""

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            result[key_text] = "***" if key_text.lower() in _SECRET_KEYS else redact(item)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value

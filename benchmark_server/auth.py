"""简单共享账户的签名会话。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Session:
    username: str
    expires_at: int


class SessionSigner:
    """签发和校验无服务器状态的短期会话。"""

    def __init__(self, secret: str, ttl_seconds: int = 12 * 3600) -> None:
        if len(secret) < 16:
            raise ValueError("会话密钥至少需要 16 个字符")
        self.secret = secret.encode("utf-8")
        self.ttl_seconds = ttl_seconds

    def issue(self, username: str, now: int | None = None) -> str:
        timestamp = int(time.time() if now is None else now)
        raw = json.dumps(
            {"sub": username, "exp": timestamp + self.ttl_seconds},
            separators=(",", ":"),
        ).encode("utf-8")
        body = base64.urlsafe_b64encode(raw).rstrip(b"=")
        signature = hmac.new(self.secret, body, hashlib.sha256).digest()
        return (body + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")).decode("ascii")

    def verify(self, token: str, now: int | None = None) -> Session:
        try:
            body, encoded_signature = token.encode("ascii").split(b".", 1)
            signature = _decode(encoded_signature)
            expected = hmac.new(self.secret, body, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError("签名错误")
            payload = json.loads(_decode(body))
            current = int(time.time() if now is None else now)
            if int(payload["exp"]) <= current:
                raise ValueError("会话已过期")
            return Session(str(payload["sub"]), int(payload["exp"]))
        except Exception as exc:
            raise ValueError("无效会话") from exc


def _decode(value: bytes) -> bytes:
    return base64.urlsafe_b64decode(value + b"=" * (-len(value) % 4))

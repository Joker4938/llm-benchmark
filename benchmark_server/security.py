"""安装级密钥和本地 API Key 认证加密。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from pathlib import Path

from benchmark_core.redaction import mask_secret


class SecretBox:
    """使用安装级 256 位密钥加密并校验数据库中的凭据。

    格式为 ``v1:base64(nonce || ciphertext || hmac)``。密钥流和认证密钥通过
    SHA-256/HMAC 域分离生成；该实现只依赖 Python 标准库，便于完全离线部署。
    """

    def __init__(self, key: bytes) -> None:
        if len(key) < 32:
            raise ValueError("安装级密钥长度不足")
        self._key = key[:32]

    @classmethod
    def load(
        cls,
        data_dir: str | Path,
        *,
        env_name: str = "LLM_BENCHMARK_MASTER_KEY",
    ) -> "SecretBox":
        configured = os.environ.get(env_name)
        if configured:
            try:
                key = base64.urlsafe_b64decode(configured.encode("ascii"))
            except Exception as exc:
                raise ValueError(f"{env_name} 必须是 URL-safe Base64") from exc
            return cls(key)
        path = Path(data_dir) / "secret.key"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            key = base64.urlsafe_b64decode(path.read_bytes().strip())
        else:
            key = os.urandom(32)
            encoded = base64.urlsafe_b64encode(key)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            descriptor = os.open(path, flags, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded + b"\n")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return cls(key)

    def encrypt(self, secret: str) -> str:
        nonce = os.urandom(16)
        plain = secret.encode("utf-8")
        cipher = _xor(plain, _keystream(self._key, nonce, len(plain)))
        tag = hmac.new(self._auth_key(), nonce + cipher, hashlib.sha256).digest()
        return "v1:" + base64.urlsafe_b64encode(nonce + cipher + tag).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        try:
            if not ciphertext.startswith("v1:"):
                raise ValueError("未知凭据密文版本")
            raw = base64.urlsafe_b64decode(ciphertext[3:].encode("ascii"))
            if len(raw) < 48:
                raise ValueError("凭据密文不完整")
            nonce, body, tag = raw[:16], raw[16:-32], raw[-32:]
            expected = hmac.new(self._auth_key(), nonce + body, hashlib.sha256).digest()
            if not hmac.compare_digest(tag, expected):
                raise ValueError("凭据认证失败")
            return _xor(body, _keystream(self._key, nonce, len(body))).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("凭据无法使用当前安装密钥解密") from exc

    def _auth_key(self) -> bytes:
        return hmac.new(self._key, b"llm-benchmark-auth-v1", hashlib.sha256).digest()

    @staticmethod
    def fingerprint(secret: str) -> str:
        return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def masked(secret: str) -> str:
        return mask_secret(secret)


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    chunks = []
    counter = 0
    while sum(map(len, chunks)) < length:
        chunks.append(
            hmac.new(
                key,
                b"llm-benchmark-encryption-v1" + nonce + counter.to_bytes(8, "big"),
                hashlib.sha256,
            ).digest()
        )
        counter += 1
    return b"".join(chunks)[:length]


def _xor(left: bytes, right: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(left, right))

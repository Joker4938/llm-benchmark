"""异常到稳定错误类别的映射。"""

from __future__ import annotations

import asyncio

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, AuthenticationError, RateLimitError

from .models import ErrorCategory, RequestError
from .redaction import redact_text


def classify_exception(exc: BaseException) -> RequestError:
    """将依赖库异常转换为安全、可聚合的请求错误。"""

    status_code = getattr(exc, "status_code", None)
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, APITimeoutError, httpx.TimeoutException)):
        category = ErrorCategory.TIMEOUT
    elif isinstance(exc, AuthenticationError) or status_code in {401, 403}:
        category = ErrorCategory.AUTHENTICATION
    elif isinstance(exc, RateLimitError) or status_code == 429:
        category = ErrorCategory.RATE_LIMIT
    elif isinstance(exc, APIStatusError) or isinstance(status_code, int):
        category = ErrorCategory.SERVER if status_code and status_code >= 500 else ErrorCategory.CLIENT
    elif isinstance(exc, (APIConnectionError, httpx.TransportError, ConnectionError)):
        category = ErrorCategory.TRANSPORT
    elif isinstance(exc, asyncio.CancelledError):
        category = ErrorCategory.CANCELLED
    else:
        category = ErrorCategory.UNKNOWN
    message = redact_text(str(exc)).strip() or exc.__class__.__name__
    return RequestError(
        category=category,
        message=message[:500],
        status_code=status_code,
        retryable=category in {ErrorCategory.TIMEOUT, ErrorCategory.RATE_LIMIT, ErrorCategory.SERVER, ErrorCategory.TRANSPORT},
    )

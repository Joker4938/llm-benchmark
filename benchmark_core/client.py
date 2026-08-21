"""OpenAI Chat Completions 协议客户端与响应解析。"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
from openai import AsyncOpenAI

from .errors import classify_exception
from .models import EndpointConfig, ErrorCategory, RequestConfig, RequestError, RequestSample, TimingMetrics, TokenUsage
from .tokens import TokenEstimator, resolve_token_usage, usage_from_object

Clock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class ParsedResponse:
    """解析后的协议响应。"""

    content: str
    finish_reason: str | None
    first_content_at: float | None
    completed_at: float
    usage: TokenUsage | None


class OpenAIChatClient:
    """负责 HTTP 生命周期、超时和 OpenAI 兼容响应解析。"""

    def __init__(
        self,
        endpoint: EndpointConfig,
        *,
        max_connections: int = 100,
        estimator: TokenEstimator | None = None,
        estimator_name: str | None = None,
        clock: Clock = time.perf_counter,
        client: Any | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.estimator = estimator
        self.estimator_name = estimator_name
        self.clock = clock
        self._owns_client = client is None
        self._http_client: httpx.AsyncClient | None = None
        if client is None:
            limits = httpx.Limits(max_connections=max_connections, max_keepalive_connections=max_connections)
            self._http_client = httpx.AsyncClient(verify=endpoint.verify_tls, limits=limits)
            self._client = AsyncOpenAI(
                api_key=endpoint.api_key or "none",
                base_url=_normalise_base_url(endpoint.base_url),
                http_client=self._http_client,
                default_headers=dict(endpoint.extra_headers),
            )
        else:
            self._client = client

    async def __aenter__(self) -> "OpenAIChatClient":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        await self.close()

    async def close(self) -> None:
        """可靠关闭 SDK 及底层连接池。"""

        if self._owns_client:
            close = getattr(self._client, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result
            elif self._http_client is not None:
                await self._http_client.aclose()

    async def request(
        self,
        config: RequestConfig,
        *,
        request_id: str | None = None,
        task_started_at: float | None = None,
    ) -> RequestSample:
        """执行一次请求；失败仅记录一次，不做自动重试。"""

        request_id = request_id or str(uuid.uuid4())
        task_started_at = self.clock() if task_started_at is None else task_started_at
        started_at = self.clock()
        kwargs = _request_kwargs(self.endpoint.model, config)
        try:
            parsed = await asyncio.wait_for(
                self._perform_request(config.stream, kwargs),
                timeout=self.endpoint.timeout_seconds,
            )
            latency = max(0.0, parsed.completed_at - started_at)
            ttft = None if parsed.first_content_at is None else max(0.0, parsed.first_content_at - started_at)
            generation_duration = (
                None
                if parsed.first_content_at is None
                else max(0.0, parsed.completed_at - parsed.first_content_at)
            )
            usage = resolve_token_usage(parsed.usage, parsed.content, self.estimator, self.estimator_name)
            output_tps = _output_tps(usage.completion_tokens, generation_duration)
            protocol_valid = bool(parsed.content or parsed.finish_reason or parsed.usage)
            error = None
            if not protocol_valid:
                error = RequestError(ErrorCategory.PROTOCOL, "响应不包含可识别的内容、结束原因或 usage")
            return RequestSample(
                request_id=request_id,
                started_at_offset=max(0.0, started_at - task_started_at),
                timing=TimingMetrics(latency, ttft, generation_duration),
                token_usage=usage,
                content=parsed.content,
                finish_reason=parsed.finish_reason,
                transport_success=True,
                protocol_valid=protocol_valid,
                error=error,
                output_tps=output_tps,
            )
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            error = classify_exception(exc)
            return RequestSample(
                request_id=request_id,
                started_at_offset=max(0.0, started_at - task_started_at),
                timing=None,
                token_usage=TokenUsage(),
                error=error,
            )

    async def _perform_request(self, stream: bool, kwargs: dict[str, Any]) -> ParsedResponse:
        response = await self._client.chat.completions.create(**kwargs)
        if stream:
            return await consume_stream(response, self.clock)
        return parse_completion(response, self.clock())


async def consume_stream(stream: AsyncIterable[Any], clock: Clock = time.perf_counter) -> ParsedResponse:
    """消费完整 SSE 流，即使 finish_reason 已出现也继续等待最终 usage。"""

    parts: list[str] = []
    finish_reason: str | None = None
    first_content_at: float | None = None
    usage: TokenUsage | None = None
    async for chunk in stream:
        chunk_usage = usage_from_object(_get(chunk, "usage"))
        if chunk_usage is not None:
            usage = chunk_usage
        choices = _get(chunk, "choices", []) or []
        for choice in choices:
            reason = _get(choice, "finish_reason")
            if reason is not None:
                finish_reason = str(reason)
            delta = _get(choice, "delta")
            content = _get(delta, "content") if delta is not None else None
            if content:
                if first_content_at is None:
                    first_content_at = clock()
                parts.append(str(content))
    return ParsedResponse("".join(parts), finish_reason, first_content_at, clock(), usage)


def parse_completion(response: Any, completed_at: float) -> ParsedResponse:
    """解析非流式 Chat Completion。"""

    parts: list[str] = []
    finish_reason: str | None = None
    for choice in _get(response, "choices", []) or []:
        reason = _get(choice, "finish_reason")
        if reason is not None:
            finish_reason = str(reason)
        message = _get(choice, "message")
        content = _get(message, "content") if message is not None else None
        if content:
            parts.append(str(content))
    content_text = "".join(parts)
    usage = usage_from_object(_get(response, "usage"))
    first_content_at = completed_at if content_text else None
    return ParsedResponse(content_text, finish_reason, first_content_at, completed_at, usage)


def _request_kwargs(model: str, config: RequestConfig) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [dict(message) for message in config.messages],
        "max_tokens": config.max_output_tokens,
        "stream": config.stream,
        "timeout": None,
    }
    if config.stream:
        kwargs["stream_options"] = {"include_usage": True}
    for name in ("temperature", "top_p", "seed"):
        value = getattr(config, name)
        if value is not None:
            kwargs[name] = value
    if config.extra_body:
        kwargs["extra_body"] = dict(config.extra_body)
    return kwargs


def _normalise_base_url(base_url: str) -> str:
    cleaned = base_url.rstrip("/")
    return cleaned if cleaned.endswith("/v1") else f"{cleaned}/v1"


def _get(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _output_tps(completion_tokens: int | None, generation_duration: float | None) -> float | None:
    if completion_tokens is None or generation_duration is None or generation_duration <= 0:
        return None
    return completion_tokens / generation_duration

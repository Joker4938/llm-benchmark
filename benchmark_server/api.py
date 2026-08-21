"""FastAPI 应用工厂与完全离线的单机管理 API。"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import hmac
import io
import json
import logging
import os
import platform
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from openpyxl import Workbook
from pydantic import BaseModel, Field, field_validator, model_validator

from benchmark_core import (
    BenchmarkPlan,
    PlanType,
    RequestConfig,
    SafetyLimits,
    StageConfig,
    compare_baseline,
    validate_plan,
)
from benchmark_core.redaction import redact

from .auth import Session, SessionSigner
from .database import Database, utc_now
from .migration import LegacyMigrator
from .security import SecretBox
from .storage import Repository

LOGGER = logging.getLogger("llm_benchmark.api")
COOKIE_NAME = "llm_benchmark_session"
TERMINAL_STATES = {"completed", "failed", "cancelled", "interrupted"}
REPORT_FORMATS = {"json", "html", "csv", "xlsx", "jsonl.gz", "events.jsonl.gz"}


@dataclass(frozen=True, slots=True)
class AppSettings:
    """服务端配置；生产值只允许从环境变量或显式构造注入。"""

    data_dir: Path
    username: str
    password: str
    session_secret: str
    session_hours: int = 12
    cors_origins: tuple[str, ...] = ()
    secure_cookie: bool = False
    frontend_dir: Path | None = None

    def __post_init__(self) -> None:
        if not self.username or not self.password:
            raise ValueError("登录用户名和密码不能为空")
        if len(self.session_secret) < 16:
            raise ValueError("会话密钥至少需要 16 个字符")
        if self.session_hours <= 0 or self.session_hours > 24 * 30:
            raise ValueError("会话时长必须在 1 到 720 小时之间")

    @classmethod
    def from_env(cls) -> "AppSettings":
        username = os.environ.get("LLM_BENCHMARK_USER")
        password = os.environ.get("LLM_BENCHMARK_PASSWORD")
        secret = os.environ.get("LLM_BENCHMARK_SESSION_SECRET")
        missing = [name for name, value in (
            ("LLM_BENCHMARK_USER", username),
            ("LLM_BENCHMARK_PASSWORD", password),
            ("LLM_BENCHMARK_SESSION_SECRET", secret),
        ) if not value]
        if missing:
            raise RuntimeError("缺少必需环境变量: " + ", ".join(missing))
        origins = tuple(item.strip() for item in os.environ.get("LLM_BENCHMARK_CORS_ORIGINS", "").split(",") if item.strip())
        return cls(
            Path(os.environ.get("LLM_BENCHMARK_DATA_DIR", "data")),
            str(username),
            str(password),
            str(secret),
            int(os.environ.get("LLM_BENCHMARK_SESSION_HOURS", "12")),
            origins,
            os.environ.get("LLM_BENCHMARK_SECURE_COOKIE", "false").lower() == "true",
            Path(os.environ["LLM_BENCHMARK_FRONTEND_DIR"]) if os.environ.get("LLM_BENCHMARK_FRONTEND_DIR") else None,
        )


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)


class ApiConfigInput(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    base_url: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=256)
    api_key: str | None = Field(default=None, max_length=4096)
    verify_tls: bool = True
    timeout_seconds: float = Field(default=60, gt=0, le=3600)
    is_default: bool = False

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("base_url 必须使用 http:// 或 https://")
        return value


class DatasetInput(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    content_jsonl: str = Field(min_length=1, max_length=20_000_000)

    @field_validator("content_jsonl")
    @classmethod
    def validate_jsonl(cls, value: str) -> str:
        found = False
        for number, line in enumerate(value.splitlines(), 1):
            if not line.strip():
                continue
            found = True
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"第 {number} 行不是合法 JSON: {exc.msg}") from exc
            if not isinstance(item, dict) or not isinstance(item.get("messages"), list) or not item["messages"]:
                raise ValueError(f"第 {number} 行 messages 必须是非空数组")
        if not found:
            raise ValueError("数据集不能为空")
        return value


class PlanTemplateInput(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    plan: dict[str, Any]


class AssertionInput(BaseModel):
    type: Literal[
        "non_empty", "token_range", "finish_reason", "contains", "regex",
        "exact", "json_parse", "json_schema", "response_field",
    ]
    value: Any = None
    options: dict[str, Any] = Field(default_factory=dict)


class TaskInput(BaseModel):
    name: str = Field(default="benchmark", min_length=1, max_length=128)
    api_config_id: str | None = None
    endpoint: dict[str, Any] | None = None
    plan: dict[str, Any]
    workload: dict[str, Any] = Field(default_factory=dict)
    assertions: list[AssertionInput] = Field(default_factory=list, max_length=50)
    stream: bool = True
    formats: list[Literal["json", "jsonl.gz", "events.jsonl.gz", "html", "xlsx", "csv"]] = Field(default_factory=lambda: ["json", "jsonl.gz"])
    priority: int = Field(default=0, ge=-100, le=100)
    risk_confirmed: bool = False

    @model_validator(mode="after")
    def validate_endpoint(self) -> "TaskInput":
        if not self.api_config_id and not self.endpoint:
            raise ValueError("必须提供 api_config_id 或 endpoint")
        return self


class ThresholdInput(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    rules: list[dict[str, Any]] = Field(min_length=1)


class BaselineInput(BaseModel):
    baseline_id: str = Field(min_length=1, max_length=64)
    tolerances: dict[str, dict[str, float | None] | float] = Field(default_factory=dict)

    @field_validator("tolerances")
    @classmethod
    def validate_tolerances(cls, value):
        for metric, configured in value.items():
            if not metric.strip():
                raise ValueError("容差指标名不能为空")
            numbers = configured.values() if isinstance(configured, dict) else (configured,)
            if any(number is not None and number < 0 for number in numbers):
                raise ValueError("基线容差不能为负数")
        return value


class ComparisonTargetInput(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    api_config_id: str | None = None
    endpoint: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_endpoint(self) -> "ComparisonTargetInput":
        if bool(self.api_config_id) == bool(self.endpoint):
            raise ValueError("每个比较目标必须且只能提供 api_config_id 或 endpoint")
        return self


class ComparisonInput(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    mode: Literal["sequential", "synchronous"] = "sequential"
    resource_semantics: Literal["independent", "shared"]
    task_ids: list[str] | None = Field(default=None, min_length=2, max_length=20)
    targets: list[ComparisonTargetInput] | None = Field(default=None, min_length=2, max_length=10)
    plan: dict[str, Any] | None = None
    workload: dict[str, Any] = Field(default_factory=dict)
    assertions: list[AssertionInput] = Field(default_factory=list, max_length=50)
    stream: bool = True
    formats: list[Literal["json", "jsonl.gz", "events.jsonl.gz", "html", "xlsx", "csv"]] = Field(
        default_factory=lambda: ["json", "jsonl.gz"]
    )
    priority: int = Field(default=0, ge=-100, le=100)
    risk_confirmed: bool = False
    confirm_synchronous: bool = False

    @model_validator(mode="after")
    def validate_source(self) -> "ComparisonInput":
        if bool(self.task_ids) == bool(self.targets):
            raise ValueError("必须且只能提供 task_ids 或 targets")
        if self.targets and self.plan is None:
            raise ValueError("创建模型比较任务时必须提供 plan")
        if self.targets:
            names = [target.name for target in self.targets]
            if len(names) != len(set(names)):
                raise ValueError("比较目标名称不能重复")
        return self


class ReportGenerateInput(BaseModel):
    format: Literal["json", "html", "csv", "xlsx"]


def create_app(settings: AppSettings | None = None) -> FastAPI:
    """创建不依赖任何公网服务的 FastAPI 应用。"""

    settings = settings or AppSettings.from_env()
    database = Database(settings.data_dir)
    recovery = database.initialize(resilient=True)
    repository = Repository(database, SecretBox.load(settings.data_dir))
    migration = LegacyMigrator(repository).migrate(
        api_configs_path=os.environ.get("LLM_BENCHMARK_LEGACY_CONFIG", "backend/api_configs.json"),
        reports_dir=os.environ.get("LLM_BENCHMARK_LEGACY_REPORTS", "reports"),
    )
    reports_dir = settings.data_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    frontend_dir = (
        settings.frontend_dir
        or (Path(os.environ["LLM_BENCHMARK_FRONTEND_DIR"]) if os.environ.get("LLM_BENCHMARK_FRONTEND_DIR") else None)
        or Path(__file__).resolve().parents[1] / "frontend" / "dist"
    ).resolve()
    signer = SessionSigner(settings.session_secret, settings.session_hours * 3600)
    app = FastAPI(title="LLM Benchmark", version="0.2.0", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.repository = repository
    app.state.database = database
    app.state.settings = settings
    app.state.recovery = recovery
    app.state.migration = migration
    app.state.reports_dir = reports_dir
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", "Last-Event-ID", "X-Request-ID"],
        )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        supplied = request.headers.get("x-request-id", "")
        request_id = supplied[:128] if supplied and supplied.isprintable() else uuid.uuid4().hex
        request.state.request_id = request_id
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        response.headers["cache-control"] = "no-store"
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["referrer-policy"] = "no-referrer"
        LOGGER.info(
            "request method=%s path=%s status=%s elapsed_ms=%.1f request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            (time.perf_counter() - started) * 1000,
            request_id,
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return _error(request, 422, "validation_error", "请求参数校验失败", _json_safe(exc.errors()))

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return _error(request, exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(StarletteHTTPException)
    async def route_error(request: Request, exc: StarletteHTTPException):
        path = request.url.path
        reserved = path == "/api" or path.startswith("/api/") or path == "/health" or path.startswith("/health/")
        asset_like = path.startswith("/assets/") or "." in Path(path).name
        index_path = frontend_dir / "index.html"
        if exc.status_code == 404 and request.method == "GET" and not reserved and not asset_like and index_path.is_file():
            return FileResponse(index_path, media_type="text/html")
        message = "接口不存在" if exc.status_code == 404 and reserved else str(exc.detail)
        return _error(request, exc.status_code, "http_error", message)

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception):
        LOGGER.error("unhandled request_id=%s error_type=%s", getattr(request.state, "request_id", "-"), type(exc).__name__)
        return _error(request, 500, "internal_error", "服务内部错误")

    def current_session(session: str | None = Cookie(default=None, alias=COOKIE_NAME)) -> Session:
        if not session:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未登录")
        try:
            return signer.verify(session)
        except ValueError:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "会话无效或已过期")

    def current_user(session: Session = Depends(current_session)) -> str:
        return session.username

    @app.post("/api/auth/login")
    def login(value: LoginInput, response: Response):
        if not (
            hmac.compare_digest(value.username, settings.username)
            and hmac.compare_digest(value.password, settings.password)
        ):
            raise HTTPException(401, "用户名或密码错误")
        session = signer.issue(value.username)
        response.set_cookie(
            COOKIE_NAME,
            session,
            httponly=True,
            secure=settings.secure_cookie,
            samesite="strict",
            max_age=settings.session_hours * 3600,
            path="/",
        )
        verified = signer.verify(session)
        return {"user": value.username, "expires_at": verified.expires_at, "expires_in": settings.session_hours * 3600}

    @app.get("/api/auth/verify")
    def verify(session: Session = Depends(current_session)):
        return {"user": session.username, "expires_at": session.expires_at}

    @app.post("/api/auth/logout")
    def logout(response: Response):
        response.delete_cookie(COOKIE_NAME, path="/", secure=settings.secure_cookie, samesite="strict")
        return {"ok": True}

    @app.get("/api/configs")
    def list_configs(user: str = Depends(current_user)):
        return repository.list_api_configs()

    @app.post("/api/configs", status_code=201)
    def create_config(value: ApiConfigInput, user: str = Depends(current_user)):
        payload = value.model_dump()
        payload["api_key"] = payload.get("api_key") or ""
        return repository.save_api_config(payload)

    @app.put("/api/configs/{config_id}")
    def update_config(config_id: str, value: ApiConfigInput, user: str = Depends(current_user)):
        try:
            existing = repository.get_api_config(config_id, reveal_secret=True)
        except KeyError:
            raise HTTPException(404, "配置不存在")
        payload = value.model_dump()
        if payload.get("api_key") is None:
            payload["api_key"] = existing["api_key"]
        return repository.save_api_config(payload, config_id)

    @app.delete("/api/configs/{config_id}", status_code=204)
    def delete_config(config_id: str, user: str = Depends(current_user)):
        if not repository.delete_api_config(config_id):
            raise HTTPException(404, "配置不存在")
        return Response(status_code=204)

    @app.get("/api/datasets")
    def list_datasets(user: str = Depends(current_user)):
        return [{k: v for k, v in item.items() if k != "content_jsonl"} for item in repository.list_datasets()]

    @app.get("/api/datasets/{dataset_id}")
    def get_dataset(dataset_id: str, include_content: bool = False, user: str = Depends(current_user)):
        try:
            item = repository.get_dataset(dataset_id)
        except KeyError:
            raise HTTPException(404, "数据集不存在")
        if not include_content:
            item.pop("content_jsonl", None)
        return item

    @app.post("/api/datasets", status_code=201)
    def create_dataset(value: DatasetInput, user: str = Depends(current_user)):
        raw = value.content_jsonl.encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        dataset_id = repository.save_dataset({
            "name": value.name,
            "version": digest[:12],
            "sha256": digest,
            "content_jsonl": value.content_jsonl,
            "metadata": {},
        })
        item = repository.get_dataset(dataset_id)
        item.pop("content_jsonl", None)
        return item

    @app.delete("/api/datasets/{dataset_id}", status_code=204)
    def delete_dataset(dataset_id: str, user: str = Depends(current_user)):
        if not repository.delete_resource("datasets", dataset_id):
            raise HTTPException(404, "数据集不存在")
        return Response(status_code=204)

    @app.get("/api/plans")
    def list_plans(user: str = Depends(current_user)):
        return repository.list_plans()

    @app.post("/api/plans", status_code=201)
    def create_plan(value: PlanTemplateInput, user: str = Depends(current_user)):
        _plan(value.plan)
        item_id = repository.save_plan({"name": value.name, **value.plan})
        return repository.get_plan(item_id)

    @app.delete("/api/plans/{plan_id}", status_code=204)
    def delete_plan(plan_id: str, user: str = Depends(current_user)):
        if not repository.delete_resource("plans", plan_id):
            raise HTTPException(404, "计划模板不存在")
        return Response(status_code=204)

    @app.get("/api/settings")
    def get_settings(user: str = Depends(current_user)):
        return repository.get_setting("ui", {})

    @app.put("/api/settings")
    def put_settings(value: dict[str, Any], user: str = Depends(current_user)):
        repository.set_setting("ui", redact(value))
        return repository.get_setting("ui", {})

    @app.post("/api/plans/preflight")
    def preflight(value: TaskInput, user: str = Depends(current_user)):
        plan = _plan(value.plan)
        request_config = RequestConfig(
            messages=[{"role": "user", "content": "preflight"}],
            max_output_tokens=int(value.workload.get("output_size", 128)),
        )
        try:
            risks = validate_plan(plan, [request_config], SafetyLimits(), risk_confirmed=value.risk_confirmed)
        except PermissionError as exc:
            raise HTTPException(409, str(exc))
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        return {
            "valid": True,
            "risks": risks,
            "estimated_requests": _estimated_requests(plan),
            "estimated_max_concurrency": max([plan.concurrency, *(item.concurrency for item in plan.stages)]),
        }

    def prepare_task_payload(value: TaskInput) -> dict[str, Any]:
        payload = value.model_dump(exclude={"priority", "risk_confirmed"})
        payload["workload"] = {"seed": 1, **payload.get("workload", {})}
        config_id = value.api_config_id
        if config_id:
            try:
                stored_endpoint = repository.get_api_config(config_id)
            except KeyError:
                raise HTTPException(422, "API 配置不存在")
            payload["endpoint"] = {
                key: stored_endpoint[key]
                for key in ("base_url", "model", "verify_tls", "timeout_seconds")
            }
        elif value.endpoint:
            endpoint = dict(value.endpoint)
            for required in ("base_url", "model"):
                if not endpoint.get(required):
                    raise HTTPException(422, f"endpoint.{required} 不能为空")
            temp_name = f"临时配置-{uuid.uuid4().hex[:8]}"
            config = repository.save_api_config({
                "name": temp_name,
                "base_url": endpoint["base_url"],
                "model": endpoint["model"],
                "api_key": endpoint.get("api_key", ""),
                "verify_tls": endpoint.get("verify_tls", True),
                "timeout_seconds": endpoint.get("timeout_seconds", 60),
                "is_default": False,
            })
            payload["api_config_id"] = config["id"]
            payload["endpoint"] = {
                key: endpoint[key]
                for key in ("base_url", "model", "verify_tls", "timeout_seconds")
                if key in endpoint
            }
        return payload

    @app.post("/api/tasks", status_code=202)
    def create_task(value: TaskInput, user: str = Depends(current_user)):
        preflight(value, user)
        payload = prepare_task_payload(value)
        task_id = repository.create_task(value.name, payload, priority=value.priority)
        return repository.get_task(task_id)

    @app.get("/api/tasks")
    def list_tasks(
        limit: int = Query(default=100, ge=1, le=1000),
        status_filter: str | None = Query(default=None, alias="status"),
        user: str = Depends(current_user),
    ):
        tasks = repository.list_tasks(limit)
        return [item for item in tasks if status_filter is None or item["status"] == status_filter]

    @app.get("/api/queue")
    def queue(user: str = Depends(current_user)):
        tasks = repository.list_tasks(1000)
        queued = [repository.get_task(item["id"]) for item in tasks if item["status"] == "queued"]
        queued.sort(key=lambda item: item.get("queue_position", 10**9))
        return {"running": [item for item in tasks if item["status"] in {"running", "stopping"}], "queued": queued}

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: str, user: str = Depends(current_user)):
        try:
            return repository.get_task(task_id)
        except KeyError:
            raise HTTPException(404, "任务不存在")

    @app.get("/api/tasks/{task_id}/status")
    def get_task_status(task_id: str, user: str = Depends(current_user)):
        task = get_task(task_id, user)
        return {key: task.get(key) for key in ("id", "status", "queue_position", "heartbeat_at", "updated_at", "stopped_reason")}

    @app.post("/api/tasks/{task_id}/cancel", status_code=202)
    def cancel_task(task_id: str, user: str = Depends(current_user)):
        task = get_task(task_id, user)
        if task["status"] == "queued":
            with database.transaction(immediate=True) as connection:
                connection.execute(
                    "UPDATE tasks SET status='cancelled',finished_at=?,updated_at=? WHERE id=? AND status='queued'",
                    (utc_now(), utc_now(), task_id),
                )
            return repository.get_task(task_id)
        if not repository.request_stop(task_id):
            raise HTTPException(409, "任务当前状态不可取消")
        return repository.get_task(task_id)

    @app.get("/api/tasks/{task_id}/events")
    async def task_events(
        task_id: str,
        request: Request,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
        follow: bool = Query(default=True),
        user: str = Depends(current_user),
    ):
        get_task(task_id, user)
        try:
            start = max(0, int(last_event_id or 0))
        except ValueError:
            raise HTTPException(422, "Last-Event-ID 必须是非负整数")

        async def stream():
            sequence = start
            idle = 0
            while True:
                rows = repository.events_after(task_id, sequence)
                for event in rows:
                    sequence = event["sequence"]
                    yield f"id: {sequence}\nevent: {event['event_type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                task = repository.get_task(task_id)
                if not follow or (task["status"] in TERMINAL_STATES and not rows):
                    yield f"event: terminal\ndata: {json.dumps({'status': task['status']})}\n\n"
                    break
                if await request.is_disconnected():
                    break
                idle += 1
                if idle % 15 == 0:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @app.get("/api/thresholds")
    def list_thresholds(user: str = Depends(current_user)):
        return repository.list_thresholds()

    @app.get("/api/thresholds/{item_id}")
    def get_threshold(item_id: str, user: str = Depends(current_user)):
        try:
            return repository.get_threshold(item_id)
        except KeyError:
            raise HTTPException(404, "阈值模板不存在")

    @app.post("/api/thresholds", status_code=201)
    def create_threshold(value: ThresholdInput, user: str = Depends(current_user)):
        item_id = repository.save_threshold(value.name, value.rules)
        return repository.get_threshold(item_id)

    @app.put("/api/thresholds/{item_id}")
    def update_threshold(item_id: str, value: ThresholdInput, user: str = Depends(current_user)):
        get_threshold(item_id, user)
        repository.save_threshold(value.name, value.rules, item_id)
        return repository.get_threshold(item_id)

    @app.delete("/api/thresholds/{item_id}", status_code=204)
    def delete_threshold(item_id: str, user: str = Depends(current_user)):
        if not repository.delete_resource("thresholds", item_id):
            raise HTTPException(404, "阈值模板不存在")
        return Response(status_code=204)

    @app.get("/api/tasks/{task_id}/baseline/{baseline_id}")
    def baseline(task_id: str, baseline_id: str, user: str = Depends(current_user)):
        current = get_task(task_id, user).get("result")
        previous = get_task(baseline_id, user).get("result")
        if not current or not previous:
            raise HTTPException(409, "当前任务或基线尚无结果")
        return compare_baseline(current, previous)

    @app.get("/api/tasks/{task_id}/baseline-candidates")
    def baseline_candidates(task_id: str, user: str = Depends(current_user)):
        current_task = get_task(task_id, user)
        current = current_task.get("result")
        if not current:
            raise HTTPException(409, "当前任务尚无可比较结果")
        candidates = []
        for task in repository.list_tasks(1000):
            if (
                task["id"] == task_id
                or not task.get("result")
                or task.get("status") not in TERMINAL_STATES
                or task.get("created_at", "") > current_task.get("created_at", "")
            ):
                continue
            comparison = compare_baseline(current, task["result"])
            request_snapshot = task["result"].get("request", {})
            plan_snapshot = task["result"].get("plan", {})
            candidates.append({
                "id": task["id"],
                "name": task["name"],
                "status": task["status"],
                "created_at": task["created_at"],
                "finished_at": task["finished_at"],
                "model": request_snapshot.get("model"),
                "plan_type": plan_snapshot.get("plan_type"),
                "compatible": comparison["compatible"],
                "incompatibilities": comparison["incompatibilities"],
                "conclusion": comparison["conclusion"],
            })
        return candidates

    @app.get("/api/tasks/{task_id}/baseline")
    def get_saved_baseline(task_id: str, user: str = Depends(current_user)):
        get_task(task_id, user)
        try:
            return repository.get_task_baseline(task_id)
        except KeyError:
            return None

    @app.put("/api/tasks/{task_id}/baseline")
    def save_baseline(task_id: str, value: BaselineInput, user: str = Depends(current_user)):
        if task_id == value.baseline_id:
            raise HTTPException(422, "不能将任务自身设为历史基线")
        current = get_task(task_id, user).get("result")
        previous = get_task(value.baseline_id, user).get("result")
        if not current or not previous:
            raise HTTPException(409, "当前任务或基线尚无结果")
        comparison = compare_baseline(current, previous, tolerances=value.tolerances)
        if not comparison["compatible"]:
            reasons = "；".join(comparison["incompatibilities"])
            raise HTTPException(409, f"所选历史基线不兼容：{reasons}")
        return repository.save_task_baseline(
            task_id, value.baseline_id, value.tolerances, comparison,
        )

    @app.delete("/api/tasks/{task_id}/baseline", status_code=204)
    def delete_saved_baseline(task_id: str, user: str = Depends(current_user)):
        get_task(task_id, user)
        if not repository.delete_task_baseline(task_id):
            raise HTTPException(404, "当前任务尚未选择历史基线")
        return Response(status_code=204)

    @app.post("/api/comparisons/preflight")
    def comparison_preflight(value: ComparisonInput, user: str = Depends(current_user)):
        if not value.targets or value.plan is None:
            raise HTTPException(422, "模型比较预检必须提供 targets 和 plan")
        for target in value.targets:
            if target.api_config_id:
                try:
                    repository.get_api_config(target.api_config_id)
                except KeyError:
                    raise HTTPException(422, f"比较目标 {target.name} 的 API 配置不存在")
            elif target.endpoint:
                for required in ("base_url", "model"):
                    if not target.endpoint.get(required):
                        raise HTTPException(422, f"比较目标 {target.name} 的 endpoint.{required} 不能为空")
        plan = _plan(value.plan)
        multiplier = len(value.targets) if value.mode == "synchronous" else 1
        request_config = RequestConfig(
            messages=[{"role": "user", "content": "comparison-preflight"}],
            max_output_tokens=int(value.workload.get("output_size", 128)),
        )
        scaled_plan = _scale_comparison_plan(plan, multiplier)
        try:
            risks = validate_plan(
                scaled_plan,
                [request_config],
                SafetyLimits(),
                risk_confirmed=value.risk_confirmed,
            )
        except PermissionError as exc:
            raise HTTPException(409, str(exc))
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        return {
            "valid": True,
            "risks": risks,
            "estimated_requests": _estimated_requests(plan),
            **_comparison_metadata(value, plan, value.targets),
        }

    @app.get("/api/comparisons")
    def list_comparisons(user: str = Depends(current_user)):
        return repository.list_comparisons()

    @app.get("/api/comparisons/{comparison_id}")
    def get_comparison(comparison_id: str, user: str = Depends(current_user)):
        try:
            return repository.get_comparison(comparison_id)
        except KeyError:
            raise HTTPException(404, "模型比较不存在")

    @app.post("/api/comparisons", status_code=201)
    def create_comparison(value: ComparisonInput, user: str = Depends(current_user)):
        if value.mode == "synchronous" and not value.confirm_synchronous:
            raise HTTPException(422, "同步比较必须显式确认总负载叠加")
        if value.task_ids:
            for task_id in value.task_ids:
                get_task(task_id, user)
            item_id = repository.save_comparison({
                "name": value.name,
                "mode": value.mode,
                "resource_semantics": value.resource_semantics,
                "task_ids": value.task_ids,
            })
            return repository.get_comparison(item_id)

        comparison_preflight(value, user)
        plan_data = dict(value.plan or {})
        workload = {"seed": 1, **value.workload}
        task_specs = []
        targets = value.targets or []
        for target in targets:
            task_input = TaskInput(
                name=f"{value.name} - {target.name}",
                api_config_id=target.api_config_id,
                endpoint=target.endpoint,
                plan=plan_data,
                workload=workload,
                assertions=value.assertions,
                stream=value.stream,
                formats=value.formats,
                priority=value.priority,
                risk_confirmed=value.risk_confirmed,
            )
            preflight(task_input, user)
            task_specs.append({
                "name": task_input.name,
                "target_name": target.name,
                "priority": value.priority,
                "payload": prepare_task_payload(task_input),
            })
        plan = _plan(plan_data)
        result = _comparison_metadata(value, plan, targets)
        item_id = repository.create_comparison_tasks(
            {
                "name": value.name,
                "mode": value.mode,
                "resource_semantics": value.resource_semantics,
                "result": result,
            },
            task_specs,
        )
        return repository.get_comparison(item_id)

    @app.get("/api/reports")
    def list_reports(task_id: str | None = None, user: str = Depends(current_user)):
        return repository.list_reports(task_id)

    @app.post("/api/tasks/{task_id}/reports", status_code=201)
    def generate_report(task_id: str, value: ReportGenerateInput, user: str = Depends(current_user)):
        task = get_task(task_id, user)
        if not task.get("result"):
            raise HTTPException(409, "任务尚无可导出的结果")
        artifact = _generate_summary_artifact(reports_dir, task_id, value.format, task["result"])
        report_id = repository.add_report({"task_id": task_id, **artifact})
        try:
            return repository.get_report(report_id)
        except KeyError:
            for item in repository.list_reports(task_id):
                if item["relative_path"] == artifact["relative_path"] and item["sha256"] == artifact["sha256"]:
                    return item
            raise HTTPException(500, "报告索引写入失败")

    @app.get("/api/reports/{report_id}/download")
    def download_report(report_id: str, user: str = Depends(current_user)):
        try:
            report = repository.get_report(report_id)
        except KeyError:
            raise HTTPException(404, "报告不存在")
        path = _safe_report_path(reports_dir, report["relative_path"])
        if not path.is_file():
            raise HTTPException(404, "报告文件不存在")
        media_types = {
            "json": "application/json", "html": "text/html; charset=utf-8", "csv": "text/csv; charset=utf-8",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "jsonl.gz": "application/gzip", "events.jsonl.gz": "application/gzip",
        }
        return FileResponse(path, filename=path.name, media_type=media_types.get(report["format"], "application/octet-stream"))

    @app.delete("/api/reports/{report_id}", status_code=204)
    def delete_report(report_id: str, user: str = Depends(current_user)):
        try:
            report = repository.get_report(report_id)
        except KeyError:
            raise HTTPException(404, "报告不存在")
        path = _safe_report_path(reports_dir, report["relative_path"])
        path.unlink(missing_ok=True)
        repository.delete_resource("reports", report_id)
        return Response(status_code=204)

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready():
        try:
            with database.connect() as connection:
                connection.execute("SELECT 1").fetchone()
            writable = os.access(settings.data_dir, os.W_OK)
            status_value = "ready" if writable else "degraded"
            return JSONResponse(
                {"status": status_value, "database": "ok", "data_dir_writable": writable, "configuration": "ok"},
                status_code=200 if writable else 503,
            )
        except Exception:
            return JSONResponse({"status": "not_ready", "database": "error", "configuration": "ok"}, status_code=503)

    @app.get("/api/diagnostics")
    def diagnostics(user: str = Depends(current_user)):
        with database.connect() as connection:
            executor = connection.execute("SELECT * FROM executor_state WHERE name='local-executor'").fetchone()
        executor_data = dict(executor) if executor else None
        if executor_data and executor_data.get("metadata_json"):
            executor_data["metadata"] = json.loads(executor_data.pop("metadata_json"))
        disk = shutil.disk_usage(settings.data_dir)
        load_average = None
        if hasattr(os, "getloadavg"):
            load_average = list(os.getloadavg())
        return {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "database": database.path.name,
            "database_recovered": recovery.recovered,
            "legacy_migration": migration,
            "executor": executor_data,
            "disk": {"total": disk.total, "used": disk.used, "free": disk.free},
            "generator": {"cpu_count": os.cpu_count(), "load_average": load_average},
            "network_check": "skipped",
        }

    app.state.frontend_dir = frontend_dir

    def frontend_file(relative_path: str) -> FileResponse:
        candidate = (frontend_dir / relative_path).resolve()
        if candidate != frontend_dir and frontend_dir not in candidate.parents:
            raise HTTPException(404, "页面不存在")
        if not candidate.is_file():
            raise HTTPException(404, "前端资源不存在")
        return FileResponse(candidate)

    @app.get("/", include_in_schema=False)
    def frontend_index():
        index_path = frontend_dir / "index.html"
        if not index_path.is_file():
            raise HTTPException(404, "前端资源尚未构建")
        return FileResponse(index_path, media_type="text/html")

    @app.get("/assets/{asset_path:path}", include_in_schema=False)
    def frontend_asset(asset_path: str):
        return frontend_file(f"assets/{asset_path}")

    return app


def _plan(value: Mapping[str, Any]) -> BenchmarkPlan:
    """把 API 字典转换成领域计划并校验计划类型的必需字段。"""

    try:
        stages = tuple(
            StageConfig(
                name=str(item.get("name", f"stage-{index + 1}")),
                concurrency=int(item["concurrency"]),
                requests=int(item["requests"]) if item.get("requests") is not None else None,
                duration_seconds=float(item["duration_seconds"]) if item.get("duration_seconds") is not None else None,
                target_qps=float(item["target_qps"]) if item.get("target_qps") is not None else None,
            )
            for index, item in enumerate(value.get("stages") or ())
        )
        plan = BenchmarkPlan(
            str(value.get("name", "benchmark")),
            PlanType(value.get("plan_type", "fixed_concurrency")),
            int(value.get("concurrency", 1)),
            int(value["total_requests"]) if value.get("total_requests") is not None else None,
            float(value["duration_seconds"]) if value.get("duration_seconds") is not None else None,
            float(value["target_qps"]) if value.get("target_qps") is not None else None,
            float(value.get("warmup_seconds", 0)),
            float(value.get("cooldown_seconds", 0)),
            stages,
            dict(value.get("metadata") or {}),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(422, f"计划参数无效: {exc}")
    if plan.concurrency <= 0:
        raise HTTPException(422, "并发必须大于 0")
    if plan.total_requests is not None and plan.total_requests <= 0:
        raise HTTPException(422, "请求数必须大于 0")
    if plan.duration_seconds is not None and plan.duration_seconds <= 0:
        raise HTTPException(422, "时长必须大于 0")
    if plan.target_qps is not None and plan.target_qps <= 0:
        raise HTTPException(422, "QPS 必须大于 0")
    if plan.warmup_seconds < 0 or plan.cooldown_seconds < 0:
        raise HTTPException(422, "预热和冷却时长不能为负数")
    if plan.plan_type in {PlanType.SMOKE, PlanType.BASELINE, PlanType.FIXED_CONCURRENCY} and plan.total_requests is None:
        raise HTTPException(422, "该计划必须设置 total_requests")
    if plan.plan_type is PlanType.CONSTANT_RATE and (plan.target_qps is None or (plan.total_requests is None and plan.duration_seconds is None)):
        raise HTTPException(422, "恒定 QPS 计划必须设置 target_qps，并设置请求数或时长")
    if plan.plan_type is PlanType.STABILITY and plan.duration_seconds is None:
        raise HTTPException(422, "稳定性计划必须设置 duration_seconds")
    if plan.plan_type is PlanType.STEPPED:
        if not plan.stages:
            raise HTTPException(422, "阶梯计划必须至少包含一个阶段")
        for stage in plan.stages:
            if stage.concurrency <= 0 or (stage.requests is None and stage.duration_seconds is None):
                raise HTTPException(422, "每个阶梯阶段必须有正并发，并设置请求数或时长")
            if stage.requests is not None and stage.requests <= 0:
                raise HTTPException(422, "阶梯阶段请求数必须大于 0")
            if stage.duration_seconds is not None and stage.duration_seconds <= 0:
                raise HTTPException(422, "阶梯阶段时长必须大于 0")
    return plan


def _estimated_requests(plan: BenchmarkPlan) -> int | None:
    if plan.stages and all(item.requests is not None for item in plan.stages):
        return sum(int(item.requests or 0) for item in plan.stages)
    return plan.total_requests


def _scale_comparison_plan(plan: BenchmarkPlan, multiplier: int) -> BenchmarkPlan:
    """将同步比较的每模型计划换算为总生成负载，用于安全校验。"""

    stages = tuple(
        StageConfig(
            stage.name,
            stage.concurrency * multiplier,
            stage.requests * multiplier if stage.requests is not None else None,
            stage.duration_seconds,
            stage.target_qps * multiplier if stage.target_qps is not None else None,
        )
        for stage in plan.stages
    )
    return BenchmarkPlan(
        plan.name,
        plan.plan_type,
        plan.concurrency * multiplier,
        plan.total_requests * multiplier if plan.total_requests is not None else None,
        plan.duration_seconds,
        plan.target_qps * multiplier if plan.target_qps is not None else None,
        plan.warmup_seconds,
        plan.cooldown_seconds,
        stages,
        plan.metadata,
    )


def _comparison_metadata(
    value: ComparisonInput,
    plan: BenchmarkPlan,
    targets: list[ComparisonTargetInput],
) -> dict[str, Any]:
    """生成可解释的模型比较负载元数据，不计算不透明综合分。"""

    concurrency = max([plan.concurrency, *(stage.concurrency for stage in plan.stages)])
    qps_values = [
        qps for qps in [plan.target_qps, *(stage.target_qps for stage in plan.stages)]
        if qps is not None
    ]
    target_qps = max(qps_values) if qps_values else None
    per_model_loads = [
        {
            "target_name": target.name,
            "max_concurrency": concurrency,
            "target_qps": target_qps,
            "estimated_requests": _estimated_requests(plan),
        }
        for target in targets
    ]
    multiplier = len(targets) if value.mode == "synchronous" else 1
    warning = None
    if value.resource_semantics == "shared":
        warning = "结果包含共享资源竞争效应，不应解释为彼此独立的模型容量。"
    return {
        "schema_version": "1.0",
        "comparison_mode": value.mode,
        "resource_semantics": value.resource_semantics,
        "same_workload_order": True,
        "per_model_loads": per_model_loads,
        "aggregate_load": {
            "active_models": multiplier,
            "max_concurrency": concurrency * multiplier,
            "target_qps": target_qps * multiplier if target_qps is not None else None,
        },
        "warning": warning,
    }


def _safe_report_path(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    resolved_root = root.resolve()
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise HTTPException(400, "非法报告路径")
    return candidate


def _generate_summary_artifact(root: Path, task_id: str, format_name: str, result: Mapping[str, Any]) -> dict[str, Any]:
    """从已持久化摘要生成不依赖样本明细的按需制品。"""

    root.mkdir(parents=True, exist_ok=True)
    safe_result = redact(dict(result))
    target = root / f"{task_id}.summary.{format_name}"
    if format_name == "json":
        target.write_text(json.dumps(safe_result, ensure_ascii=False, indent=2), encoding="utf-8")
    elif format_name == "html":
        encoded = json.dumps(safe_result, ensure_ascii=False).replace("</", "<\\/")
        title = str(safe_result.get("plan", {}).get("name") or task_id)
        target.write_text(
            "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{_html_escape(title)} · LLM Benchmark</title><style>body{{font:14px Arial,'Microsoft YaHei',sans-serif;background:#eef3f3;color:#23343c;margin:0}}header{{background:#142831;color:#fff;padding:24px}}main{{max-width:1080px;margin:24px auto;padding:0 20px}}pre{{background:#fff;padding:18px;border-left:4px solid #00989b;overflow:auto;white-space:pre-wrap}}</style></head>"
            f"<body><header><h1>{_html_escape(title)}</h1><p>离线压测摘要</p></header><main><pre id='summary'></pre></main><script>const D={encoded};document.getElementById('summary').textContent=JSON.stringify(D,null,2)</script></body></html>",
            encoding="utf-8",
        )
    elif format_name == "csv":
        with target.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["指标", "值"])
            for key, value in _flatten_mapping(safe_result):
                writer.writerow([key, json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value])
    elif format_name == "xlsx":
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "摘要"
        sheet.append(["指标", "值"])
        for key, value in _flatten_mapping(safe_result):
            sheet.append([key, json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value])
        workbook.save(target)
    else:
        raise HTTPException(422, "不支持按需生成该报告格式")
    raw = target.read_bytes()
    return {"format": format_name, "relative_path": target.name, "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def _flatten_mapping(value: Mapping[str, Any], prefix: str = ""):
    for key, item in value.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, Mapping):
            yield from _flatten_mapping(item, name)
        else:
            yield name, item


def _html_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _error(request: Request, status_code: int, code: str, message: str, details: Any = None):
    return JSONResponse(
        status_code=status_code,
        content=redact({
            "error": {"code": code, "message": message, "details": _json_safe(details)},
            "request_id": getattr(request.state, "request_id", None),
        }),
    )

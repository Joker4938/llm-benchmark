"""无需 Web 服务即可运行的 LLM Benchmark 命令行入口。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import signal
import sys
import uuid
from pathlib import Path
from typing import Any, Sequence

from benchmark_core import (
    AssertionSpec,
    AssertionType,
    AutoStopPolicy,
    BenchmarkPlan,
    BenchmarkScheduler,
    CancellationToken,
    EndpointConfig,
    OpenAIChatClient,
    PlanType,
    RequestConfig,
    SafetyLimits,
    StageConfig,
    ThresholdStatus,
    WorkloadDimensions,
    aggregate_time_windows,
    apply_validation,
    build_summary,
    builtin_dataset,
    compare_baseline,
    custom_dataset,
    evaluate_thresholds,
    load_jsonl,
    parse_threshold,
    sample_records,
    validate_plan,
    write_report_bundle,
)
from benchmark_core.redaction import redact

from .config import load_config, resolve, resolve_secret

EXIT_OK = 0
EXIT_INVALID = 2
EXIT_EXECUTION = 3
EXIT_THRESHOLD = 4
EXIT_INTERRUPTED = 130


def build_parser() -> argparse.ArgumentParser:
    """创建稳定的多命令 CLI 解析器。"""

    parser = argparse.ArgumentParser(
        prog="llm-benchmark",
        description="OpenAI 兼容接口离线性能压测工具",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="运行一个性能测试计划")
    _add_run_arguments(run)

    compare = sub.add_parser("compare", help="使用相同负载顺序比较两个或更多模型")
    _add_run_arguments(compare)
    compare.add_argument(
        "--target",
        action="append",
        required=True,
        help="目标：名称|Base URL|模型|API_KEY环境变量，至少提供两次",
    )
    compare.add_argument(
        "--comparison-mode",
        choices=["sequential", "synchronous"],
        default="sequential",
    )
    compare.add_argument(
        "--resource-semantics",
        choices=["independent", "shared"],
        required=True,
        help="声明独立端点测量或共享资源竞争测试",
    )
    compare.add_argument(
        "--confirm-synchronous",
        action="store_true",
        help="确认同步模式的总负载等于所有模型负载之和",
    )

    validate = sub.add_parser("validate-dataset", help="校验 messages JSONL 数据集")
    validate.add_argument("path")

    diagnose = sub.add_parser("diagnose", help="检查本机运行环境，不访问互联网")
    diagnose.add_argument("--output-dir", default="reports")
    return parser


def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", help="JSON 配置文件")
    parser.add_argument("--plan", choices=[item.value for item in PlanType])
    parser.add_argument("--name")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key", help="建议改用 LLM_BENCHMARK_API_KEY 环境变量")
    parser.add_argument("--model")
    parser.add_argument(
        "--insecure",
        action="store_true",
        default=None,
        help="关闭 TLS 校验（仅限明确的内网测试）",
    )
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--requests", type=int)
    parser.add_argument("--duration", type=float)
    parser.add_argument("--concurrency", type=int)
    parser.add_argument("--qps", type=float)
    parser.add_argument(
        "--stage",
        action="append",
        help="阶梯：名称:并发:请求数，或 名称:并发:请求数:QPS",
    )
    parser.add_argument("--warmup", type=float)
    parser.add_argument("--cooldown", type=float)
    parser.add_argument("--output-tokens", type=int)
    parser.add_argument("--prompt-type", choices=["general", "structured", "reasoning"])
    parser.add_argument("--input-size", choices=["short", "medium", "long"])
    parser.add_argument("--system-message")
    parser.add_argument("--user-message")
    parser.add_argument("--dataset")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--non-stream", action="store_true", default=None)
    parser.add_argument("--assertions", help="断言 JSON 文件")
    parser.add_argument("--threshold", action="append", help="指标:运算符:数值，可重复")
    parser.add_argument("--baseline", help="历史任务 JSON 摘要文件")
    parser.add_argument("--risk-confirmed", action="store_true", default=None)
    parser.add_argument("--max-error-rate", type=float)
    parser.add_argument("--max-p95-latency", type=float)
    parser.add_argument("--stop-windows", type=int)
    parser.add_argument(
        "--format",
        action="append",
        choices=["json", "jsonl.gz", "events.jsonl.gz", "html", "xlsx", "csv"],
    )
    parser.add_argument("--output-dir")
    parser.add_argument("--json", action="store_true", help="只向 stdout 输出机器可读 JSON")
    parser.add_argument("--quiet", action="store_true")


async def execute_run(
    args: argparse.Namespace,
    *,
    cancellation: CancellationToken | None = None,
    install_signal_handlers: bool = True,
) -> tuple[int, dict[str, Any]]:
    """执行一次 CLI 压测，并在取消时保留部分结果与报告。"""

    config = load_config(args.config)
    plan_type = PlanType(resolve(args.plan, config, "plan.type", PlanType.FIXED_CONCURRENCY.value))
    endpoint = EndpointConfig(
        base_url=_required(
            resolve(args.base_url, config, "endpoint.base_url", os.environ.get("LLM_BENCHMARK_BASE_URL")),
            "base-url",
        ),
        model=_required(
            resolve(args.model, config, "endpoint.model", os.environ.get("LLM_BENCHMARK_MODEL")),
            "model",
        ),
        api_key=resolve_secret(args.api_key, config),
        verify_tls=not bool(resolve(args.insecure, config, "endpoint.insecure", False)),
        timeout_seconds=float(resolve(args.timeout, config, "endpoint.timeout", 60.0)),
    )
    dimensions = WorkloadDimensions(
        resolve(args.prompt_type, config, "workload.prompt_type", "general"),
        resolve(args.input_size, config, "workload.input_size", "short"),
        int(resolve(args.output_tokens, config, "workload.output_tokens", 128)),
    )
    dataset = _dataset(args, config, dimensions)
    duration_value = resolve(args.duration, config, "plan.duration")
    request_value = resolve(args.requests, config, "plan.requests")
    if request_value is None and duration_value is None and plan_type is not PlanType.STEPPED:
        request_value = 3 if plan_type is PlanType.SMOKE else 10
    stages = _stages(args.stage or resolve(None, config, "plan.stages", []))
    plan = BenchmarkPlan(
        name=resolve(args.name, config, "name", f"cli-{plan_type.value}"),
        plan_type=plan_type,
        concurrency=int(resolve(args.concurrency, config, "plan.concurrency", 1)),
        total_requests=int(request_value) if request_value is not None else None,
        duration_seconds=float(duration_value) if duration_value is not None else None,
        target_qps=_optional_float(resolve(args.qps, config, "plan.qps")),
        warmup_seconds=float(resolve(args.warmup, config, "plan.warmup", 0)),
        cooldown_seconds=float(resolve(args.cooldown, config, "plan.cooldown", 0)),
        stages=stages,
        metadata={"source": "cli"},
    )
    _validate_plan_shape(plan)

    sample_count = plan.total_requests or max(1, len(dataset.records))
    seed = int(resolve(args.seed, config, "workload.seed", 1))
    records, snapshot = sample_records(dataset, sample_count, seed, dimensions)
    stream = not bool(resolve(args.non_stream, config, "request.non_stream", False))
    request_configs = [record.to_request(dimensions, stream=stream) for record in records]
    validate_plan(
        plan,
        request_configs,
        SafetyLimits(),
        risk_confirmed=bool(resolve(args.risk_confirmed, config, "safety.risk_confirmed", False)),
    )
    assertions = _assertions(resolve(args.assertions, config, "validation.file"))
    threshold_values = args.threshold or resolve(None, config, "thresholds", [])
    threshold_rules = tuple(parse_threshold(value) for value in threshold_values)

    cancellation = cancellation or CancellationToken()
    loop = asyncio.get_running_loop()
    installed_signals: list[int] = []
    if install_signal_handlers:
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, cancellation.cancel)
                installed_signals.append(signum)
            except (NotImplementedError, RuntimeError):
                pass

    task_id = str(uuid.uuid4())
    try:
        async with OpenAIChatClient(
            endpoint,
            max_connections=max(10, plan.concurrency + 10),
        ) as client:

            async def executor(request: RequestConfig, request_id: str, started: float):
                sample = await client.request(
                    request,
                    request_id=request_id,
                    task_started_at=started,
                )
                return apply_validation(sample, assertions)[0]

            async def progress(event):
                if (
                    not args.quiet
                    and not args.json
                    and event.event_type
                    in {"request_completed", "request_scheduled", "stage_started", "automatic_stop"}
                ):
                    print(
                        f"[{event.phase.value}] {event.event_type} {dict(event.payload)}",
                        file=sys.stderr,
                    )

            policy = _auto_stop_policy(args, config)
            result = await BenchmarkScheduler(executor).run(
                plan,
                request_configs,
                cancellation=cancellation,
                event_sink=progress,
                auto_stop=policy,
            )
    finally:
        for signum in installed_signals:
            loop.remove_signal_handler(signum)

    interrupted = cancellation.cancelled and result.stopped_reason is None
    summary = build_summary(task_id, plan, result.samples, result.elapsed)
    windows = aggregate_time_windows(result.samples, total_elapsed=result.elapsed)
    formats = args.format or resolve(None, config, "reports.formats", ["json", "jsonl.gz"])
    artifacts = write_report_bundle(
        resolve(args.output_dir, config, "reports.output_dir", "reports"),
        summary,
        result.samples,
        result.events,
        windows,
        formats,
    )
    payload = summary.to_dict()
    payload.update(
        {
            "workload": redact(
                {
                    "dataset": dataset.name,
                    "version": dataset.version,
                    "sha256": dataset.sha256,
                    "seed": snapshot.seed,
                    "selected_record_ids": snapshot.selected_record_ids,
                    "dimensions": {
                        "prompt_type": dimensions.prompt_type,
                        "input_size": dimensions.input_size,
                        "output_tokens": dimensions.output_size,
                    },
                }
            ),
            "artifacts": [
                redact(
                    {
                        "format": item.format,
                        "path": item.relative_path,
                        "size_bytes": item.size_bytes,
                        "sha256": item.sha256,
                    }
                )
                for item in artifacts
            ],
            "stopped_reason": result.stopped_reason,
        }
    )
    threshold_results = evaluate_thresholds(summary, threshold_rules)
    payload["thresholds"] = [item.to_dict() for item in threshold_results]
    if args.baseline:
        baseline = _load_json_object(args.baseline, "基线")
        payload["baseline_comparison"] = compare_baseline(payload, baseline)

    if interrupted:
        return EXIT_INTERRUPTED, payload
    if any(item.status is ThresholdStatus.FAILED for item in threshold_results):
        return EXIT_THRESHOLD, payload
    if summary.completed_requests and summary.transport_successes == 0:
        return EXIT_EXECUTION, payload
    return EXIT_OK, payload


async def execute_compare(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    """顺序或同步运行模型比较，并复用相同种子和数据集顺序。"""

    targets = [_parse_target(value) for value in args.target]
    if len(targets) < 2:
        raise ValueError("模型比较至少需要两个 --target")
    if args.comparison_mode == "synchronous" and not args.confirm_synchronous:
        raise PermissionError("同步比较会叠加总负载，必须提供 --confirm-synchronous")

    async def run_target(target: dict[str, str]) -> tuple[int, dict[str, Any]]:
        values = vars(args).copy()
        values.update(
            command="run",
            name=f"{args.name or 'compare'}-{target['name']}",
            base_url=target["base_url"],
            model=target["model"],
            api_key=os.environ.get(target["api_key_env"], "") if target.get("api_key_env") else args.api_key,
        )
        code, payload = await execute_run(
            argparse.Namespace(**values),
            install_signal_handlers=False,
        )
        return code, {"target": target["name"], "result": payload}

    if args.comparison_mode == "synchronous":
        completed = list(await asyncio.gather(*(run_target(target) for target in targets)))
    else:
        completed = []
        for target in targets:
            completed.append(await run_target(target))

    codes = [item[0] for item in completed]
    runs = [item[1] for item in completed]
    concurrency = int(args.concurrency or 1)
    qps = args.qps
    warning = None
    if args.resource_semantics == "shared":
        warning = "结果包含共享资源竞争效应，不应解释为彼此独立的模型容量。"
    payload = {
        "schema_version": "1.0",
        "comparison_mode": args.comparison_mode,
        "resource_semantics": args.resource_semantics,
        "same_workload_order": True,
        "per_model_concurrency": concurrency,
        "aggregate_concurrency": concurrency * len(targets) if args.comparison_mode == "synchronous" else concurrency,
        "per_model_target_qps": qps,
        "aggregate_target_qps": (qps * len(targets)) if qps is not None and args.comparison_mode == "synchronous" else qps,
        "warning": warning,
        "runs": runs,
    }
    if EXIT_INTERRUPTED in codes:
        return EXIT_INTERRUPTED, payload
    if EXIT_EXECUTION in codes:
        return EXIT_EXECUTION, payload
    if EXIT_THRESHOLD in codes:
        return EXIT_THRESHOLD, payload
    return EXIT_OK, payload


def main(argv: Sequence[str] | None = None) -> int:
    """运行 CLI 并返回稳定进程退出码。"""

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "validate-dataset":
            dataset = load_jsonl(args.path)
            print(
                json.dumps(
                    {
                        "name": dataset.name,
                        "version": dataset.version,
                        "sha256": dataset.sha256,
                        "records": len(dataset.records),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return EXIT_OK
        if args.command == "diagnose":
            output = Path(args.output_dir)
            output.mkdir(parents=True, exist_ok=True)
            print(
                json.dumps(
                    {
                        "python": sys.version.split()[0],
                        "platform": platform.platform(),
                        "output_dir": str(output.resolve()),
                        "writable": os.access(output, os.W_OK),
                        "network_check": "skipped",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return EXIT_OK

        if args.command == "compare":
            code, payload = asyncio.run(execute_compare(args))
        else:
            code, payload = asyncio.run(execute_run(args))
        if args.json:
            print(json.dumps(redact(payload), ensure_ascii=False, indent=2))
        else:
            _print_human_summary(payload, comparison=args.command == "compare")
        return code
    except KeyboardInterrupt:
        print("压测已被用户中断", file=sys.stderr)
        return EXIT_INTERRUPTED
    except (ValueError, PermissionError) as exc:
        print(f"配置错误: {redact(str(exc))}", file=sys.stderr)
        return EXIT_INVALID
    except Exception as exc:  # CLI 边界必须转换为稳定退出码
        print(f"执行失败: {redact(str(exc))}", file=sys.stderr)
        return EXIT_EXECUTION


def _dataset(args, config, dimensions):
    path = resolve(args.dataset, config, "workload.dataset")
    user = resolve(args.user_message, config, "workload.user_message")
    if path:
        return load_jsonl(path)
    if user:
        return custom_dataset(
            resolve(args.system_message, config, "workload.system_message"),
            user,
        )
    return builtin_dataset(dimensions.prompt_type, dimensions.input_size)


def _stages(values) -> tuple[StageConfig, ...]:
    result = []
    for index, value in enumerate(values or []):
        if isinstance(value, dict):
            result.append(
                StageConfig(
                    str(value.get("name", f"stage-{index + 1}")),
                    int(value["concurrency"]),
                    _optional_int(value.get("requests")),
                    _optional_float(value.get("duration")),
                    _optional_float(value.get("qps")),
                )
            )
        else:
            parts = str(value).split(":")
            if len(parts) not in {3, 4}:
                raise ValueError("stage 格式必须为 名称:并发:请求数[:QPS]")
            result.append(
                StageConfig(
                    parts[0],
                    int(parts[1]),
                    requests=int(parts[2]),
                    target_qps=float(parts[3]) if len(parts) == 4 else None,
                )
            )
    return tuple(result)


def _assertions(path):
    if not path:
        return ()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("断言文件必须是 JSON 数组")
    return tuple(
        AssertionSpec(
            AssertionType(item["type"]),
            item.get("value"),
            item.get("options", {}),
        )
        for item in data
    )


def _auto_stop_policy(args, config) -> AutoStopPolicy | None:
    max_error = resolve(args.max_error_rate, config, "safety.max_error_rate")
    max_latency = resolve(args.max_p95_latency, config, "safety.max_p95_latency")
    if max_error is None and max_latency is None:
        return None
    return AutoStopPolicy(
        max_error_rate=_optional_float(max_error),
        max_p95_latency=_optional_float(max_latency),
        consecutive_windows=int(resolve(args.stop_windows, config, "safety.stop_windows", 3)),
    )


def _validate_plan_shape(plan: BenchmarkPlan) -> None:
    if plan.concurrency <= 0:
        raise ValueError("concurrency 必须大于 0")
    if plan.total_requests is not None and plan.total_requests <= 0:
        raise ValueError("requests 必须大于 0")
    if plan.duration_seconds is not None and plan.duration_seconds <= 0:
        raise ValueError("duration 必须大于 0")
    if plan.plan_type is PlanType.STEPPED and not plan.stages:
        raise ValueError("stepped 计划至少需要一个 --stage")
    if plan.plan_type is PlanType.CONSTANT_RATE and not plan.target_qps:
        raise ValueError("constant_rate 计划必须提供 --qps")
    if plan.plan_type is PlanType.STABILITY and plan.duration_seconds is None:
        raise ValueError("stability 计划必须提供 --duration")
    if plan.total_requests is None and plan.duration_seconds is None and plan.plan_type is not PlanType.STEPPED:
        raise ValueError("计划必须提供 --requests 或 --duration")


def _parse_target(value: str) -> dict[str, str]:
    parts = [item.strip() for item in value.split("|")]
    if len(parts) not in {3, 4} or not all(parts[:3]):
        raise ValueError("target 格式必须为 名称|Base URL|模型[|API_KEY环境变量]")
    return {
        "name": parts[0],
        "base_url": parts[1],
        "model": parts[2],
        "api_key_env": parts[3] if len(parts) == 4 else "",
    }


def _load_json_object(path: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取{label}文件 {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label}文件根节点必须是 JSON 对象")
    return value


def _print_human_summary(payload: dict[str, Any], *, comparison: bool = False) -> None:
    if comparison:
        print(
            f"模型比较完成：{len(payload.get('runs', []))} 个目标，"
            f"模式={payload.get('comparison_mode')}，资源语义={payload.get('resource_semantics')}"
        )
        if payload.get("warning"):
            print(f"警告：{payload['warning']}")
        for item in payload.get("runs", []):
            result = item.get("result", {})
            print(
                f"- {item.get('target')}: QPS={_fmt(result.get('achieved_qps'))}, "
                f"P95={_fmt((result.get('latency') or {}).get('p95'))}s, "
                f"成功={result.get('transport_successes', 0)}/{result.get('completed_requests', 0)}"
            )
        return
    print(f"任务 {payload.get('task_id')} 完成")
    print(
        f"请求={payload.get('completed_requests', 0)}/{payload.get('total_requests', 0)}  "
        f"成功={payload.get('transport_successes', 0)}  "
        f"QPS={_fmt(payload.get('achieved_qps'))}  "
        f"P95={_fmt((payload.get('latency') or {}).get('p95'))}s  "
        f"TPS={_fmt(payload.get('aggregate_output_tps'))}"
    )
    if payload.get("stopped_reason"):
        print(f"自动停止：{payload['stopped_reason']}")
    for item in payload.get("thresholds", []):
        print(f"阈值 {item['metric']}: {item['status']} ({item['message']})")
    for item in payload.get("artifacts", []):
        print(f"报告 {item['format']}: {item['path']}")


def _fmt(value: Any) -> str:
    return "N/A" if value is None else f"{float(value):.3f}"


def _required(value, name):
    if value in {None, ""}:
        raise ValueError(f"缺少必填参数 --{name}")
    return str(value)


def _optional_float(value: Any) -> float | None:
    return float(value) if value is not None else None


def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None

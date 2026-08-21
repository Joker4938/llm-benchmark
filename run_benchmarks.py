"""旧版多档位压测入口的兼容包装。"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from llm_benchmark import run_benchmark

DEFAULT_STAGES = (
    {"num_requests": 10, "concurrency": 1, "output_tokens": 100},
    {"num_requests": 100, "concurrency": 50, "output_tokens": 100},
    {"num_requests": 200, "concurrency": 100, "output_tokens": 100},
    {"num_requests": 400, "concurrency": 200, "output_tokens": 100},
    {"num_requests": 600, "concurrency": 300, "output_tokens": 100},
)


async def run_all_benchmarks(
    llm_url: str,
    api_key: str,
    model: str,
    use_long_context: bool,
) -> list[dict[str, Any]]:
    """顺序执行旧版预设档位，内部复用新的压测核心。"""

    all_results = []
    for index, config in enumerate(DEFAULT_STAGES):
        print(f"运行并发档位 {config['concurrency']}...")
        all_results.append(
            await run_benchmark(
                config["num_requests"],
                config["concurrency"],
                30,
                config["output_tokens"],
                llm_url,
                api_key,
                model,
                use_long_context,
            )
        )
        if index < len(DEFAULT_STAGES) - 1:
            await asyncio.sleep(5)
    return all_results


def analyze_results(all_results: list[dict[str, Any]]):
    """保留旧调用方使用的汇总返回结构。"""

    summary = []
    total_tokens = 0
    total_time = 0.0
    for result in all_results:
        total = max(1, int(result.get("total_requests", 0)))
        successes = int(result.get("successful_requests", 0))
        summary.append(
            [
                result.get("concurrency", 0),
                f"{float(result.get('requests_per_second') or 0):.2f}",
                f"{float((result.get('latency') or {}).get('average') or 0):.3f}",
                f"{float((result.get('latency') or {}).get('p99') or 0):.3f}",
                f"{float((result.get('tokens_per_second') or {}).get('average') or 0):.2f}",
                f"{float((result.get('time_to_first_token') or {}).get('average') or 0):.3f}",
                f"{successes / total * 100:.1f}%",
            ]
        )
        total_tokens += int(result.get("total_output_tokens", 0))
        total_time += float(result.get("total_time", 0))
    return summary, total_tokens, total_time


def print_summary(all_results, model_name, use_long_context):
    summary, total_tokens, total_time = analyze_results(all_results)
    print(f"模型: {model_name}；负载: {'长文本' if use_long_context else '短文本'}")
    print("并发\tQPS\t平均延迟\tP99\t平均TPS\tTTFT\t成功率")
    for row in summary:
        print("\t".join(str(value) for value in row))
    print(f"累计 Token: {total_tokens}；累计耗时: {total_time:.2f}s")


def main() -> int:
    parser = argparse.ArgumentParser(description="兼容版多档位 LLM 压测")
    parser.add_argument("--llm_url", required=True)
    parser.add_argument("--api_key", default="")
    parser.add_argument("--model", required=True)
    parser.add_argument("--use_long_context", action="store_true")
    parser.add_argument("--output_dir", default="reports")
    args = parser.parse_args()
    results = asyncio.run(
        run_all_benchmarks(args.llm_url, args.api_key, args.model, args.use_long_context)
    )
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    target = output / f"legacy-gradient-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    target.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print_summary(results, args.model, args.use_long_context)
    print(f"详细结果: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# CLAUDE.md

This file provides guidance to Claude Code when working in this repository.

## Project overview

LLM-Benchmark is a Chinese-language, single-node, fully offline performance testing tool for OpenAI-compatible Chat Completions APIs. Web and CLI share the same benchmark core. It measures latency, TTFT, token throughput, request throughput, transport/validation success, threshold results, resource observations, and reproducibility metadata.

## Commands

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
python -m benchmark_cli --help

# Required for production entrypoints
export LLM_BENCHMARK_USER=admin
export LLM_BENCHMARK_PASSWORD='replace-me'
export LLM_BENCHMARK_SESSION_SECRET='replace-with-at-least-16-characters'

# Run these in separate terminals for local Web development
python -m benchmark_server.executor_main
python -m uvicorn benchmark_server.main:app --host 127.0.0.1 --port 8080

cd frontend
pnpm install --frozen-lockfile
pnpm run serve
pnpm run lint
pnpm run build

docker compose up -d --build
docker compose down
```

Use `install.sh`, `run.sh`, `stop.sh`, and `update.sh` for the offline deployment lifecycle documented under `docs/`.

## Architecture

- `benchmark_core/`: shared client, workloads, schedulers, metrics, validation, thresholds, and report generation.
- `benchmark_cli/`: primary browser-independent CLI. The root `llm_benchmark.py` and `run_benchmarks.py` remain compatibility wrappers.
- `benchmark_server/`: FastAPI application, signed-cookie login, SQLite repository, encrypted API keys, resilient legacy migration, persistent task queue, SSE, and local executor.
- `frontend/`: Vue 3 + Vite + Pinia + Vue Router with local styles and no CDN/runtime internet dependency.
- `Dockerfile` and `docker-compose.yml`: one runtime container supervising the API and executor; no Redis, external database, or cloud service.

The default scheduler allows one benchmark task at a time. Model comparisons run sequentially unless the user explicitly confirms synchronous aggregate load.

## Compatibility and security

- Only OpenAI-compatible Chat Completions is in scope.
- Runtime and deployment must work without internet access.
- Browser target: Chrome 80+ and Firefox 78+; Windows 7 acceptance focuses on Chrome 109 and Firefox 115 ESR. IE is unsupported.
- Never log, return, export, or commit complete API keys, passwords, session secrets, `.env` files, local databases, reports, archives, `frontend/dist/`, or `node_modules/`.
- Legacy `backend/api_configs.json` and `reports/` are optional migration inputs; their absence must never block startup.

## Conventions

- 用户显式要求优先于以下项目约定。
- `AGENTS.md` 是协作、分支、提交和注释规范的唯一完整来源；不要在本文件重复这些规则。
- 涉及功能、行为或接口变更时，先检查并遵循适用的 active OpenSpec change。
- 运行方式、架构与入口以 `README.md` 和当前代码为准；不要根据历史实现推断新的运行方式。
- Keep commits focused and validate each small closure before committing.
- Run CodeGraph first when `.codegraph/` exists and code location or call paths need investigation.
- Use Chinese for user-facing UI and documentation unless a protocol field requires English.

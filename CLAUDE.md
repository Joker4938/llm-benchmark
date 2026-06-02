# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

LLM-Benchmark is an async high-concurrency stress testing tool for LLM API services. It measures throughput (RPS/TPS), latency percentiles (P50/P95/P99), Time to First Token (TTFT), and success rates. The project is a Chinese-language tool; README and most user-facing content is in Chinese.

## Commands

### Backend (Python/Flask)
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -r backend/requirements.txt
python backend/app.py          # Flask on port 5000
```

### Frontend (Vue 2 / Element UI)
```bash
cd frontend
npm install
npm run serve                  # Dev server on port 8080, proxies /api to localhost:6000
npm run build                  # Outputs to frontend/dist/ (committed to repo)
npm run lint                   # ESLint
```

### Docker
```bash
docker compose up -d --build   # http://localhost:5000
docker compose down
```

### CLI Benchmark (no web UI)
```bash
python llm_benchmark.py --llm_url "http://..." --model "model" --num_requests 50 --concurrency 10
python run_benchmarks.py --llm_url "http://..." --model "model"
```

There is no test framework configured (no pytest, Jest, etc.).

## Architecture

**Monolith container**: Flask serves the Vue 2 SPA as static files and provides the REST API. Frontend is pre-built into `frontend/dist/` and committed to the repo.

**Backend** (`backend/app.py`): Single Flask app with JWT auth (Flask-JWT-Extended). Only one benchmark runs at a time — a global `task_state` dict with `threading.Lock` returns HTTP 409 if busy. Benchmarks run in a daemon thread calling `asyncio.run()` over the async engine.

**Benchmark engine** (`llm_benchmark.py`): Uses `AsyncOpenAI` + `httpx.AsyncClient` (connection pool = concurrency + 100), `asyncio.Semaphore` for concurrency, and `asyncio.Queue` with worker coroutines. Streaming responses are parsed for TTFT measurement. `run_benchmarks.py` adds multi-stage gradient testing on top.

**Frontend** (`frontend/src/`): Vue 2 + Vue Router (hash mode) + Element UI + ECharts. Three views: Login, Home (benchmark config + execution), History (report management). Axios instance in `api/index.js` handles JWT injection and 401 redirects. Dev server proxies `/api` to port 6000 (configurable in `vue.config.js`).

**API endpoints** (`/api/`, JWT-protected except login):
- `POST /api/auth/login`, `GET /api/auth/verify`
- `POST /api/benchmark/run` — single benchmark
- `POST /api/benchmark/run-gradient` — multi-stage gradient benchmark
- `GET /api/task/status` — poll task progress (frontend polls every 2s)
- `GET /api/reports`, `GET /api/reports/download/<filename>`, `DELETE /api/reports/<filename>`

**Key environment variables**: `BENCH_USER`/`BENCH_PASS` (default: admin/admin), `JWT_SECRET_KEY` (default: change-me-in-production).

## Conventions

- Commit messages use conventional style: `feat:`, `add:`, `update:`, etc.
- The pre-built `frontend/dist/` is committed — rebuild and commit after frontend changes.
- `webui.py` is a deprecated Streamlit UI, kept for reference but not the primary interface.
- Reports are saved to `reports/` as JSON + XLSX; the directory is mounted as a Docker volume.

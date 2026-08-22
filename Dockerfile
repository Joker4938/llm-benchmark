# syntax=docker/dockerfile:1

# 前端依赖仅在制品构建阶段访问包仓库；部署后的运行容器不访问互联网。
FROM node:20-alpine AS frontend-builder

WORKDIR /build/frontend

RUN corepack enable \
    && corepack prepare pnpm@10.30.2 --activate

COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm fetch --frozen-lockfile

COPY frontend/ ./
RUN pnpm install --offline --frozen-lockfile \
    && pnpm run build


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    LLM_BENCHMARK_DATA_DIR=/app/data \
    LLM_BENCHMARK_FRONTEND_DIR=/app/frontend/dist \
    LLM_BENCHMARK_HOST=0.0.0.0 \
    LLM_BENCHMARK_PORT=8080

WORKDIR /app

# 使用精确锁定的生产依赖，避免构建时重新解析版本。
COPY requirements.lock ./
RUN python -m pip install \
    --disable-pip-version-check \
    --no-cache-dir \
    --requirement requirements.lock

COPY benchmark_core/ ./benchmark_core/
COPY benchmark_cli/ ./benchmark_cli/
COPY benchmark_server/ ./benchmark_server/
COPY assets/ ./assets/
COPY llm_benchmark.py run_benchmarks.py ./
COPY --from=frontend-builder /build/frontend/dist ./frontend/dist

# 使用固定的非特权 UID/GID；命名卷首次创建时会继承数据目录权限。
RUN mkdir -p /app/data \
    && chown -R 10001:10001 /app

USER 10001:10001

EXPOSE 8080
VOLUME ["/app/data"]

HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=6 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('LLM_BENCHMARK_PORT', '8080') + '/health/ready', timeout=3).read()"]

CMD ["python", "-m", "benchmark_server.container_main"]

#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"
ENV_FILE="$SCRIPT_DIR/.env"
STOP_TIMEOUT=${LLM_BENCHMARK_STOP_TIMEOUT:-30}

fail() {
  printf '[停止失败] %s\n' "$*" >&2
  exit 1
}

command -v docker >/dev/null 2>&1 || fail '缺少命令：docker'
[ -f "$COMPOSE_FILE" ] || fail '缺少 docker-compose.yml'
[ -f "$ENV_FILE" ] || fail '尚未安装：缺少 .env'
docker info >/dev/null 2>&1 || fail 'Docker 服务不可用或当前用户无访问权限'
docker compose version >/dev/null 2>&1 || fail 'Docker Compose 插件不可用'

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" stop --timeout "$STOP_TIMEOUT"
printf '[停止] 服务已停止，容器和数据卷均已保留。\n'
printf '[停止] 重新启动请执行：%s/run.sh\n' "$SCRIPT_DIR"

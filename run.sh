#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"
ENV_FILE="$SCRIPT_DIR/.env"
HEALTH_TIMEOUT=${LLM_BENCHMARK_HEALTH_TIMEOUT:-120}

log() {
  printf '[运行] %s\n' "$*"
}

fail() {
  printf '[运行失败] %s\n' "$*" >&2
  exit 1
}

env_value() {
  awk -v key="$2" '
    index($0, key "=") == 1 {
      print substr($0, length(key) + 2)
      exit
    }
  ' "$1"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "缺少命令：$1"
}

require_command docker
require_command awk
[ -f "$COMPOSE_FILE" ] || fail '缺少 docker-compose.yml'
[ -f "$ENV_FILE" ] || fail '尚未安装：缺少 .env，请先执行 install.sh'
docker info >/dev/null 2>&1 || fail 'Docker 服务不可用或当前用户无访问权限'
docker compose version >/dev/null 2>&1 || fail 'Docker Compose 插件不可用'

for key in LLM_BENCHMARK_USER LLM_BENCHMARK_PASSWORD LLM_BENCHMARK_SESSION_SECRET; do
  value=$(env_value "$ENV_FILE" "$key")
  [ -n "$value" ] || fail ".env 中的 $key 不能为空"
done
session_secret=$(env_value "$ENV_FILE" LLM_BENCHMARK_SESSION_SECRET)
[ ${#session_secret} -ge 16 ] || fail 'LLM_BENCHMARK_SESSION_SECRET 至少需要 16 个字符'

image=$(env_value "$ENV_FILE" LLM_BENCHMARK_IMAGE)
[ -n "$image" ] || image=llm-benchmark:latest
docker image inspect "$image" >/dev/null 2>&1 || fail "本地不存在镜像：$image；请先执行 install.sh"

container_name=$(env_value "$ENV_FILE" LLM_BENCHMARK_CONTAINER_NAME)
[ -n "$container_name" ] || container_name=llm-benchmark
log "启动本地镜像：$image"
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up --detach --no-build

start_time=$(date +%s)
while :; do
  health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_name" 2>/dev/null || true)
  case "$health" in
    healthy)
      break
      ;;
    unhealthy|exited|dead)
      docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" logs --tail 80 >&2 || true
      fail "容器状态异常：$health"
      ;;
  esac

  now=$(date +%s)
  if [ $((now - start_time)) -ge "$HEALTH_TIMEOUT" ]; then
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" logs --tail 80 >&2 || true
    fail "等待健康检查超时（${HEALTH_TIMEOUT}s）"
  fi
  sleep 2
done

bind_address=$(env_value "$ENV_FILE" LLM_BENCHMARK_BIND_ADDRESS)
[ -n "$bind_address" ] || bind_address=0.0.0.0
web_port=$(env_value "$ENV_FILE" LLM_BENCHMARK_WEB_PORT)
[ -n "$web_port" ] || web_port=8080
case "$bind_address" in
  0.0.0.0|::) access_host='<服务器IP>' ;;
  *) access_host=$bind_address ;;
esac

log '服务已通过健康检查'
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps
printf '\n访问地址：http://%s:%s\n' "$access_host" "$web_port"
printf '查看日志：docker compose --env-file %s -f %s logs -f\n' "$ENV_FILE" "$COMPOSE_FILE"

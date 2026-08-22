#!/bin/sh
set -eu
umask 077

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"
ENV_FILE="$SCRIPT_DIR/.env"
BACKUPS_DIR="$SCRIPT_DIR/backups"
CHECKSUM_FILE=${LLM_BENCHMARK_CHECKSUM_FILE:-"$SCRIPT_DIR/checksums.sha256"}
HEALTH_TIMEOUT=${LLM_BENCHMARK_HEALTH_TIMEOUT:-120}
STOP_TIMEOUT=${LLM_BENCHMARK_STOP_TIMEOUT:-30}

ROLLBACK_ARMED=false
ROLLBACK_RUNNING=false
BACKUP_READY=false
BACKUP_DIR=''
BACKUP_ARCHIVE='data.tar.gz'
OLD_IMAGE=''
OLD_IMAGE_ID=''
ROLLBACK_IMAGE=''
CONTAINER_NAME='llm-benchmark'
VOLUME_NAME='llm-benchmark-data'

log() {
  printf '[更新] %s\n' "$*"
}

fail() {
  printf '[更新失败] %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "缺少命令：$1"
}

env_value() {
  awk -v key="$2" '
    index($0, key "=") == 1 {
      print substr($0, length(key) + 2)
      exit
    }
  ' "$1"
}

set_env_value() {
  file=$1
  key=$2
  value=$3
  temporary="${file}.tmp.$$"
  awk -v key="$key" -v value="$value" '
    BEGIN { replaced = 0 }
    index($0, key "=") == 1 {
      print key "=" value
      replaced = 1
      next
    }
    { print }
    END {
      if (!replaced) {
        print key "=" value
      }
    }
  ' "$file" > "$temporary"
  mv "$temporary" "$file"
  chmod 600 "$file"
}

find_image_archive() {
  if [ -n "${LLM_BENCHMARK_IMAGE_ARCHIVE:-}" ]; then
    case "$LLM_BENCHMARK_IMAGE_ARCHIVE" in
      /*) archive=$LLM_BENCHMARK_IMAGE_ARCHIVE ;;
      *) archive="$SCRIPT_DIR/$LLM_BENCHMARK_IMAGE_ARCHIVE" ;;
    esac
    [ -f "$archive" ] || fail "找不到新版镜像归档：$archive"
    printf '%s\n' "$archive"
    return
  fi

  found=''
  for candidate in \
    "$SCRIPT_DIR"/images/llm-benchmark-*.tar \
    "$SCRIPT_DIR"/llm-benchmark-*.tar \
    "$SCRIPT_DIR"/llm-benchmark.tar
  do
    [ -f "$candidate" ] || continue
    if [ -n "$found" ] && [ "$found" != "$candidate" ]; then
      fail "发现多个镜像归档，请通过 LLM_BENCHMARK_IMAGE_ARCHIVE 指定新版归档"
    fi
    found=$candidate
  done
  [ -n "$found" ] || fail '未找到新版离线镜像归档（images/llm-benchmark-*.tar）'
  printf '%s\n' "$found"
}

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    fail '缺少 SHA-256 校验工具（sha256sum 或 shasum）'
  fi
}

verify_archive() {
  archive=$1
  checksum_file=$CHECKSUM_FILE
  if [ ! -f "$checksum_file" ] && [ -f "${archive}.sha256" ]; then
    checksum_file="${archive}.sha256"
  fi
  [ -f "$checksum_file" ] || fail "缺少校验文件：$CHECKSUM_FILE 或 ${archive}.sha256"

  archive_name=$(basename -- "$archive")
  relative_path=${archive#"$SCRIPT_DIR"/}
  expected=$(awk -v relative="$relative_path" -v name="$archive_name" '
    {
      file = $2
      sub(/^\*/, "", file)
      sub(/^\.\//, "", file)
      if (file == relative || file == name || file == "images/" name) {
        print tolower($1)
        exit
      }
    }
  ' "$checksum_file")
  [ -n "$expected" ] || fail "校验文件未包含新版镜像归档：$relative_path"

  actual=$(sha256_of "$archive")
  [ "$actual" = "$expected" ] || fail '新版镜像归档 SHA-256 不匹配'
  log "新版镜像归档校验通过：$archive_name"
}

wait_for_health() {
  container_name=$1
  timeout=$2
  started_at=$(date +%s)
  while :; do
    health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_name" 2>/dev/null || true)
    case "$health" in
      healthy)
        return 0
        ;;
      unhealthy|exited|dead)
        return 1
        ;;
    esac
    now=$(date +%s)
    if [ $((now - started_at)) -ge "$timeout" ]; then
      return 1
    fi
    sleep 2
  done
}

backup_data_volume() {
  docker run --rm \
    --user 0:0 \
    --network none \
    --entrypoint python \
    --env "BACKUP_FILE=$BACKUP_ARCHIVE" \
    --volume "$VOLUME_NAME:/data:ro" \
    --volume "$BACKUP_DIR:/backup" \
    "$ROLLBACK_IMAGE" \
    -c 'import os, tarfile
with tarfile.open("/backup/" + os.environ["BACKUP_FILE"], "w:gz") as archive:
    archive.add("/data", arcname=".")'
  [ -s "$BACKUP_DIR/$BACKUP_ARCHIVE" ] || return 1
  sha256_of "$BACKUP_DIR/$BACKUP_ARCHIVE" > "$BACKUP_DIR/$BACKUP_ARCHIVE.sha256"
}

restore_data_volume() {
  docker run --rm \
    --user 0:0 \
    --network none \
    --entrypoint python \
    --env "BACKUP_FILE=$BACKUP_ARCHIVE" \
    --volume "$VOLUME_NAME:/data" \
    --volume "$BACKUP_DIR:/backup:ro" \
    "$ROLLBACK_IMAGE" \
    -c 'import os, pathlib, shutil, tarfile
root = pathlib.Path("/data")
for child in root.iterdir():
    if child.is_dir() and not child.is_symlink():
        shutil.rmtree(child)
    else:
        child.unlink()
with tarfile.open("/backup/" + os.environ["BACKUP_FILE"], "r:gz") as archive:
    archive.extractall(root, filter="data")'
}

run_migration() {
  image=$1
  docker run --rm \
    --user 10001:10001 \
    --network none \
    --entrypoint python \
    --volume "$VOLUME_NAME:/app/data" \
    "$image" \
    -c 'from benchmark_server.database import Database
Database("/app/data").initialize(resilient=False)'
}

capture_logs() {
  target=$1
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" logs --no-color --tail 200 > "$target" 2>&1 || true
}

rollback_update() {
  ROLLBACK_RUNNING=true
  rollback_ok=true
  printf '\n[回滚] 新版本更新失败，开始恢复旧版本。\n' >&2
  capture_logs "$BACKUP_DIR/new-version.log"

  if ! docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" down --remove-orphans; then
    printf '[回滚] 停止新版容器失败。\n' >&2
    rollback_ok=false
  fi

  if [ "$BACKUP_READY" = true ]; then
    if ! restore_data_volume; then
      printf '[回滚] 恢复数据卷失败。\n' >&2
      rollback_ok=false
    fi
  fi

  if ! cp "$BACKUP_DIR/.env" "$ENV_FILE"; then
    printf '[回滚] 恢复 .env 失败。\n' >&2
    rollback_ok=false
  else
    chmod 600 "$ENV_FILE"
  fi
  if ! cp "$BACKUP_DIR/docker-compose.yml" "$COMPOSE_FILE"; then
    printf '[回滚] 恢复 docker-compose.yml 失败。\n' >&2
    rollback_ok=false
  fi

  if ! docker image tag "$OLD_IMAGE_ID" "$OLD_IMAGE"; then
    printf '[回滚] 原镜像标签已变化，改用保留标签：%s\n' "$ROLLBACK_IMAGE" >&2
    set_env_value "$ENV_FILE" LLM_BENCHMARK_IMAGE "$ROLLBACK_IMAGE"
  fi

  if ! docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up --detach --no-build; then
    printf '[回滚] 旧版本容器启动失败。\n' >&2
    rollback_ok=false
  elif ! wait_for_health "$CONTAINER_NAME" "$HEALTH_TIMEOUT"; then
    printf '[回滚] 旧版本健康检查失败。\n' >&2
    capture_logs "$BACKUP_DIR/rollback-version.log"
    rollback_ok=false
  fi

  if [ "$rollback_ok" = true ]; then
    printf '[回滚] 已恢复旧数据、旧配置和旧镜像，旧版本健康检查通过。\n' >&2
  else
    printf '[回滚] 自动回滚未完全成功，请查看：%s\n' "$BACKUP_DIR" >&2
    return 1
  fi
}

handle_exit() {
  status=$1
  trap - 0 1 2 15
  if [ "$status" -ne 0 ] && [ "$ROLLBACK_ARMED" = true ] && [ "$ROLLBACK_RUNNING" = false ]; then
    rollback_update || true
    printf '[更新失败] 诊断和备份目录：%s\n' "$BACKUP_DIR" >&2
  fi
  exit "$status"
}

trap 'handle_exit $?' 0
trap 'exit 130' 1 2 15

require_command docker
require_command awk
require_command date
case "$HEALTH_TIMEOUT" in ''|*[!0-9]*) fail 'LLM_BENCHMARK_HEALTH_TIMEOUT 必须是正整数' ;; esac
case "$STOP_TIMEOUT" in ''|*[!0-9]*) fail 'LLM_BENCHMARK_STOP_TIMEOUT 必须是正整数' ;; esac
[ "$HEALTH_TIMEOUT" -gt 0 ] || fail 'LLM_BENCHMARK_HEALTH_TIMEOUT 必须大于 0'
[ "$STOP_TIMEOUT" -gt 0 ] || fail 'LLM_BENCHMARK_STOP_TIMEOUT 必须大于 0'
[ -f "$COMPOSE_FILE" ] || fail '缺少 docker-compose.yml'
[ -f "$ENV_FILE" ] || fail '尚未安装：缺少 .env，请先执行 install.sh'
docker info >/dev/null 2>&1 || fail 'Docker 服务不可用或当前用户无访问权限'
docker compose version >/dev/null 2>&1 || fail 'Docker Compose 插件不可用'

OLD_IMAGE=$(env_value "$ENV_FILE" LLM_BENCHMARK_IMAGE)
[ -n "$OLD_IMAGE" ] || OLD_IMAGE=llm-benchmark:latest
CONTAINER_NAME=$(env_value "$ENV_FILE" LLM_BENCHMARK_CONTAINER_NAME)
[ -n "$CONTAINER_NAME" ] || CONTAINER_NAME=llm-benchmark
VOLUME_NAME=$(env_value "$ENV_FILE" LLM_BENCHMARK_DATA_VOLUME)
[ -n "$VOLUME_NAME" ] || VOLUME_NAME=llm-benchmark-data
docker volume inspect "$VOLUME_NAME" >/dev/null 2>&1 || fail "数据卷不存在：$VOLUME_NAME"
OLD_IMAGE_ID=$(docker image inspect --format '{{.Id}}' "$OLD_IMAGE" 2>/dev/null) || fail "旧镜像不存在：$OLD_IMAGE"

archive=$(find_image_archive)
verify_archive "$archive"
stamp=$(date -u '+%Y%m%dT%H%M%SZ')
BACKUP_DIR="$BACKUPS_DIR/update-$stamp"
ROLLBACK_IMAGE="llm-benchmark:rollback-$stamp"
mkdir -p "$BACKUP_DIR"
cp "$ENV_FILE" "$BACKUP_DIR/.env"
cp "$COMPOSE_FILE" "$BACKUP_DIR/docker-compose.yml"
printf '%s\n' "$OLD_IMAGE" > "$BACKUP_DIR/old-image.txt"
printf '%s\n' "$OLD_IMAGE_ID" > "$BACKUP_DIR/old-image-id.txt"
printf '%s\n' "$ROLLBACK_IMAGE" > "$BACKUP_DIR/rollback-image.txt"
docker image tag "$OLD_IMAGE_ID" "$ROLLBACK_IMAGE"

ROLLBACK_ARMED=true
log '停止旧版本并创建一致性数据备份'
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" stop --timeout "$STOP_TIMEOUT"
backup_data_volume || fail '备份数据卷失败'
BACKUP_READY=true
log "数据与配置已备份到：$BACKUP_DIR"

log "加载新版镜像：$(basename -- "$archive")"
if ! load_output=$(docker load --input "$archive" 2>&1); then
  printf '%s\n' "$load_output" >&2
  fail '加载新版镜像失败'
fi
printf '%s\n' "$load_output"
loaded_image=$(printf '%s\n' "$load_output" | awk '
  index($0, "Loaded image: ") == 1 {
    image = substr($0, length("Loaded image: ") + 1)
  }
  END { print image }
')
new_image=${LLM_BENCHMARK_NEW_IMAGE:-$loaded_image}
[ -n "$new_image" ] || fail '无法确定新版镜像标签，请设置 LLM_BENCHMARK_NEW_IMAGE'
new_image_id=$(docker image inspect --format '{{.Id}}' "$new_image" 2>/dev/null) || fail "新版镜像不存在：$new_image"
[ "$new_image_id" != "$OLD_IMAGE_ID" ] || fail '新版镜像与当前镜像内容相同，已取消更新'
set_env_value "$ENV_FILE" LLM_BENCHMARK_IMAGE "$new_image"

log '在无网络环境中执行数据库迁移'
run_migration "$new_image" || fail '数据库迁移失败'

log "启动新版镜像：$new_image"
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up --detach --no-build
if ! wait_for_health "$CONTAINER_NAME" "$HEALTH_TIMEOUT"; then
  fail "新版健康检查失败或超时（${HEALTH_TIMEOUT}s）"
fi
capture_logs "$BACKUP_DIR/new-version.log"
ROLLBACK_ARMED=false
printf '%s\n' "$new_image" > "$BACKUP_DIR/new-image.txt"
printf '%s\n' "$new_image_id" > "$BACKUP_DIR/new-image-id.txt"
log '更新完成，新版本健康检查通过'
log "备份目录：$BACKUP_DIR"
log "保留的旧镜像标签：$ROLLBACK_IMAGE"

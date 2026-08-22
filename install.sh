#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"
ENV_EXAMPLE="$SCRIPT_DIR/.env.example"
ENV_FILE="$SCRIPT_DIR/.env"
CHECKSUM_FILE=${LLM_BENCHMARK_CHECKSUM_FILE:-"$SCRIPT_DIR/checksums.sha256"}

log() {
  printf '[安装] %s\n' "$*"
}

fail() {
  printf '[安装失败] %s\n' "$*" >&2
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
}

generate_hex_secret() {
  byte_count=$1
  od -An -N "$byte_count" -tx1 /dev/urandom | tr -d ' \n'
}

find_image_archive() {
  if [ -n "${LLM_BENCHMARK_IMAGE_ARCHIVE:-}" ]; then
    case "$LLM_BENCHMARK_IMAGE_ARCHIVE" in
      /*) archive=$LLM_BENCHMARK_IMAGE_ARCHIVE ;;
      *) archive="$SCRIPT_DIR/$LLM_BENCHMARK_IMAGE_ARCHIVE" ;;
    esac
    [ -f "$archive" ] || fail "找不到镜像归档：$archive"
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
      fail "发现多个镜像归档，请通过 LLM_BENCHMARK_IMAGE_ARCHIVE 指定"
    fi
    found=$candidate
  done
  [ -n "$found" ] || fail "未找到离线镜像归档（images/llm-benchmark-*.tar）"
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
      if (file == relative || file == name || file == "images/" name) {
        print tolower($1)
        exit
      }
    }
  ' "$checksum_file")
  [ -n "$expected" ] || fail "校验文件未包含镜像归档：$relative_path"

  actual=$(sha256_of "$archive")
  [ "$actual" = "$expected" ] || fail "镜像归档 SHA-256 不匹配"
  log "镜像归档校验通过：$archive_name"
}

require_command docker
require_command awk
require_command od
require_command tr
[ -f "$COMPOSE_FILE" ] || fail "缺少 docker-compose.yml"
[ -f "$ENV_EXAMPLE" ] || fail "缺少 .env.example"
docker info >/dev/null 2>&1 || fail 'Docker 服务不可用或当前用户无访问权限'
docker compose version >/dev/null 2>&1 || fail 'Docker Compose 插件不可用'

archive=$(find_image_archive)
verify_archive "$archive"
log "加载离线镜像：$(basename -- "$archive")"
if ! load_output=$(docker load --input "$archive" 2>&1); then
  printf '%s\n' "$load_output" >&2
  fail '加载离线镜像失败'
fi
printf '%s\n' "$load_output"
loaded_image=$(printf '%s\n' "$load_output" | awk '
  index($0, "Loaded image: ") == 1 {
    image = substr($0, length("Loaded image: ") + 1)
  }
  END { print image }
')
configured_image=${LLM_BENCHMARK_IMAGE:-$loaded_image}
[ -n "$configured_image" ] || fail '无法确定镜像标签，请设置 LLM_BENCHMARK_IMAGE'
docker image inspect "$configured_image" >/dev/null 2>&1 || fail "本地不存在镜像：$configured_image"

created_env=false
if [ ! -f "$ENV_FILE" ]; then
  cp "$ENV_EXAMPLE" "$ENV_FILE"
  created_env=true
fi
chmod 600 "$ENV_FILE"
set_env_value "$ENV_FILE" LLM_BENCHMARK_IMAGE "$configured_image"

username=$(env_value "$ENV_FILE" LLM_BENCHMARK_USER)
if [ -z "$username" ]; then
  username=admin
  set_env_value "$ENV_FILE" LLM_BENCHMARK_USER "$username"
fi
password=$(env_value "$ENV_FILE" LLM_BENCHMARK_PASSWORD)
generated_password=false
if [ -z "$password" ]; then
  password=$(generate_hex_secret 12)
  set_env_value "$ENV_FILE" LLM_BENCHMARK_PASSWORD "$password"
  generated_password=true
fi
session_secret=$(env_value "$ENV_FILE" LLM_BENCHMARK_SESSION_SECRET)
if [ ${#session_secret} -lt 16 ]; then
  session_secret=$(generate_hex_secret 32)
  set_env_value "$ENV_FILE" LLM_BENCHMARK_SESSION_SECRET "$session_secret"
fi

volume_name=$(env_value "$ENV_FILE" LLM_BENCHMARK_DATA_VOLUME)
[ -n "$volume_name" ] || volume_name=llm-benchmark-data
docker volume create "$volume_name" >/dev/null
mkdir -p "$SCRIPT_DIR/backups"
chmod 700 "$SCRIPT_DIR/backups"

log "安装准备完成；数据卷：$volume_name"
if [ "$created_env" = true ]; then
  log "已生成仅本机使用的 .env（权限 600）"
fi
if [ "$generated_password" = true ]; then
  printf '\n初始登录用户名：%s\n初始登录密码：%s\n' "$username" "$password"
  printf '请妥善保存，并按需修改 .env 中的登录凭据。\n'
fi
printf '\n安装脚本不会自动启动服务。请执行：%s/run.sh\n' "$SCRIPT_DIR"

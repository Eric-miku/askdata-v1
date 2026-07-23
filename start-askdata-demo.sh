#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${SCRIPT_DIR}/pyproject.toml" && -d "${SCRIPT_DIR}/frontend" ]]; then
  ROOT="$SCRIPT_DIR"
elif [[ -f "${SCRIPT_DIR}/askdata-v1/pyproject.toml" && -d "${SCRIPT_DIR}/askdata-v1/frontend" ]]; then
  ROOT="${SCRIPT_DIR}/askdata-v1"
else
  printf '\033[1;31m[AskData]\033[0m 找不到项目目录。请把脚本放在 askdata-v1 项目根目录，或放在包含 askdata-v1 的目录下。\n' >&2
  exit 1
fi
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
FRONTEND_URL="http://localhost:${FRONTEND_PORT}"
LOG_DIR="${ROOT}/.logs"

mkdir -p "$LOG_DIR"

backend_pid=""
frontend_pid=""
started_backend=0
started_frontend=0

info() {
  printf '\033[1;34m[AskData]\033[0m %s\n' "$*"
}

warn() {
  printf '\033[1;33m[AskData]\033[0m %s\n' "$*"
}

fail() {
  printf '\033[1;31m[AskData]\033[0m %s\n' "$*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "缺少命令：$1。请先安装后再重试。"
}

port_open() {
  local host="$1"
  local port="$2"
  python3 - "$host" "$port" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
sock = socket.socket()
sock.settimeout(0.3)
try:
    sock.connect((host, port))
except OSError:
    sys.exit(1)
else:
    sys.exit(0)
finally:
    sock.close()
PY
}

wait_for_port() {
  local name="$1"
  local host="$2"
  local port="$3"
  local timeout="${4:-60}"
  local elapsed=0

  while (( elapsed < timeout )); do
    if port_open "$host" "$port"; then
      info "$name 已启动：$host:$port"
      return 0
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done

  warn "$name 在 ${timeout}s 内未响应，请查看日志。"
  return 1
}

open_browser() {
  local url="$1"
  if command -v cmd.exe >/dev/null 2>&1; then
    cmd.exe /C start "" "$url" >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$url" >/dev/null 2>&1 || true
  elif command -v open >/dev/null 2>&1; then
    open "$url" >/dev/null 2>&1 || true
  fi
}

cleanup() {
  info "正在停止本脚本启动的服务..."
  if [[ "$started_frontend" == "1" && -n "$frontend_pid" ]] && kill -0 "$frontend_pid" >/dev/null 2>&1; then
    kill "$frontend_pid" >/dev/null 2>&1 || true
  fi
  if [[ "$started_backend" == "1" && -n "$backend_pid" ]] && kill -0 "$backend_pid" >/dev/null 2>&1; then
    kill "$backend_pid" >/dev/null 2>&1 || true
  fi
}

trap cleanup INT TERM EXIT

cd "$ROOT"

need_cmd python3
need_cmd uv
need_cmd npm

info "项目目录：$ROOT"
info "日志目录：$LOG_DIR"

if [[ ! -f ".env" ]]; then
  warn "未发现 .env；如果模型或 MySQL 连接失败，请先复制 .env.example 并配置。"
fi

if [[ ! -d ".venv" ]]; then
  info "首次运行：同步后端依赖..."
  bash scripts/setup-dev-env.sh
fi

if [[ ! -d "frontend/node_modules" ]]; then
  info "首次运行：安装前端依赖..."
  (cd frontend && npm install)
fi

if port_open "$BACKEND_HOST" "$BACKEND_PORT"; then
  warn "后端端口 ${BACKEND_PORT} 已被占用，将复用已有后端服务。"
else
  info "启动后端：http://${BACKEND_HOST}:${BACKEND_PORT}"
  UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}" uv run askdata serve --host "$BACKEND_HOST" --port "$BACKEND_PORT" \
    >"${LOG_DIR}/demo-backend.log" 2>&1 &
  backend_pid="$!"
  started_backend=1
  wait_for_port "后端" "$BACKEND_HOST" "$BACKEND_PORT" 90 || {
    tail -n 80 "${LOG_DIR}/demo-backend.log" || true
    fail "后端启动失败。"
  }
fi

if port_open "127.0.0.1" "$FRONTEND_PORT"; then
  warn "前端端口 ${FRONTEND_PORT} 已被占用，将复用已有前端服务。"
else
  info "启动前端：$FRONTEND_URL"
  (cd frontend && npm run dev -- --host 0.0.0.0 --port "$FRONTEND_PORT") \
    >"${LOG_DIR}/demo-frontend.log" 2>&1 &
  frontend_pid="$!"
  started_frontend=1
  wait_for_port "前端" "127.0.0.1" "$FRONTEND_PORT" 90 || {
    tail -n 80 "${LOG_DIR}/demo-frontend.log" || true
    fail "前端启动失败。"
  }
fi

info "Demo 已就绪：$FRONTEND_URL"
info "后端 API 文档：http://${BACKEND_HOST}:${BACKEND_PORT}/docs"
info "按 Ctrl+C 可停止由本脚本启动的服务。"
open_browser "$FRONTEND_URL"

while true; do
  sleep 3600
done

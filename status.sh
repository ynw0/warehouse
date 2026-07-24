#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PORT="${PORT:-5000}"
DATA_DIR="${DATA_DIR:-$(pwd)/data}"
PID_FILE="${PID_FILE:-$DATA_DIR/warehouse.pid}"

find_running_pid() {
  local gunicorn_pids pid_cwd
  gunicorn_pids=$(pgrep -f "gunicorn.*(warehouse_suit\\.wsgi:app|app:app)" 2>/dev/null || true)
  for pid in $gunicorn_pids; do
    pid_cwd=$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)
    if [ "$pid_cwd" = "$(pwd)" ]; then
      echo "$pid"
      return 0
    fi
  done
  return 1
}

if [ ! -f "$PID_FILE" ]; then
  pid="$(find_running_pid || true)"
  if [ -z "$pid" ]; then
    echo "未运行：没有 PID 文件。"
    exit 1
  fi
  mkdir -p "$(dirname "$PID_FILE")"
  echo "$pid" > "$PID_FILE"
  echo "运行中，PID: $pid（已修复缺失的 PID 文件）"
else
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
    pid="$(find_running_pid || true)"
    if [ -z "$pid" ]; then
      echo "未运行：PID 文件存在，但进程不存在。"
      exit 1
    fi
    mkdir -p "$(dirname "$PID_FILE")"
    echo "$pid" > "$PID_FILE"
    echo "运行中，PID: $pid（已修复过期的 PID 文件）"
  else
    echo "运行中，PID: $pid"
  fi
fi
if command -v curl >/dev/null 2>&1; then
  if curl -fsS "http://127.0.0.1:$PORT/api/session" >/dev/null; then
    echo "本机接口检查通过：http://127.0.0.1:$PORT"
  else
    echo "进程存在，但接口暂时未响应：http://127.0.0.1:$PORT"
  fi
fi

#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

DATA_DIR="${DATA_DIR:-$(pwd)/data}"
PID_FILE="${PID_FILE:-$DATA_DIR/warehouse.pid}"

log() { printf '[stop] %s\n' "$*"; }

kill_children() {
  local ppid="$1"
  local children
  children=$(pgrep -P "$ppid" 2>/dev/null || true)
  for child in $children; do
    kill "$child" 2>/dev/null || true
  done
}

# 1) graceful shutdown via PID file
if [ -f "$PID_FILE" ]; then
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    log "sending SIGTERM to master PID $pid"
    kill "$pid"
    for _ in $(seq 1 25); do
      if ! kill -0 "$pid" 2>/dev/null; then
        log "master process exited"
        break
      fi
      sleep 0.2
    done
    if kill -0 "$pid" 2>/dev/null; then
      log "master didn't exit, force kill"
      kill -9 "$pid" 2>/dev/null || true
      sleep 0.5
    fi
  fi
  rm -f "$PID_FILE"
fi

# 2) kill any remaining gunicorn processes for this project
PROJ_DIR="$(pwd)"
gunicorn_pids=$(pgrep -f "gunicorn.*(warehouse_suit\\.wsgi:app|app:app)" 2>/dev/null || true)
project_pids=""
for gp in $gunicorn_pids; do
  gp_cwd=$(pwdx "$gp" 2>/dev/null | awk '{print $2}' || true)
  if [ "$gp_cwd" = "$PROJ_DIR" ] || [ -z "$gp_cwd" ]; then
    project_pids="$project_pids $gp"
  fi
done

if [ -n "$project_pids" ]; then
  log "found remaining gunicorn processes, sending SIGTERM"
  for rp in $project_pids; do
    kill "$rp" 2>/dev/null || true
  done
  sleep 1
  for rp in $project_pids; do
    if kill -0 "$rp" 2>/dev/null; then
      kill -9 "$rp" 2>/dev/null || true
    fi
  done
  log "remaining processes cleaned up"
fi

log "warehouse suite stopped"

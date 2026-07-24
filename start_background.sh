#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-5000}"
VENV_DIR="${VENV_DIR:-.venv}"
DATA_DIR="${DATA_DIR:-$(pwd)/data}"
PID_FILE="${PID_FILE:-$DATA_DIR/warehouse.pid}"
LOG_FILE="${LOG_FILE:-$DATA_DIR/warehouse.log}"
WORKERS="${WORKERS:-2}"
THREADS="${THREADS:-32}"
TIMEOUT="${TIMEOUT:-120}"

mkdir -p "$DATA_DIR"
if [ ! -f "$DATA_DIR/warehouse.db" ]; then
  if [ -f "$(pwd)/warehouse.db" ]; then
    mv "$(pwd)/warehouse.db" "$DATA_DIR/warehouse.db"
  elif [ -f "$(pwd)/warehouse_cards.db" ]; then
    mv "$(pwd)/warehouse_cards.db" "$DATA_DIR/warehouse.db"
  fi
fi

export HOST PORT
export ENABLE_AI="${ENABLE_AI:-1}"
export MATERIAL_SYSTEM="${MATERIAL_SYSTEM:-1}"
export WAREHOUSE_DATA_DIR="${WAREHOUSE_DATA_DIR:-$DATA_DIR}"
export WAREHOUSE_DB="${WAREHOUSE_DB:-$DATA_DIR/warehouse.db}"
export LOG_FILE

if [ ! -x "$VENV_DIR/bin/python" ]; then
  echo "Virtual environment not found. Run ./install_offline.sh first." >&2
  exit 1
fi

if [ -f "$PID_FILE" ]; then
  old_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
    echo "Warehouse suite is already running, PID: $old_pid"
    echo "URL: http://$HOST:$PORT"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

if "$VENV_DIR/bin/python" -m gunicorn --version >/dev/null 2>&1; then
  cmd=(
    "$VENV_DIR/bin/python"
    -m
    gunicorn
    --workers "$WORKERS"
    --threads "$THREADS"
    --timeout "$TIMEOUT"
    --bind "$HOST:$PORT"
    --access-logfile -
    --error-logfile -
    warehouse_suit.wsgi:app
  )
else
  if [ -x "$VENV_DIR/bin/warehouse_suit" ]; then
    cmd=("$VENV_DIR/bin/warehouse_suit" --host "$HOST" --port "$PORT")
  else
    cmd=("$VENV_DIR/bin/python" -m warehouse_suit --host "$HOST" --port "$PORT")
  fi
fi

nohup "${cmd[@]}" >> "$LOG_FILE" 2>&1 &
pid="$!"
mkdir -p "$(dirname "$PID_FILE")"
echo "$pid" > "$PID_FILE"

sleep 1
if ! kill -0 "$pid" 2>/dev/null; then
  rm -f "$PID_FILE"
  echo "Background start failed. See log: $LOG_FILE" >&2
  tail -n 40 "$LOG_FILE" >&2 || true
  exit 1
fi

if command -v curl >/dev/null 2>&1; then
  for _ in $(seq 1 30); do
    if curl -fsS "http://127.0.0.1:$PORT/api/session" >/dev/null; then
      break
    fi
    sleep 0.25
  done
fi

echo "Warehouse suite started, PID: $pid"
echo "URL: http://$HOST:$PORT"
echo "Database: $WAREHOUSE_DB"
echo "Log: $LOG_FILE"

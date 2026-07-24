#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-5000}"
VENV_DIR="${VENV_DIR:-.venv}"
DATA_DIR="${DATA_DIR:-$(pwd)/data}"
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
export LOG_FILE="${LOG_FILE:-$DATA_DIR/warehouse.log}"

if [ ! -x "$VENV_DIR/bin/python" ]; then
  echo "Virtual environment not found. Run ./install_offline.sh first." >&2
  exit 1
fi

if "$VENV_DIR/bin/python" -m gunicorn --version >/dev/null 2>&1; then
  exec "$VENV_DIR/bin/python" -m gunicorn \
    --workers "$WORKERS" \
    --threads "$THREADS" \
    --timeout "$TIMEOUT" \
    --bind "$HOST:$PORT" \
    --access-logfile - \
    --error-logfile - \
    warehouse_suit.wsgi:app
fi

if [ -x "$VENV_DIR/bin/warehouse_suit" ]; then
  exec "$VENV_DIR/bin/warehouse_suit" --host "$HOST" --port "$PORT"
fi

exec "$VENV_DIR/bin/python" -m warehouse_suit --host "$HOST" --port "$PORT"

#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

SERVICE_NAME="${SERVICE_NAME:-warehouse-suite}"
INSTALL_DIR="${INSTALL_DIR:-$(pwd)}"
SERVICE_USER="${SERVICE_USER:-$(id -un)}"
SERVICE_GROUP="${SERVICE_GROUP:-$(id -gn)}"
PORT="${PORT:-5000}"
HOST="${HOST:-0.0.0.0}"
WORKERS="${WORKERS:-2}"
THREADS="${THREADS:-32}"

unit_file="/etc/systemd/system/${SERVICE_NAME}.service"
tmp_unit="$(mktemp)"

sed \
  -e "s#WorkingDirectory=/opt/warehouse-suite-offline#WorkingDirectory=${INSTALL_DIR}#g" \
  -e "s#WAREHOUSE_DATA_DIR=/opt/warehouse-suite-offline/data#WAREHOUSE_DATA_DIR=${INSTALL_DIR}/data#g" \
  -e "s#WAREHOUSE_DB=/opt/warehouse-suite-offline/data/warehouse.db#WAREHOUSE_DB=${INSTALL_DIR}/data/warehouse.db#g" \
  -e "s#LOG_FILE=/opt/warehouse-suite-offline/data/warehouse.log#LOG_FILE=${INSTALL_DIR}/data/warehouse.log#g" \
  -e "s#/opt/warehouse-suite-offline/.venv/bin/gunicorn#${INSTALL_DIR}/.venv/bin/gunicorn#g" \
  -e "s#User=warehouse#User=${SERVICE_USER}#g" \
  -e "s#Group=warehouse#Group=${SERVICE_GROUP}#g" \
  -e "s#Environment=PORT=5000#Environment=PORT=${PORT}#g" \
  -e "s#Environment=HOST=0.0.0.0#Environment=HOST=${HOST}#g" \
  -e "s#Environment=WORKERS=2#Environment=WORKERS=${WORKERS}#g" \
  -e "s#Environment=THREADS=32#Environment=THREADS=${THREADS}#g" \
  warehouse-suite.service > "$tmp_unit"

sudo install -m 0644 "$tmp_unit" "$unit_file"
rm -f "$tmp_unit"

sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE_NAME"
sudo systemctl status "$SERVICE_NAME" --no-pager

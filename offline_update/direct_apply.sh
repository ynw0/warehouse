#!/usr/bin/env bash
set -Eeuo pipefail

PACKAGE_DIR="$(cd "$(dirname "$0")" && pwd)"
PAYLOAD_DIR="$PACKAGE_DIR/app"
APP_DIR="${1:-${APP_DIR:-}}"
UPDATE_VERSION="2026.7.27-direct"
SERVICE_NAME="${SERVICE_NAME:-}"

log() { printf '[%s] %s\n' "$UPDATE_VERSION" "$*"; }
fail() { log "ERROR: $*"; exit 1; }
if [ -z "$APP_DIR" ]; then
  read -r -p "请输入系统安装目录（例如 /mnt/disk0/warehouse-suit）： " APP_DIR
fi
if [ -z "$SERVICE_NAME" ]; then
  read -r -p "请输入 systemd 服务名（例如 warehouse-suit.service）： " SERVICE_NAME
fi


if [ -z "$APP_DIR" ]; then
  printf '用法：APP_DIR=/实际安装目录 bash update_direct.sh\n' >&2
  exit 2
fi
APP_DIR="$(cd "$APP_DIR" && pwd -P)"
[ "$APP_DIR" != "/" ] || fail "拒绝以根目录作为安装目录"
[ -f "$PAYLOAD_DIR/app.py" ] || fail "更新载荷不完整"
[ -f "$APP_DIR/app.py" ] || fail "安装目录不正确"
[ -d "$APP_DIR/warehouse_suit" ] || fail "安装目录不正确"
[ -x "$APP_DIR/.venv/bin/python" ] || fail "缺少 .venv/bin/python"
[ -n "$SERVICE_NAME" ] || fail "必须提供 systemd 服务名"
systemctl show --property=LoadState --value "$SERVICE_NAME" | grep -qx "loaded" || fail "未找到已加载的 systemd 服务：$SERVICE_NAME"

if command -v sha256sum >/dev/null 2>&1; then
  (cd "$PACKAGE_DIR" && sha256sum -c checksums.sha256 >/dev/null) || fail "更新包校验失败"
fi

STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="$APP_DIR/backups/direct_update_${UPDATE_VERSION}_${STAMP}"
ITEMS=(app.py pyproject.toml run.sh start_background.sh stop.sh status.sh warehouse_suit static templates wuliao_skill)
ROLLBACK_NEEDED=0

stop_service() {
  if [ -n "$SERVICE_NAME" ]; then
    systemctl stop "$SERVICE_NAME"
  elif [ -f "$APP_DIR/stop.sh" ]; then
    (cd "$APP_DIR" && bash stop.sh)
  fi
}

start_service() {
  if [ -n "$SERVICE_NAME" ]; then
    systemctl start "$SERVICE_NAME"
    systemctl is-active --quiet "$SERVICE_NAME" || fail "systemd 服务未能启动：$SERVICE_NAME"
  elif [ -f "$APP_DIR/start_background.sh" ]; then
    (cd "$APP_DIR" && bash start_background.sh)
  fi
}

restore_backup() {
  log "更新失败，回退本次覆盖的程序文件..."
  stop_service >/dev/null 2>&1 || true
  for item in "${ITEMS[@]}"; do
    if [ -e "$BACKUP_DIR/$item" ] || [ -L "$BACKUP_DIR/$item" ]; then
      rm -rf -- "$APP_DIR/$item"
      mkdir -p "$APP_DIR/$(dirname "$item")"
      cp -a "$BACKUP_DIR/$item" "$APP_DIR/$item"
    fi
  done
  start_service >/dev/null 2>&1 || true
  log "已回退；备份：$BACKUP_DIR"
}
on_error() {
  status=$?
  if [ "$ROLLBACK_NEEDED" = "1" ]; then restore_backup; fi
  exit "$status"
}
trap on_error ERR

log "安装目录：$APP_DIR"
log "备份程序文件到：$BACKUP_DIR"
mkdir -p "$BACKUP_DIR"
for item in "${ITEMS[@]}"; do
  if [ -e "$APP_DIR/$item" ] || [ -L "$APP_DIR/$item" ]; then
    mkdir -p "$BACKUP_DIR/$(dirname "$item")"
    cp -a "$APP_DIR/$item" "$BACKUP_DIR/$item"
  fi
done

log "停止当前服务..."
stop_service

ROLLBACK_NEEDED=1
log "直接覆盖程序文件（不触碰 data、.venv、上传、日志和备份）..."
for item in "${ITEMS[@]}"; do
  [ -e "$PAYLOAD_DIR/$item" ] || continue
  rm -rf -- "$APP_DIR/$item"
  mkdir -p "$APP_DIR/$(dirname "$item")"
  cp -a "$PAYLOAD_DIR/$item" "$APP_DIR/$item"
done
chmod +x "$APP_DIR/run.sh" "$APP_DIR/start_background.sh" "$APP_DIR/stop.sh" "$APP_DIR/status.sh" 2>/dev/null || true

log "校验 Python 语法..."
"$APP_DIR/.venv/bin/python" -m py_compile "$APP_DIR/app.py"
log "启动新服务..."
start_service

ROLLBACK_NEEDED=0
trap - ERR
log "直接更新完成。备份目录：$BACKUP_DIR"

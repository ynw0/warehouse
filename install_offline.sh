#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-.venv}"
DATA_DIR="${DATA_DIR:-$(pwd)/data}"

mkdir -p "$DATA_DIR"
if [ ! -f "$DATA_DIR/warehouse.db" ]; then
  if [ -f "warehouse.db" ]; then
    mv warehouse.db "$DATA_DIR/warehouse.db"
  elif [ -f "warehouse_cards.db" ]; then
    mv warehouse_cards.db "$DATA_DIR/warehouse.db"
  fi
fi

"$PYTHON_BIN" -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"
python -m pip install --no-index --find-links wheelhouse -r requirements-runtime.txt

cat > "$VENV_DIR/bin/warehouse_suit" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$APP_DIR"
exec "$SCRIPT_DIR/python" -m warehouse_suit "$@"
EOF
chmod +x "$VENV_DIR/bin/warehouse_suit"

chmod +x run.sh start_background.sh stop.sh status.sh install_systemd_service.sh

echo "安装完成。"
echo "前台运行：./run.sh"
echo "后台运行：./start_background.sh"
echo "默认地址：http://服务器IP:5000"

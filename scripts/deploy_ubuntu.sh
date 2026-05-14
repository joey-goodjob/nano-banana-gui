#!/usr/bin/env bash
set -euo pipefail

APP_NAME="${APP_NAME:-nano-banana-gui}"
APP_DIR="${APP_DIR:-$(pwd)}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8501}"
RUN_USER="${RUN_USER:-${SUDO_USER:-$(id -un)}}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This script only supports Linux/Ubuntu."
  exit 1
fi

if [[ ! -f "$APP_DIR/streamlit_app.py" || ! -f "$APP_DIR/requirements.txt" ]]; then
  echo "Run this script from the project root, or set APP_DIR=/path/to/nano-banana-gui."
  exit 1
fi

if ! command -v sudo >/dev/null 2>&1; then
  echo "sudo is required to install system packages and register the systemd service."
  exit 1
fi

echo "==> App directory: $APP_DIR"
echo "==> Run user: $RUN_USER"
echo "==> Service name: $APP_NAME"
echo "==> Listen address: $HOST:$PORT"

echo "==> Installing Ubuntu packages..."
sudo apt-get update
sudo apt-get install -y \
  python3 \
  python3-venv \
  python3-pip \
  build-essential \
  libjpeg-dev \
  zlib1g-dev

echo "==> Creating Python virtual environment..."
"$PYTHON_BIN" -m venv "$APP_DIR/.venv"

echo "==> Installing Python dependencies..."
"$APP_DIR/.venv/bin/python" -m pip install --upgrade pip setuptools wheel
"$APP_DIR/.venv/bin/python" -m pip install -r "$APP_DIR/requirements.txt"

echo "==> Writing systemd service..."
sudo tee "/etc/systemd/system/${APP_NAME}.service" >/dev/null <<SERVICE
[Unit]
Description=Nano Banana Streamlit Web App
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${APP_DIR}
Environment=PYTHONUNBUFFERED=1
ExecStart=${APP_DIR}/.venv/bin/python -m streamlit run streamlit_app.py --server.address ${HOST} --server.port ${PORT} --server.headless true
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE

echo "==> Starting service..."
sudo systemctl daemon-reload
sudo systemctl enable --now "$APP_NAME"

if command -v ufw >/dev/null 2>&1 && sudo ufw status | grep -q "Status: active"; then
  echo "==> UFW is active. Opening ${PORT}/tcp..."
  sudo ufw allow "${PORT}/tcp"
fi

echo "==> Service status:"
sudo systemctl --no-pager --full status "$APP_NAME" || true

echo
echo "Deployment finished."
echo "Local test: http://127.0.0.1:${PORT}"
echo "Public URL: http://YOUR_SERVER_PUBLIC_IP:${PORT}"
echo
echo "View logs: sudo journalctl -u ${APP_NAME} -f"
echo "Restart: sudo systemctl restart ${APP_NAME}"

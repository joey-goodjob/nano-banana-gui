#!/usr/bin/env bash
set -euo pipefail

APP_NAME="${APP_NAME:-nano-banana-gui}"
APP_DIR="${APP_DIR:-/opt/nano-banana-gui}"
ZIP_PATH="${1:-${ZIP_PATH:-/opt/nano-banana-gui-deploy.zip}}"
TMP_DIR="$(mktemp -d)"

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

if [[ ! -f "$ZIP_PATH" ]]; then
  echo "ZIP file not found: $ZIP_PATH"
  echo "Upload nano-banana-gui-deploy.zip to /opt, then run:"
  echo "  bash scripts/update_ubuntu_zip.sh /opt/nano-banana-gui-deploy.zip"
  exit 1
fi

if [[ ! -d "$APP_DIR" ]]; then
  echo "App directory not found: $APP_DIR"
  echo "Run scripts/deploy_ubuntu.sh first, or set APP_DIR=/path/to/app."
  exit 1
fi

echo "==> App directory: $APP_DIR"
echo "==> ZIP package: $ZIP_PATH"
echo "==> Service name: $APP_NAME"

echo "==> Installing unzip/rsync if needed..."
sudo apt-get update
sudo apt-get install -y unzip rsync

echo "==> Extracting package..."
unzip -q "$ZIP_PATH" -d "$TMP_DIR/package"

SOURCE_DIR="$TMP_DIR/package"
if [[ ! -f "$SOURCE_DIR/streamlit_app.py" ]]; then
  nested_count="$(find "$SOURCE_DIR" -mindepth 1 -maxdepth 1 -type d | wc -l)"
  if [[ "$nested_count" -eq 1 ]]; then
    SOURCE_DIR="$(find "$SOURCE_DIR" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
  fi
fi

if [[ ! -f "$SOURCE_DIR/streamlit_app.py" || ! -f "$SOURCE_DIR/requirements.txt" ]]; then
  echo "Invalid package: streamlit_app.py or requirements.txt not found."
  exit 1
fi

echo "==> Syncing files..."
rsync -a --delete \
  --exclude ".git/" \
  --exclude ".venv/" \
  --exclude "config.json" \
  --exclude "输出/" \
  "$SOURCE_DIR/" "$APP_DIR/"

echo "==> Fixing ownership..."
sudo chown -R "${SUDO_USER:-$USER}:${SUDO_USER:-$USER}" "$APP_DIR"

if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
  echo "==> Virtual environment not found. Running deploy script..."
  cd "$APP_DIR"
  bash scripts/deploy_ubuntu.sh
  exit 0
fi

echo "==> Updating Python dependencies..."
"$APP_DIR/.venv/bin/python" -m pip install -r "$APP_DIR/requirements.txt"

echo "==> Restarting service..."
sudo systemctl restart "$APP_NAME"

echo "==> Service status:"
sudo systemctl --no-pager --full status "$APP_NAME" || true

echo
echo "ZIP update finished."
echo "View logs: sudo journalctl -u ${APP_NAME} -f"

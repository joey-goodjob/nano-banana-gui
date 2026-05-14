#!/usr/bin/env bash
set -euo pipefail

APP_NAME="${APP_NAME:-nano-banana-gui}"
APP_DIR="${APP_DIR:-$(pwd)}"

if [[ ! -d "$APP_DIR/.git" ]]; then
  echo "APP_DIR is not a Git repo. If deployed by ZIP, upload the new code and run deploy_ubuntu.sh again."
  exit 1
fi

if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
  echo "Virtual environment not found. Run scripts/deploy_ubuntu.sh first."
  exit 1
fi

echo "==> Pulling latest code..."
git -C "$APP_DIR" pull --ff-only

echo "==> Updating dependencies..."
"$APP_DIR/.venv/bin/python" -m pip install -r "$APP_DIR/requirements.txt"

echo "==> Restarting service..."
sudo systemctl restart "$APP_NAME"

echo "==> Service status:"
sudo systemctl --no-pager --full status "$APP_NAME" || true

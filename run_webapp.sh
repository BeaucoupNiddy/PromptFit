#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [[ ! -d ".venv" ]]; then
  python3 -m venv .venv
fi

source ".venv/bin/activate"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

APP_HOST="${PROMPTFIT_HOST:-0.0.0.0}"
APP_PORT="${PROMPTFIT_PORT:-8000}"

echo "Prompt → FIT is starting at http://localhost:${APP_PORT}"
if [[ "$(uname -s)" == "Darwin" ]] && command -v scutil >/dev/null 2>&1; then
  MAC_NAME="$(scutil --get LocalHostName 2>/dev/null || true)"
  if [[ -n "$MAC_NAME" ]]; then
    echo "On your phone (same Wi-Fi): http://${MAC_NAME}.local:${APP_PORT}/#garmin-connect"
  fi
fi

exec uvicorn webapp.app:app --reload --host "$APP_HOST" --port "$APP_PORT"

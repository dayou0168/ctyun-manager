#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export PATH="$APP_DIR/.venv/bin:$PATH"
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$APP_DIR/.playwright}"
export CTYUN_MANAGER_DB="${CTYUN_MANAGER_DB:-$APP_DIR/data/ctyun-manager.db}"
export HOME="${HOME:-$APP_DIR/data/home}"
export DISPLAY="${DISPLAY:-:99}"
APP_PORT="${CTYUN_MANAGER_PORT:-8000}"
START_XVFB="${CTYUN_START_XVFB:-1}"

mkdir -p "$APP_DIR/data/home"
cd "$APP_DIR"

if [ "$START_XVFB" != "0" ]; then
  Xvfb "$DISPLAY" -screen 0 1440x900x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
  sleep 1
  fluxbox -display "$DISPLAY" >/tmp/fluxbox.log 2>&1 &
  sleep 1
  x11vnc -display "$DISPLAY" -forever -shared -listen 127.0.0.1 -rfbport 5900 -nopw -noxdamage -o /tmp/x11vnc.log -bg

  vnc_ready=0
  for _ in $(seq 1 20); do
    if python -c "import socket; s=socket.create_connection(('127.0.0.1', 5900), 1); s.close()" >/dev/null 2>&1; then
      vnc_ready=1
      break
    fi
    sleep 0.5
  done

  if [ "$vnc_ready" -ne 1 ]; then
    echo "x11vnc failed to start"
    cat /tmp/xvfb.log /tmp/fluxbox.log /tmp/x11vnc.log 2>/dev/null || true
    exit 1
  fi
fi

exec uvicorn app.main:app --host 0.0.0.0 --port "$APP_PORT"

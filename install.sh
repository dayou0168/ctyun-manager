#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this installer as root: sudo ./install.sh"
  exit 1
fi

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="ctyun-manager"
SERVICE_USER="ctyun-manager"
VENV_DIR="$APP_DIR/.venv"
BROWSER_DIR="$APP_DIR/.playwright"
DATA_DIR="$APP_DIR/data"

cd "$APP_DIR"
mkdir -p "$DATA_DIR/home"
systemctl stop "$SERVICE_NAME" >/dev/null 2>&1 || true

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  python3 python3-venv python3-pip \
  xvfb x11vnc fluxbox novnc \
  ca-certificates curl fonts-noto-cjk iproute2

if ss -H -ltn 'sport = :8000' | grep -q .; then
  echo "Port 8000 is already in use by another service. This installer will not stop or migrate it."
  ss -ltnp 'sport = :8000' || true
  echo "Stop the old service, then run ./install.sh again."
  exit 1
fi

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$DATA_DIR/home" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install --prefer-binary --timeout 120 --retries 10 -r requirements.txt

PLAYWRIGHT_BROWSERS_PATH="$BROWSER_DIR" \
  "$VENV_DIR/bin/python" -m playwright install --with-deps chromium

if [ ! -f "$APP_DIR/.env" ]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  SESSION_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  sed -i "s|^CTYUN_MANAGER_SESSION_SECRET=.*|CTYUN_MANAGER_SESSION_SECRET=$SESSION_SECRET|" "$APP_DIR/.env"
fi

chmod +x "$APP_DIR/install.sh" "$APP_DIR/start.sh" "$APP_DIR/restart.sh" "$APP_DIR/stop.sh"
chown -R "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR" "$BROWSER_DIR"
chown root:"$SERVICE_USER" "$APP_DIR/.env"
chmod 640 "$APP_DIR/.env"

cat >"/etc/systemd/system/$SERVICE_NAME.service" <<EOF
[Unit]
Description=Ctyun Manager
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=-$APP_DIR/.env
Environment=PYTHONUNBUFFERED=1
ExecStart=$APP_DIR/start.sh
Restart=always
RestartSec=5
TimeoutStopSec=20
KillMode=control-group

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"
sleep 3

BUILD_VERSION="$(grep -Eo 'APP_VERSION = "[^"]+"' "$APP_DIR/app/main.py" | cut -d'"' -f2 || true)"
echo "Installed ctyun-manager ${BUILD_VERSION:-unknown}"
systemctl --no-pager --full status "$SERVICE_NAME" || true
echo "URL: http://SERVER_IP:8000"
echo "Initial username: admin"
echo "Initial password: change-me-now"
echo "Change CTYUN_MANAGER_ADMIN_PASSWORD in $APP_DIR/.env, then run ./restart.sh"
echo "Logs: journalctl -u $SERVICE_NAME -f"

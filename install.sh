#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this installer as root: sudo ./install.sh"
  exit 1
fi

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="${CTYUN_MANAGER_SERVICE_NAME:-ctyun-manager}"
SERVICE_USER="${CTYUN_MANAGER_SERVICE_USER:-ctyun-manager}"
APP_PORT="${CTYUN_MANAGER_PORT:-8000}"
VENV_DIR="$APP_DIR/.venv"
BROWSER_DIR="$APP_DIR/.playwright"
DATA_DIR="$APP_DIR/data"

if ! command -v apt-get >/dev/null 2>&1; then
  echo "This one-click installer currently supports Debian/Ubuntu servers with apt-get."
  exit 1
fi

cd "$APP_DIR"
mkdir -p "$DATA_DIR/home"
systemctl stop "$SERVICE_NAME" >/dev/null 2>&1 || true

export DEBIAN_FRONTEND=noninteractive

echo "Updating apt package metadata..."
apt-get update

if [ "${CTYUN_MANAGER_SKIP_SYSTEM_UPGRADE:-0}" != "1" ]; then
  echo "Upgrading installed system packages..."
  apt-get upgrade -y
else
  echo "Skipping system package upgrade because CTYUN_MANAGER_SKIP_SYSTEM_UPGRADE=1"
fi

echo "Installing system dependencies..."
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  python3 python3-venv python3-pip \
  xvfb x11vnc fluxbox novnc \
  ca-certificates curl fonts-noto-cjk git iproute2 procps

if ss -H -ltn "sport = :$APP_PORT" | grep -q .; then
  echo "Port $APP_PORT is already in use by another service. This installer will not stop or migrate it."
  ss -ltnp "sport = :$APP_PORT" || true
  echo "Stop the old service, then run ./install.sh again."
  exit 1
fi

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$DATA_DIR/home" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install --prefer-binary --timeout 120 --retries 10 -r requirements.txt

echo "Installing Playwright Chromium browser..."
PLAYWRIGHT_BROWSERS_PATH="$BROWSER_DIR" \
  "$VENV_DIR/bin/python" -m playwright install --with-deps chromium

set_env_value() {
  local key="$1" value="$2" file="$APP_DIR/.env"
  if grep -q "^$key=" "$file"; then
    sed -i "s|^$key=.*|$key=$value|" "$file"
  else
    printf '%s=%s\n' "$key" "$value" >>"$file"
  fi
}

env_value() {
  local key="$1" file="$APP_DIR/.env"
  grep -E "^$key=" "$file" 2>/dev/null | tail -n 1 | cut -d= -f2- || true
}

generate_token() {
  python3 -c 'import secrets; print(secrets.token_urlsafe(36))'
}

if [ ! -f "$APP_DIR/.env" ]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
fi

ADMIN_PASSWORD="$(env_value CTYUN_MANAGER_ADMIN_PASSWORD)"
GENERATED_ADMIN_PASSWORD=0
if [ -z "$ADMIN_PASSWORD" ] || [ "$ADMIN_PASSWORD" = "change-me-now" ]; then
  ADMIN_PASSWORD="$(generate_token)"
  set_env_value CTYUN_MANAGER_ADMIN_PASSWORD "$ADMIN_PASSWORD"
  GENERATED_ADMIN_PASSWORD=1
fi

SESSION_SECRET="$(env_value CTYUN_MANAGER_SESSION_SECRET)"
if [ -z "$SESSION_SECRET" ] || [ "$SESSION_SECRET" = "change-this-session-secret" ]; then
  set_env_value CTYUN_MANAGER_SESSION_SECRET "$(generate_token)"
fi

for script in install.sh install-linux.sh install-docker.sh install-compose.sh start.sh restart.sh stop.sh; do
  if [ -f "$APP_DIR/$script" ]; then
    chmod +x "$APP_DIR/$script"
  fi
done
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
Environment=CTYUN_MANAGER_PORT=$APP_PORT
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
echo "URL: http://SERVER_IP:$APP_PORT"
ADMIN_USER="$(env_value CTYUN_MANAGER_ADMIN_USER)"
echo "Admin username: ${ADMIN_USER:-admin}"
if [ "$GENERATED_ADMIN_PASSWORD" = "1" ]; then
  echo "Generated admin password: $ADMIN_PASSWORD"
else
  echo "Admin password: using CTYUN_MANAGER_ADMIN_PASSWORD from $APP_DIR/.env"
fi
echo "Change CTYUN_MANAGER_ADMIN_PASSWORD in $APP_DIR/.env, then run ./restart.sh"
echo "Logs: journalctl -u $SERVICE_NAME -f"

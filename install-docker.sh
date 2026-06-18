#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_PORT="${CTYUN_MANAGER_PORT:-8000}"

if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
else
  if ! command -v sudo >/dev/null 2>&1; then
    echo "Run this installer as root or install sudo first."
    exit 1
  fi
  SUDO="sudo"
fi

if ! command -v apt-get >/dev/null 2>&1; then
  echo "This Docker Compose installer currently supports Debian/Ubuntu servers with apt-get."
  exit 1
fi

install_docker() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    return
  fi

  . /etc/os-release
  case "${ID:-}" in
    ubuntu|debian) ;;
    *)
      echo "Unsupported Linux distribution: ${ID:-unknown}. Please install Docker Engine and the Compose plugin first."
      exit 1
      ;;
  esac

  echo "Installing Docker Engine and Docker Compose plugin..."
  $SUDO apt-get update
  $SUDO apt-get install -y --no-install-recommends ca-certificates curl gnupg
  $SUDO install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/${ID}/gpg" | $SUDO gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg
  $SUDO chmod a+r /etc/apt/keyrings/docker.gpg

  CODENAME="${VERSION_CODENAME:-}"
  if [ -z "$CODENAME" ]; then
    CODENAME="$(. /etc/os-release && echo "${UBUNTU_CODENAME:-}")"
  fi
  if [ -z "$CODENAME" ]; then
    echo "Could not detect distribution codename for Docker apt repository."
    exit 1
  fi

  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/${ID} ${CODENAME} stable" \
    | $SUDO tee /etc/apt/sources.list.d/docker.list >/dev/null
  $SUDO apt-get update
  $SUDO apt-get install -y --no-install-recommends docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
}

create_env_file() {
  cd "$APP_DIR"
  mkdir -p data
  $SUDO chown -R 10001:10001 data

  if [ ! -f .env ]; then
    cp .env.example .env
    SESSION_SECRET="$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')"
    sed -i "s|^CTYUN_MANAGER_SESSION_SECRET=.*|CTYUN_MANAGER_SESSION_SECRET=$SESSION_SECRET|" .env
  fi
}

install_docker
create_env_file

cd "$APP_DIR"
chmod +x install-docker.sh install-compose.sh install-linux.sh start.sh restart.sh stop.sh install.sh 2>/dev/null || true

if command -v ss >/dev/null 2>&1 && ss -H -ltn "sport = :$APP_PORT" 2>/dev/null | grep -q .; then
  if ! $SUDO docker ps --format '{{.Names}}' | grep -qx 'ctyun-manager'; then
    echo "Port $APP_PORT is already in use by another service. Set CTYUN_MANAGER_PORT=another_port or stop that service."
    ss -ltnp "sport = :$APP_PORT" || true
    exit 1
  fi
fi

$SUDO docker compose build
$SUDO docker compose up -d
$SUDO docker compose ps

echo "Installed ctyun-manager with Docker Compose"
echo "URL: http://SERVER_IP:$APP_PORT"
echo "Initial username: admin"
echo "Initial password: change-me-now"
echo "Change CTYUN_MANAGER_ADMIN_PASSWORD in $APP_DIR/.env, then run: docker compose up -d"
echo "Logs: docker compose logs -f"

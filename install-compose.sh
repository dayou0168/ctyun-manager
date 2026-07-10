#!/usr/bin/env bash
set -euo pipefail

REPO_OWNER="${CTYUN_MANAGER_REPO_OWNER:-dayou0168}"
REPO_NAME="${CTYUN_MANAGER_REPO_NAME:-ctyun-manager}"
REPO_REF="${CTYUN_MANAGER_REPO_REF:-main}"
INSTALL_DIR="${CTYUN_MANAGER_INSTALL_DIR:-/opt/ctyun-manager}"
APP_PORT="${CTYUN_MANAGER_PORT:-8000}"
IMAGE_NAME="${CTYUN_MANAGER_IMAGE:-ghcr.io/$REPO_OWNER/$REPO_NAME:latest}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this installer as root, for example:"
  echo "curl -fsSL https://raw.githubusercontent.com/$REPO_OWNER/$REPO_NAME/$REPO_REF/install-compose.sh | sudo bash"
  exit 1
fi

if [ -z "$INSTALL_DIR" ] || [ "$INSTALL_DIR" = "/" ]; then
  echo "Refusing to install into an unsafe directory: $INSTALL_DIR"
  exit 1
fi

if ! command -v apt-get >/dev/null 2>&1; then
  echo "This Docker Compose installer currently supports Debian/Ubuntu servers with apt-get."
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

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
  apt-get update
  apt-get install -y --no-install-recommends ca-certificates curl gnupg iproute2
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/${ID}/gpg" | gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg

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
    | tee /etc/apt/sources.list.d/docker.list >/dev/null
  apt-get update
  apt-get install -y --no-install-recommends docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
}

download_compose_file() {
  mkdir -p "$INSTALL_DIR"
  cd "$INSTALL_DIR"

  RAW_URL="https://raw.githubusercontent.com/$REPO_OWNER/$REPO_NAME/$REPO_REF/docker-compose.deploy.yml"
  API_URL="https://api.github.com/repos/$REPO_OWNER/$REPO_NAME/contents/docker-compose.deploy.yml?ref=$REPO_REF"

  echo "Downloading docker-compose.deploy.yml..."
  if [ -n "${GITHUB_TOKEN:-}" ]; then
    curl -fsSL --retry 3 --connect-timeout 20 \
      -H "Authorization: Bearer $GITHUB_TOKEN" \
      -H "Accept: application/vnd.github.raw" \
      "$API_URL" \
      -o docker-compose.deploy.yml
  else
    curl -fsSL --retry 3 --connect-timeout 20 -L "$RAW_URL" -o docker-compose.deploy.yml
  fi
}

create_env_file() {
  cd "$INSTALL_DIR"
  generate_token() {
    od -An -N36 -tx1 /dev/urandom | tr -d ' \n'
  }

  set_env_value() {
    local key="$1" value="$2"
    if grep -q "^$key=" .env; then
      sed -i "s|^$key=.*|$key=$value|" .env
    else
      printf '%s=%s\n' "$key" "$value" >>.env
    fi
  }

  env_value() {
    local key="$1"
    grep -E "^$key=" .env 2>/dev/null | tail -n 1 | cut -d= -f2- || true
  }

  if [ ! -f .env ]; then
    ADMIN_PASSWORD="$(generate_token)"
    SESSION_SECRET="$(generate_token)"
    GENERATED_ADMIN_PASSWORD=1
    cat >.env <<EOF
CTYUN_MANAGER_PORT=$APP_PORT
CTYUN_MANAGER_IMAGE=$IMAGE_NAME
CTYUN_MANAGER_ADMIN_USER=admin
CTYUN_MANAGER_ADMIN_PASSWORD=$ADMIN_PASSWORD
CTYUN_MANAGER_SESSION_SECRET=$SESSION_SECRET
CTYUN_MANAGER_PUBLIC_URL=http://127.0.0.1:$APP_PORT
CTYUN_BROWSER_HEADFUL=1
CTYUN_RECHARGE_PREWARM_ENABLED=1
CTYUN_RECHARGE_FAST_ORDER_ENABLED=1
CTYUN_RECHARGE_QR_CACHE_ENABLED=1
EOF
    chmod 600 .env
  else
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
  fi
}

login_to_registry() {
  if [ -n "${GITHUB_TOKEN:-}" ]; then
    echo "$GITHUB_TOKEN" | docker login ghcr.io -u "$REPO_OWNER" --password-stdin || true
  fi
}

check_port() {
  if command -v ss >/dev/null 2>&1 && ss -H -ltn "sport = :$APP_PORT" 2>/dev/null | grep -q .; then
    if ! docker ps --format '{{.Names}}' | grep -qx 'ctyun-manager'; then
      echo "Port $APP_PORT is already in use by another service. Set CTYUN_MANAGER_PORT=another_port or stop that service."
      ss -ltnp "sport = :$APP_PORT" || true
      exit 1
    fi
  fi
}

apt-get update
apt-get install -y --no-install-recommends ca-certificates curl iproute2

install_docker
download_compose_file
create_env_file
login_to_registry
check_port

cd "$INSTALL_DIR"
docker compose -f docker-compose.deploy.yml pull
docker compose -f docker-compose.deploy.yml up -d
docker compose -f docker-compose.deploy.yml ps

echo "Installed ctyun-manager with Docker Compose"
echo "Image: $IMAGE_NAME"
echo "Compose file: $INSTALL_DIR/docker-compose.deploy.yml"
echo "URL: http://SERVER_IP:$APP_PORT"
echo "Admin username: admin"
if [ "${GENERATED_ADMIN_PASSWORD:-0}" = "1" ]; then
  echo "Generated admin password: $ADMIN_PASSWORD"
else
  echo "Admin password: using CTYUN_MANAGER_ADMIN_PASSWORD from $INSTALL_DIR/.env"
fi
echo "Change CTYUN_MANAGER_ADMIN_PASSWORD in $INSTALL_DIR/.env, then run:"
echo "  cd $INSTALL_DIR && docker compose -f docker-compose.deploy.yml up -d"
echo "Logs:"
echo "  cd $INSTALL_DIR && docker compose -f docker-compose.deploy.yml logs -f"

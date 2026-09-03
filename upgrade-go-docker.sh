#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="${CTYUN_MANAGER_SOURCE_DIR:-$(cd "$(dirname "$0")" && pwd)}"
INSTALL_DIR="${CTYUN_MANAGER_INSTALL_DIR:-/opt/ctyun-manager}"
SERVICE_NAME="${CTYUN_MANAGER_SERVICE_NAME:-ctyun-manager}"
CONTAINER_NAME="${CTYUN_MANAGER_CONTAINER_NAME:-ctyun-manager}"
PORT="${CTYUN_MANAGER_PORT:-8000}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RELEASE_ROOT="$INSTALL_DIR/.go-releases"
RELEASE_DIR="$RELEASE_ROOT/$STAMP"
BACKUP_DIR="$INSTALL_DIR/backups/go-migration-$STAMP"
IMAGE="ctyun-manager:go-$STAMP"
OLD_SERVICE_ACTIVE=0
OLD_SERVICE_ENABLED=0
OLD_CONTAINER_ID=""
OLD_CONTAINER_RUNNING=0
ROLLBACK_CONTAINER="$CONTAINER_NAME-rollback-$STAMP"
BUILD_FILE="Dockerfile"

fail() { echo "[upgrade] ERROR: $*" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || fail "run as root"
[ -n "$INSTALL_DIR" ] && [ "$INSTALL_DIR" != "/" ] || fail "unsafe install directory"
[ -f "$SOURCE_DIR/Dockerfile" ] && [ -f "$SOURCE_DIR/docker-compose.deploy.yml" ] || fail "source package incomplete"
[ -f "$SOURCE_DIR/worker/recharge.mjs" ] || fail "source package is missing the Node.js recharge worker"
[ -d "$INSTALL_DIR/data" ] || fail "existing data directory not found: $INSTALL_DIR/data"

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  fail "Docker Compose is required"
fi
command -v sqlite3 >/dev/null 2>&1 || fail "sqlite3 is required for a consistent backup"

mkdir -p "$RELEASE_DIR" "$BACKUP_DIR"
cp -a "$SOURCE_DIR"/. "$RELEASE_DIR"/
[ ! -f "$INSTALL_DIR/.env" ] || cp -a "$INSTALL_DIR/.env" "$RELEASE_DIR/.env"

case "$(uname -m)" in
  x86_64|amd64) PREBUILT_BINARY="$RELEASE_DIR/bin/ctyun-manager-linux-amd64" ;;
  aarch64|arm64) PREBUILT_BINARY="$RELEASE_DIR/bin/ctyun-manager-linux-arm64" ;;
  *) PREBUILT_BINARY="" ;;
esac
if [ -n "$PREBUILT_BINARY" ] && [ -f "$PREBUILT_BINARY" ]; then
  cp -a "$PREBUILT_BINARY" "$RELEASE_DIR/ctyun-manager-linux"
  chmod 755 "$RELEASE_DIR/ctyun-manager-linux"
  BUILD_FILE="Dockerfile.prebuilt"
fi
if [ ! -f "$RELEASE_DIR/.env" ]; then
  command -v openssl >/dev/null 2>&1 || fail "openssl is required to generate a session secret"
  GENERATED_SESSION_SECRET="$(openssl rand -hex 48)" || fail "could not generate a session secret"
  [ "${#GENERATED_SESSION_SECRET}" -eq 96 ] || fail "generated session secret has an unexpected length"
  umask 077
  {
    printf 'CTYUN_MANAGER_SESSION_SECRET=%s\n' "$GENERATED_SESSION_SECRET"
    printf 'CTYUN_MANAGER_PUBLIC_URL=http://127.0.0.1:%s\n' "$PORT"
  } > "$RELEASE_DIR/.env"
fi
chmod 700 "$BACKUP_DIR"

echo "[upgrade] Building $IMAGE with $BUILD_FILE before stopping the current service..."
docker build --pull -f "$RELEASE_DIR/$BUILD_FILE" --build-arg VERSION="$STAMP" --build-arg BUILD_TIME="$STAMP" -t "$IMAGE" "$RELEASE_DIR"

if systemctl is-active --quiet "$SERVICE_NAME"; then OLD_SERVICE_ACTIVE=1; fi
if systemctl is-enabled --quiet "$SERVICE_NAME"; then OLD_SERVICE_ENABLED=1; fi
OLD_CONTAINER_ID="$(docker ps -aq --filter "name=^/${CONTAINER_NAME}$" | head -n 1)"
if [ -n "$OLD_CONTAINER_ID" ] && [ "$(docker inspect -f '{{.State.Running}}' "$OLD_CONTAINER_ID")" = "true" ]; then
  OLD_CONTAINER_RUNNING=1
fi
rollback() {
  code=$?
  trap - EXIT
  if [ "$code" -ne 0 ]; then
    echo "[upgrade] New service failed; rolling back."
    (cd "$RELEASE_DIR" && CTYUN_MANAGER_IMAGE="$IMAGE" CTYUN_MANAGER_DATA_DIR="$INSTALL_DIR/data" "${COMPOSE[@]}" -f docker-compose.deploy.yml down) >/dev/null 2>&1 || true
    if docker container inspect "$ROLLBACK_CONTAINER" >/dev/null 2>&1; then
      docker rename "$ROLLBACK_CONTAINER" "$CONTAINER_NAME" >/dev/null 2>&1 || true
      if [ "$OLD_CONTAINER_RUNNING" -eq 1 ]; then docker start "$CONTAINER_NAME" >/dev/null 2>&1 || true; fi
    fi
    if [ "$OLD_SERVICE_ENABLED" -eq 1 ]; then systemctl enable "$SERVICE_NAME" >/dev/null 2>&1 || true; fi
    if [ "$OLD_SERVICE_ACTIVE" -eq 1 ]; then systemctl start "$SERVICE_NAME" || true; fi
    echo "[upgrade] Backup retained at $BACKUP_DIR"
  fi
  exit "$code"
}
trap rollback EXIT

systemctl stop "$SERVICE_NAME" >/dev/null 2>&1 || true
DB="$INSTALL_DIR/data/ctyun-manager.db"
[ -f "$DB" ] || fail "database not found: $DB"
sqlite3 "$DB" ".timeout 30000" ".backup '$BACKUP_DIR/ctyun-manager.db'"
sqlite3 "$BACKUP_DIR/ctyun-manager.db" "pragma integrity_check" | grep -qx ok || fail "backup integrity check failed"
for file in "$INSTALL_DIR/data/master.key" "$INSTALL_DIR/.env"; do [ ! -f "$file" ] || cp -a "$file" "$BACKUP_DIR/"; done

if [ -n "$OLD_CONTAINER_ID" ]; then
  echo "[upgrade] Preserving the current container as $ROLLBACK_CONTAINER during cutover..."
  docker stop "$CONTAINER_NAME" >/dev/null 2>&1 || true
  docker rename "$CONTAINER_NAME" "$ROLLBACK_CONTAINER"
fi

echo "[upgrade] Starting Go + Node.js release..."
(cd "$RELEASE_DIR" && CTYUN_MANAGER_IMAGE="$IMAGE" CTYUN_MANAGER_DATA_DIR="$INSTALL_DIR/data" "${COMPOSE[@]}" -f docker-compose.deploy.yml up -d --remove-orphans)

healthy=0
for _ in $(seq 1 60); do
  if curl -fsS --max-time 3 "http://127.0.0.1:$PORT/readyz" >/dev/null; then healthy=1; break; fi
  sleep 2
done
[ "$healthy" -eq 1 ] || { (cd "$RELEASE_DIR" && "${COMPOSE[@]}" -f docker-compose.deploy.yml logs --tail=200) || true; fail "new service did not become ready"; }
curl -fsS --max-time 3 "http://127.0.0.1:$PORT/api/version" | grep -q '"migration_phase":6' || fail "new service version check failed"

if [ "$OLD_SERVICE_ENABLED" -eq 1 ]; then
  systemctl disable "$SERVICE_NAME" >/dev/null
fi
if docker container inspect "$ROLLBACK_CONTAINER" >/dev/null 2>&1; then
  docker rm "$ROLLBACK_CONTAINER" >/dev/null
fi

ln -sfn "$RELEASE_DIR" "$RELEASE_ROOT/current"
printf '%s\n' "$IMAGE" > "$RELEASE_ROOT/current-image"
trap - EXIT
echo "[upgrade] Upgrade complete. Backup: $BACKUP_DIR"
echo "[upgrade] Health: http://127.0.0.1:$PORT/readyz"

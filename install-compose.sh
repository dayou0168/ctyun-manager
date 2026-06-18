#!/usr/bin/env bash
set -euo pipefail

REPO_OWNER="${CTYUN_MANAGER_REPO_OWNER:-dayou0168}"
REPO_NAME="${CTYUN_MANAGER_REPO_NAME:-ctyun-manager}"
REPO_REF="${CTYUN_MANAGER_REPO_REF:-main}"
INSTALL_DIR="${CTYUN_MANAGER_INSTALL_DIR:-/opt/ctyun-manager}"

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

apt-get update
apt-get install -y --no-install-recommends ca-certificates curl tar

TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

ARCHIVE="$TMP_DIR/source.tar.gz"
DOWNLOAD_URL="https://api.github.com/repos/$REPO_OWNER/$REPO_NAME/tarball/$REPO_REF"
CURL_ARGS=(-fsSL --retry 3 --connect-timeout 20 -L)

if [ -n "${GITHUB_TOKEN:-}" ]; then
  CURL_ARGS+=(-H "Authorization: Bearer $GITHUB_TOKEN")
fi

echo "Downloading $REPO_OWNER/$REPO_NAME@$REPO_REF..."
if ! curl "${CURL_ARGS[@]}" "$DOWNLOAD_URL" -o "$ARCHIVE"; then
  echo "Failed to download project archive from GitHub."
  echo "If the repository is private, pass a token with repo permission:"
  echo "curl -fsSL -H \"Authorization: Bearer \$GITHUB_TOKEN\" -H \"Accept: application/vnd.github.raw\" \\"
  echo "  https://api.github.com/repos/$REPO_OWNER/$REPO_NAME/contents/install-compose.sh | sudo GITHUB_TOKEN=\"\$GITHUB_TOKEN\" bash"
  exit 1
fi

tar -xzf "$ARCHIVE" -C "$TMP_DIR"
SRC_DIR="$(find "$TMP_DIR" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
if [ -z "$SRC_DIR" ] || [ ! -f "$SRC_DIR/install-docker.sh" ]; then
  echo "Downloaded archive does not look like a ctyun-manager release."
  exit 1
fi

mkdir -p "$INSTALL_DIR"
for item in "$INSTALL_DIR"/* "$INSTALL_DIR"/.[!.]* "$INSTALL_DIR"/..?*; do
  [ -e "$item" ] || continue
  base="$(basename "$item")"
  case "$base" in
    .env|data|.playwright|.venv|master.key|*.db|*.db-shm|*.db-wal|*.log|work-*.out.log|work-*.err.log)
      continue
      ;;
  esac
  rm -rf "$item"
done

cp -a "$SRC_DIR"/. "$INSTALL_DIR"/
chmod +x "$INSTALL_DIR"/*.sh

cd "$INSTALL_DIR"
echo "Running Docker Compose installer in $INSTALL_DIR..."
./install-docker.sh

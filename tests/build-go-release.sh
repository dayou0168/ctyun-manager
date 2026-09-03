#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUTPUT_DIR="${1:-$ROOT/dist}"
VERSION="${VERSION:-$(date -u +%Y.%m.%d.%H%M)}"
BUILD_TIME="${BUILD_TIME:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"

[ -n "$OUTPUT_DIR" ] && [ "$OUTPUT_DIR" != "/" ] || { echo "unsafe output directory" >&2; exit 1; }
mkdir -p "$OUTPUT_DIR/bin"

for arch in amd64 arm64; do
  GOOS=linux GOARCH="$arch" CGO_ENABLED=0 go build -trimpath \
    -ldflags "-s -w -X github.com/dayou0168/ctyun-manager/internal/buildinfo.Version=$VERSION -X github.com/dayou0168/ctyun-manager/internal/buildinfo.BuildTime=$BUILD_TIME" \
    -o "$OUTPUT_DIR/bin/ctyun-manager-linux-$arch" "$ROOT/cmd/ctyun-manager-go"
done

printf '%s\n' "$VERSION" > "$OUTPUT_DIR/VERSION"
echo "Built Linux amd64/arm64 binaries in $OUTPUT_DIR/bin"

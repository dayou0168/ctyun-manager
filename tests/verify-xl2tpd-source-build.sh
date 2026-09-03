#!/usr/bin/env bash
set -euo pipefail

version="1.3.20"
source_url="https://github.com/xelerance/xl2tpd/archive/refs/tags/v${version}.tar.gz"
expected_sha="3db95450c5e1efaeea7547af344b5621f4453af3c227f26ec43bcbc79087b045"
test_dir="$(mktemp -d)"

cleanup() {
  local resolved
  resolved="$(readlink -f "$test_dir" 2>/dev/null || true)"
  case "$resolved" in
    /tmp/*) rm -rf -- "$resolved" ;;
    *) printf 'Refusing to remove unexpected test directory: %s\n' "$resolved" >&2 ;;
  esac
}
trap cleanup EXIT

curl --proto '=https' --tlsv1.2 -fL --retry 3 --connect-timeout 15 \
  "$source_url" -o "$test_dir/xl2tpd.tar.gz"
printf '%s  %s\n' "$expected_sha" "$test_dir/xl2tpd.tar.gz" | sha256sum -c -
tar -xzf "$test_dir/xl2tpd.tar.gz" -C "$test_dir"

# Match the CTyunOS fallback: pfc is deliberately excluded because it is not
# needed by the VPN server and makes libpcap-devel mandatory on ARM images.
make -C "$test_dir/xl2tpd-$version" -j2 xl2tpd xl2tpd-control
test -x "$test_dir/xl2tpd-$version/xl2tpd"
test -x "$test_dir/xl2tpd-$version/xl2tpd-control"

daemon_file="$(file "$test_dir/xl2tpd-$version/xl2tpd")"
control_file="$(file "$test_dir/xl2tpd-$version/xl2tpd-control")"
printf '%s\n%s\n' "$daemon_file" "$control_file"
if [ -n "${EXPECTED_FILE_PATTERN:-}" ]; then
  printf '%s\n%s\n' "$daemon_file" "$control_file" | grep -Eq "$EXPECTED_FILE_PATTERN"
fi

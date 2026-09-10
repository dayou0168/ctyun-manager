#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
installer="$repo_dir/install-l2tp-server.sh"

grep -Fq 'L2TP_SYSCTL_FILE=/etc/sysctl.d/zz-l2tp-vpn.conf' "$installer"
grep -Fq 'sysctl -p "$L2TP_SYSCTL_FILE" >/dev/null' "$installer"
grep -Fq 'sysctl -w "net.ipv4.conf.${VPN_IFACE}.rp_filter=0"' "$installer"

calls="$(grep -Fc 'apply_kernel_network_settings' "$installer")"
if [ "$calls" -lt 2 ]; then
  printf 'Expected the generated boot helper to define and call apply_kernel_network_settings.\n' >&2
  exit 1
fi

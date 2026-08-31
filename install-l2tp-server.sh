#!/usr/bin/env bash
set -euo pipefail

umask 077

SCRIPT_NAME="$(basename "$0")"
HELPER_PATH="/usr/local/sbin/l2tp-vip-egress-setup.sh"
HELPER_SERVICE="l2tp-vip-egress-setup.service"
XL2TPD_MULTI_HELPER="/usr/local/sbin/l2tp-xl2tpd-multi.sh"
XL2TPD_MULTI_SERVICE="l2tp-xl2tpd-multi.service"
XL2TPD_MULTI_CONFIG_DIR="/etc/xl2tpd/ctyun"
XL2TPD_MULTI_RUN_DIR="/run/xl2tpd-multi"
CREDENTIAL_FILE="/root/l2tp-vpn-credentials.txt"
CONFIG_DIR="/etc/l2tp-vpn"
USER_CONFIG_FILE="/etc/l2tp-vpn/users.conf"
SERVER_CONFIG_FILE="/etc/l2tp-vpn/server.conf"
USER_MAP_PATH="/etc/l2tp-vpn/users.tsv"
INSTALLER_PATH="/usr/local/sbin/l2tp-vpn-install.sh"
APPLY_CONFIG_SCRIPT="/usr/local/sbin/l2tp-vpn-apply-config.sh"
PPP_UP_SCRIPT="/usr/local/sbin/l2tp-ppp-up.sh"
PPP_DOWN_SCRIPT="/usr/local/sbin/l2tp-ppp-down.sh"
APPLY_USERS_SCRIPT="/usr/local/sbin/l2tp-vpn-apply-users.sh"
APPLY_USERS_SERVICE="l2tp-vpn-apply-users.service"
APPLY_USERS_PATH="l2tp-vpn-apply-users.path"
SERVER_CONFIG_VARS=(
  VPN_LOCAL_IP
  VPN_CLIENT_POOL
  VPN_CIDR
  VPN_L2TP_PORT
  VPN_DNS1
  VPN_DNS2
  VPN_MTU
  VPN_MRU
  VPN_IFACE
  VPN_PRIMARY_IP
  VPN_VIPS
  VPN_PLATFORM_SCAN
  VPN_VIP_CANDIDATES
  VPN_VIP_SCAN_RANGE
  VPN_VIP_SCAN_MAX
  VPN_VIP_SCAN_PARALLEL
  VPN_VIP_PROBE_TARGET
  VPN_LEFT_ID
  VPN_ENABLE_IPSEC
  VPN_RANDOM_PSK
  VPN_IPSEC_PSK
  VPN_DEFAULT_SHARE_COUNT
  VPN_AUTO_CLIENT_SCOPE
  VPN_INGRESS_MODE
  VPN_INGRESS_IP
  VPN_AUTO_CONFIG_FROM_USERS
  VPN_ENABLE_BBR
  VPN_DISABLE_DEFAULT_MASQ
  VPN_ADD_EGRESS_AS_VIP
)

usage() {
  cat <<EOF
Usage:
  sudo bash $SCRIPT_NAME

One-click L2TP server installer for Debian 9+, Ubuntu 18.04+, and CentOS 7/8/Stream 9.
Default mode is L2TP username/password only, without IPsec/PSK.

Common environment variables:
  VPN_INTERACTIVE     Ask for install parameters when a terminal is attached. Default: 1. Set 0 for unattended install.
  VPN_IPSEC_PSK       IPsec pre-shared key. If set, IPsec is enabled.
  VPN_USER            Single VPN username. Default: vpnuser.
  VPN_PASSWORD        Single VPN password. Default: random.
  VPN_SHARE_COUNT     Max simultaneous sessions for the single VPN user. Default: 1.
  VPN_DEFAULT_SHARE_COUNT Default share_count for VPN_USERS entries that omit it. Default: 1.
  VPN_CLIENT_IP       Fixed VPN client IP for the single user. Optional.
  VPN_EGRESS_IP       Local private VIP used as SNAT source for the single user. Optional.
  VPN_PUBLIC_IP_LABEL Public EIP label printed in the credential summary. Optional.
  VPN_ENABLE_IPSEC    Enable IPsec/PSK. Default: 0 unless VPN_IPSEC_PSK or VPN_RANDOM_PSK=1 is set.
  VPN_RANDOM_PSK      Generate a random PSK and enable IPsec when VPN_IPSEC_PSK is empty. Default: 0.

Multi-user, multi-EIP, and share-count mapping:
  VPN_USERS='user:pass[:egress_local_vip[:share_count[:vpn_ip[:public_eip_label]]]],user2:pass2:172.16.0.12:3::61.1.1.2'
  vpn_ip is optional. Leave it empty by default. Only set it when you need to force a user to a fixed client IP, CIDR, or range.

Persistent user config:
  $USER_CONFIG_FILE
  Edit one account per line, then save the file. A systemd path watcher applies changes automatically.
  Format: username,password,egress_local_vip,share_count,vpn_ip,public_eip_label

Persistent server config:
  $SERVER_CONFIG_FILE
  Edit L2TP port, MTU, MRU, DNS, VIPs, IPsec, and interface settings in this file.
  Apply changes with: $APPLY_CONFIG_SCRIPT

Network variables:
  VPN_LOCAL_IP        L2TP server local tunnel IP. Default: 172.18.0.1.
  VPN_CLIENT_POOL     Dynamic client pool. Default: 172.18.0.2-172.18.255.254.
  VPN_CIDR            VPN client CIDR for forwarding/NAT. Default: 172.18.0.0/16.
  VPN_L2TP_PORT       L2TP UDP port. Default: 1701.
  VPN_DNS1            DNS server pushed to clients. Default: 223.5.5.5.
  VPN_DNS2            DNS server pushed to clients. Default: 119.29.29.29.
  VPN_MTU             PPP MTU pushed by xl2tpd. Default: 1280.
  VPN_MRU             PPP MRU pushed by xl2tpd. Default: same as VPN_MTU.
  VPN_ENABLE_BBR      Try to enable TCP BBR before configuring L2TP. Default: 1.
  VPN_IFACE           Public/default network interface. Default: auto-detect.
  VPN_PRIMARY_IP      Primary private IPv4 on VPN_IFACE. Default: route-source auto-detection.
  VPN_VIPS            Comma-separated local VIPs to add on VPN_IFACE, for example 172.16.0.11/32,172.16.0.12/32.
  VPN_VIP_CANDIDATES  Platform-provided VIP candidates. With VPN_PLATFORM_SCAN=1, each address is temporarily
                      added and tested with source-address ping before it is accepted into VPN_VIPS.
  VPN_VIP_SCAN_RANGE  Optional scan scope: a single IP, CIDR, start-end range, or comma-separated combination.
  VPN_VIP_SCAN_MAX    Maximum expanded scan addresses. Default: 512; allowed: 1-4096.
  VPN_VIP_SCAN_PARALLEL Concurrent source-address ping probes per batch. Default: 32; allowed: 1-128.
  VPN_VIP_PROBE_TARGET Ping target used for active VIP verification. Default: www.baidu.com.
  VPN_INGRESS_MODE    L2TP ingress mode: smart or bound. Default: smart.
                      smart = one xl2tpd listener plus DNAT for other local ingress IPs.
                      bound = one xl2tpd listener per local ingress IP.
  VPN_CLIENT_POOL_MODE Client pool mode: auto, global, or per_vip. Default: auto.
                      global = one shared pool, users.conf column 5 should usually be empty.
                      per_vip = in bound mode, use users.conf column 5 as the pool for that local VIP.
  VPN_AUTO_CONFIG_FROM_USERS Emergency compatibility switch that may derive VPN_VIPS from users.conf. Default: 0.
                      Platform installs should keep this disabled and pass only the VIPs found by the cloud scan.
  VPN_INGRESS_IP      Local IP used by the single smart listener. Default: first IPv4 on VPN_IFACE.
  VPN_LEFT_ID         Optional strongSwan leftid. Leave empty for multi-EIP/VIP servers.

Package source variables:
  VPN_CONFIGURE_TUNA_MIRROR Switch supported system repositories to Tsinghua TUNA before installing. Default: 1.
  VPN_TUNA_MIRROR      Mirror root. Default: http://mirrors.tuna.tsinghua.edu.cn.
  VPN_APT_FORCE_IPV4   Force apt to use IPv4 and disable HTTP pipelining. Default: 1.

Tianyi Cloud VIP/EIP example:
  sudo VPN_USERS='acct1:pass1:172.16.0.11:2::61.1.1.1,acct2:pass2:172.16.0.12:3::61.1.1.2' \\
    VPN_VIPS='172.16.0.11/32,172.16.0.12/32' \\
    bash $SCRIPT_NAME

Several accounts using the same local VIP:
  sudo VPN_USERS='acct1:pass1:172.16.0.11:254::61.1.1.1,acct2:pass2:172.16.0.11:50::61.1.1.1,acct3:pass3:172.16.0.11:20::61.1.1.1' \\
    VPN_VIPS='172.16.0.11/32' \\
    bash $SCRIPT_NAME

Account with a dedicated client subnet:
  sudo VPN_USERS='acct1:pass1:172.16.0.11:254:172.18.120.0/24:61.1.1.1' \\
    VPN_VIPS='172.16.0.11/32' \\
    bash $SCRIPT_NAME

Account with a dedicated client IP range:
  sudo VPN_USERS='acct3:pass3:172.16.0.11:20:172.18.1.61-172.18.1.80:61.1.1.1' \\
    VPN_VIPS='172.16.0.11/32' \\
    bash $SCRIPT_NAME

Enable IPsec with a fixed PSK:
  sudo VPN_IPSEC_PSK='change-this-psk' bash $SCRIPT_NAME

Enable IPsec with a random PSK:
  sudo VPN_RANDOM_PSK=1 bash $SCRIPT_NAME

Notes:
  - Debian 9/10 and CentOS 7/8 are EOL. The installer can use archive repositories, but those systems no longer receive normal security updates.
  - egress_local_vip is the private virtual IP configured on this server NIC.
  - share_count is the max simultaneous sessions for that username.
  - vpn_ip is optional and should usually stay empty. xl2tpd uses one global client pool, so per-user vpn_ip limits can reject users when they receive an address from another range.
  - VPN_VIPS values are added automatically and persisted by a systemd helper.
  - egress_local_vip must already exist on the server NIC, or be listed in VPN_VIPS/server.conf first.
  - public_eip_label is only printed for humans; Tianyi Cloud performs the public IP mapping.
  - With IPsec enabled, open UDP 500, UDP 4500, VPN_L2TP_PORT/udp, and ESP protocol 50 in the cloud security group.
  - With VPN_ENABLE_IPSEC=0, open VPN_L2TP_PORT/udp only. This is not encrypted by IPsec.
  - Many built-in OS L2TP clients assume UDP 1701 and may not support a custom L2TP port.
EOF
}

log() {
  printf '[l2tp] %s\n' "$*"
}

fail() {
  printf '[l2tp] ERROR: %s\n' "$*" >&2
  exit 1
}

random_hex() {
  od -An -N "$1" -tx1 /dev/urandom | tr -d ' \n'
}

shell_quote() {
  printf '%q' "$1"
}

tsv_field() {
  local line="$1"
  local field="$2"
  awk -F '\t' -v field="$field" '{print $field; exit}' <<<"$line"
}

is_interactive_install() {
  [ -t 0 ] && [ "${VPN_INTERACTIVE:-1}" != "0" ]
}

prompt_value() {
  local var="$1" label="$2" default_value="${3:-}" input
  printf '%s [%s]: ' "$label" "$default_value"
  IFS= read -r input || input=""
  if [ -z "$input" ]; then
    input="$default_value"
  fi
  printf -v "$var" '%s' "$input"
}

prompt_yes_no() {
  local var="$1" label="$2" default_value="${3:-n}" input normalized
  case "$default_value" in
    1|y|Y|yes|YES|true|TRUE) default_value="y" ;;
    *) default_value="n" ;;
  esac
  while true; do
    if [ "$default_value" = "y" ]; then
      printf '%s [Y/n]: ' "$label"
    else
      printf '%s [y/N]: ' "$label"
    fi
    IFS= read -r input || input=""
    input="${input:-$default_value}"
    normalized="$(printf '%s' "$input" | tr '[:upper:]' '[:lower:]')"
    case "$normalized" in
      y|yes|1|true) printf -v "$var" '%s' "1"; return 0 ;;
      n|no|0|false) printf -v "$var" '%s' "0"; return 0 ;;
      *) echo "请输入 y 或 n。" ;;
    esac
  done
}

prompt_interactive_config() {
  is_interactive_install || return 0

  echo
  echo "===== L2TP 安装参数确认 ====="
  echo "直接回车使用方括号中的默认值。"
  echo "主网卡 IP 已经在系统里；平台扫描到的额外虚拟 IP 会自动写入 VPN_VIPS。"
  echo

  prompt_value VPN_L2TP_PORT "服务端 UDP 端口" "$VPN_L2TP_PORT"
  prompt_value VPN_MTU "MTU" "$VPN_MTU"
  prompt_value VPN_MRU "MRU" "$VPN_MRU"
  prompt_value VPN_CIDR "VPN 客户端内网网段，例如 172.18.0.0/16" "$VPN_CIDR"
  prompt_value VPN_LOCAL_IP "L2TP 服务端隧道 IP" "$VPN_LOCAL_IP"
  prompt_value VPN_CLIENT_POOL "客户端地址池，例如 172.18.0.2-172.18.255.254" "$VPN_CLIENT_POOL"
  prompt_value VPN_DNS1 "客户端 DNS1" "$VPN_DNS1"
  prompt_value VPN_DNS2 "客户端 DNS2" "$VPN_DNS2"
  if [ "${VPN_PLATFORM_SCAN:-0}" = "1" ]; then
    prompt_value VPN_VIP_SCAN_RANGE "VIP 扫描范围（单个 IP、CIDR 或起止范围）" "${VPN_VIP_SCAN_RANGE:-${VPN_VIP_CANDIDATES:-}}"
    prompt_value VPN_VIP_SCAN_PARALLEL "VIP 批量扫描并发数" "$VPN_VIP_SCAN_PARALLEL"
  fi
  if [ -n "${VPN_VIPS:-}" ]; then
    echo "平台已扫描到额外虚拟内网 IP：$VPN_VIPS"
  else
    echo "平台未传入额外虚拟内网 IP，VPN_VIPS 留空。"
  fi

  echo "IPsec/PSK：默认关闭；如需启用，请在平台安装选项或环境变量中填写 VPN_IPSEC_PSK，或设置 VPN_RANDOM_PSK=1。"

  echo
}

load_server_config() {
  [ -f "$SERVER_CONFIG_FILE" ] || return 0
  # shellcheck disable=SC1090
  source "$SERVER_CONFIG_FILE"
}

write_env_overrides_file() {
  local file="$1" name value
  : >"$file"
  for name in "${SERVER_CONFIG_VARS[@]}"; do
    if printenv "$name" >/dev/null 2>&1; then
      value="$(printenv "$name")"
      printf '%s=%s\n' "$name" "$(shell_quote "$value")" >>"$file"
    fi
  done
}

persist_installer_script() {
  if [ -f "$0" ]; then
    local src
    src="$(readlink -f "$0" 2>/dev/null || printf '%s' "$0")"
    if [ "$src" != "$INSTALLER_PATH" ]; then
      install -m 700 "$0" "$INSTALLER_PATH" || true
    fi
  fi
}

escape_double_quotes() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

is_ipv4() {
  local a b c d octet
  IFS=. read -r a b c d <<<"${1:-}"
  for octet in "$a" "$b" "$c" "$d"; do
    [[ "$octet" =~ ^[0-9]+$ ]] || return 1
    [ "$octet" -ge 0 ] && [ "$octet" -le 255 ] || return 1
  done
}

validate_number_between() {
  local name="$1"
  local value="$2"
  local min="$3"
  local max="$4"
  [[ "$value" =~ ^[0-9]+$ ]] || fail "$name must be a number."
  [ "$value" -ge "$min" ] && [ "$value" -le "$max" ] || fail "$name must be between $min and $max."
}

ipv4_to_int() {
  local a b c d
  IFS=. read -r a b c d <<<"$1"
  printf '%u\n' "$(( (10#$a << 24) + (10#$b << 16) + (10#$c << 8) + 10#$d ))"
}

is_ipv4_cidr() {
  local ip prefix
  case "${1:-}" in
    */*) ;;
    *) return 1 ;;
  esac
  ip="${1%/*}"
  prefix="${1#*/}"
  is_ipv4 "$ip" || return 1
  [[ "$prefix" =~ ^[0-9]+$ ]] || return 1
  [ "$prefix" -ge 0 ] && [ "$prefix" -le 32 ]
}

is_ipv4_range() {
  local start_ip end_ip start_int end_int
  case "${1:-}" in
    *-*) ;;
    *) return 1 ;;
  esac
  start_ip="${1%-*}"
  end_ip="${1#*-}"
  is_ipv4 "$start_ip" || return 1
  is_ipv4 "$end_ip" || return 1
  start_int="$(ipv4_to_int "$start_ip")"
  end_int="$(ipv4_to_int "$end_ip")"
  [ "$end_int" -ge "$start_int" ]
}

int_to_ipv4() {
  local n="$1"
  printf '%u.%u.%u.%u\n' \
    "$(((n >> 24) & 255))" \
    "$(((n >> 16) & 255))" \
    "$(((n >> 8) & 255))" \
    "$((n & 255))"
}

cidr_bounds() {
  local cidr ip prefix ip_int total mask network broadcast start end
  cidr="$1"
  ip="${cidr%/*}"
  prefix="${cidr#*/}"
  ip_int="$(ipv4_to_int "$ip")"

  if [ "$prefix" -eq 0 ]; then
    mask=0
    total=$((1 << 32))
  else
    mask=$(( (0xffffffff << (32 - prefix)) & 0xffffffff ))
    total=$((1 << (32 - prefix)))
  fi

  network=$((ip_int & mask))
  broadcast=$((network + total - 1))
  if [ "$prefix" -le 30 ]; then
    start=$((network + 1))
    end=$((broadcast - 1))
  else
    start="$network"
    end="$broadcast"
  fi
  printf '%u %u\n' "$start" "$end"
}

client_ip_scope_capacity() {
  local spec bounds start end
  spec="$1"
  if [ "$spec" = "*" ] || [ -z "$spec" ]; then
    vpn_client_pool_capacity
    return 0
  fi
  if is_ipv4 "$spec"; then
    echo 1
    return 0
  fi
  if is_ipv4_cidr "$spec"; then
    bounds="$(cidr_bounds "$spec")"
    start="${bounds%% *}"
    end="${bounds#* }"
    printf '%u\n' "$((end - start + 1))"
    return 0
  fi
  if is_ipv4_range "$spec"; then
    start="$(ipv4_to_int "${spec%-*}")"
    end="$(ipv4_to_int "${spec#*-}")"
    printf '%u\n' "$((end - start + 1))"
    return 0
  fi
  fail "Invalid vpn_ip '$spec'. Use empty, a single IPv4 address, CIDR like 172.18.120.0/24, or a range like 172.18.1.61-172.18.1.80."
}

validate_client_scope_inside_pool() {
  local spec pool_start_ip pool_end_ip pool_start pool_end bounds start end
  spec="$1"
  [ -n "$spec" ] && [ "$spec" != "*" ] || return 0
  case "$VPN_CLIENT_POOL" in
    *-*) ;;
    *) return 0 ;;
  esac

  pool_start_ip="${VPN_CLIENT_POOL%-*}"
  pool_end_ip="${VPN_CLIENT_POOL#*-}"
  is_ipv4 "$pool_start_ip" || return 0
  is_ipv4 "$pool_end_ip" || return 0
  pool_start="$(ipv4_to_int "$pool_start_ip")"
  pool_end="$(ipv4_to_int "$pool_end_ip")"

  if is_ipv4 "$spec"; then
    start="$(ipv4_to_int "$spec")"
    end="$start"
  elif is_ipv4_cidr "$spec"; then
    bounds="$(cidr_bounds "$spec")"
    start="${bounds%% *}"
    end="${bounds#* }"
  elif is_ipv4_range "$spec"; then
    start="$(ipv4_to_int "${spec%-*}")"
    end="$(ipv4_to_int "${spec#*-}")"
  else
    return 0
  fi

  if [ "$start" -lt "$pool_start" ] || [ "$end" -gt "$pool_end" ]; then
    fail "vpn_ip '$spec' is outside VPN_CLIENT_POOL '$VPN_CLIENT_POOL'. Enlarge VPN_CLIENT_POOL or choose a subnet inside it."
  fi
}

chap_ip_value_for_scope() {
  local spec start end i out
  spec="$1"
  if [ -z "$spec" ] || [ "$spec" = "*" ]; then
    printf '*'
    return 0
  fi
  if is_ipv4 "$spec" || is_ipv4_cidr "$spec"; then
    printf '%s' "$spec"
    return 0
  fi
  if is_ipv4_range "$spec"; then
    start="$(ipv4_to_int "${spec%-*}")"
    end="$(ipv4_to_int "${spec#*-}")"
    if [ "$((end - start + 1))" -gt 1024 ]; then
      fail "vpn_ip range '$spec' is too large to expand for chap-secrets. Use CIDR instead."
    fi
    out=""
    for ((i=start; i<=end; i++)); do
      if [ -n "$out" ]; then
        out+=" "
      fi
      out+="$(int_to_ipv4 "$i")"
    done
    printf '%s' "$out"
    return 0
  fi
  fail "Invalid vpn_ip '$spec'."
}

user_config_hash() {
  printf '%s\t%s\t%s\t%s\t%s' "$1" "$2" "$3" "$4" "$6" | sha256sum | awk '{print $1}'
}

vpn_client_pool_capacity() {
  local start_ip end_ip start_int end_int
  case "$VPN_CLIENT_POOL" in
    *-*) ;;
    *) fail "VPN_CLIENT_POOL must be an IPv4 range like 172.18.0.2-172.18.255.254." ;;
  esac

  start_ip="${VPN_CLIENT_POOL%-*}"
  end_ip="${VPN_CLIENT_POOL#*-}"
  is_ipv4 "$start_ip" || fail "Invalid VPN_CLIENT_POOL start IP '$start_ip'."
  is_ipv4 "$end_ip" || fail "Invalid VPN_CLIENT_POOL end IP '$end_ip'."
  start_int="$(ipv4_to_int "$start_ip")"
  end_int="$(ipv4_to_int "$end_ip")"
  [ "$end_int" -ge "$start_int" ] || fail "VPN_CLIENT_POOL end IP must be greater than or equal to start IP."
  printf '%u\n' "$((end_int - start_int + 1))"
}

validate_vpn_network_layout() {
  local bounds cidr_start cidr_end local_int pool_start pool_end
  is_ipv4_cidr "$VPN_CIDR" || fail "VPN_CIDR must be an IPv4 CIDR such as 172.18.0.0/16."
  bounds="$(cidr_bounds "$VPN_CIDR")"
  cidr_start="${bounds%% *}"
  cidr_end="${bounds#* }"
  local_int="$(ipv4_to_int "$VPN_LOCAL_IP")"
  case "$VPN_CLIENT_POOL" in
    *-*) ;;
    *) fail "VPN_CLIENT_POOL must be an IPv4 range like 172.18.0.2-172.18.255.254." ;;
  esac
  is_ipv4 "${VPN_CLIENT_POOL%-*}" || fail "Invalid VPN_CLIENT_POOL start IP '${VPN_CLIENT_POOL%-*}'."
  is_ipv4 "${VPN_CLIENT_POOL#*-}" || fail "Invalid VPN_CLIENT_POOL end IP '${VPN_CLIENT_POOL#*-}'."
  pool_start="$(ipv4_to_int "${VPN_CLIENT_POOL%-*}")"
  pool_end="$(ipv4_to_int "${VPN_CLIENT_POOL#*-}")"
  [ "$pool_end" -ge "$pool_start" ] || fail "VPN_CLIENT_POOL end IP must be greater than or equal to start IP."
  if [ "$local_int" -lt "$cidr_start" ] || [ "$local_int" -gt "$cidr_end" ]; then
    fail "VPN_LOCAL_IP '$VPN_LOCAL_IP' is outside VPN_CIDR '$VPN_CIDR'."
  fi
  if [ "$pool_start" -lt "$cidr_start" ] || [ "$pool_end" -gt "$cidr_end" ]; then
    fail "VPN_CLIENT_POOL '$VPN_CLIENT_POOL' is outside VPN_CIDR '$VPN_CIDR'."
  fi
  if [ "$local_int" -ge "$pool_start" ] && [ "$local_int" -le "$pool_end" ]; then
    fail "VPN_LOCAL_IP '$VPN_LOCAL_IP' must not be included in VPN_CLIENT_POOL '$VPN_CLIENT_POOL'."
  fi
}

allocate_client_scope() {
  local __var="$1" count="$2" start_ip end_ip pool_start pool_end scope_start scope_end block_end host
  case "$VPN_CLIENT_POOL" in
    *-*) ;;
    *) fail "VPN_CLIENT_POOL must be an IPv4 range like 172.18.0.2-172.18.255.254." ;;
  esac

  start_ip="${VPN_CLIENT_POOL%-*}"
  end_ip="${VPN_CLIENT_POOL#*-}"
  is_ipv4 "$start_ip" || fail "Invalid VPN_CLIENT_POOL start IP '$start_ip'."
  is_ipv4 "$end_ip" || fail "Invalid VPN_CLIENT_POOL end IP '$end_ip'."
  pool_start="$(ipv4_to_int "$start_ip")"
  pool_end="$(ipv4_to_int "$end_ip")"
  [ -n "${AUTO_CLIENT_NEXT:-}" ] || AUTO_CLIENT_NEXT="$pool_start"
  scope_start="$AUTO_CLIENT_NEXT"
  while :; do
    [ "$scope_start" -le "$pool_end" ] || fail "VPN_CLIENT_POOL does not have enough free addresses to auto-assign $count client IP(s)."
    host="$((scope_start & 255))"
    if [ "$host" -eq 0 ]; then
      scope_start="$((scope_start + 1))"
      continue
    fi
    if [ "$host" -eq 255 ]; then
      scope_start="$(((scope_start & 0xffffff00) + 257))"
      continue
    fi
    block_end="$(((scope_start & 0xffffff00) + 254))"
    scope_end="$((scope_start + count - 1))"
    if [ "$scope_end" -le "$block_end" ] && [ "$scope_end" -le "$pool_end" ]; then
      break
    fi
    scope_start="$(((scope_start & 0xffffff00) + 257))"
  done
  if [ "$scope_start" -eq "$scope_end" ]; then
    printf -v "$__var" '%s' "$(int_to_ipv4 "$scope_start")"
  else
    printf -v "$__var" '%s-%s' "$(int_to_ipv4 "$scope_start")" "$(int_to_ipv4 "$scope_end")"
  fi
  AUTO_CLIENT_NEXT="$((scope_end + 1))"
}

resolve_ipsec_settings() {
  local enable_was_set=0
  if [ -n "${VPN_ENABLE_IPSEC+x}" ]; then
    enable_was_set=1
  fi

  VPN_ENABLE_IPSEC="${VPN_ENABLE_IPSEC:-0}"
  VPN_RANDOM_PSK="${VPN_RANDOM_PSK:-0}"

  case "${VPN_IPSEC_PSK:-}" in
    none|NONE|off|OFF|no|NO|disabled|DISABLED)
      VPN_ENABLE_IPSEC=0
      VPN_IPSEC_PSK=""
      ;;
  esac

  case "$VPN_ENABLE_IPSEC" in
    0|1) ;;
    *) fail "VPN_ENABLE_IPSEC must be 0 or 1." ;;
  esac

  case "$VPN_RANDOM_PSK" in
    0|1) ;;
    *) fail "VPN_RANDOM_PSK must be 0 or 1." ;;
  esac

  if [ "$enable_was_set" = "0" ]; then
    if [ -n "${VPN_IPSEC_PSK:-}" ] || [ "$VPN_RANDOM_PSK" = "1" ]; then
      VPN_ENABLE_IPSEC=1
    fi
  fi

  if [ "$VPN_ENABLE_IPSEC" = "0" ]; then
    VPN_IPSEC_PSK=""
    return 0
  fi

  if [ -z "${VPN_IPSEC_PSK:-}" ]; then
    if [ "$VPN_RANDOM_PSK" = "1" ]; then
      VPN_IPSEC_PSK="$(random_hex 24)"
    else
      fail "VPN_ENABLE_IPSEC=1 requires VPN_IPSEC_PSK or VPN_RANDOM_PSK=1."
    fi
  fi
}

need_root() {
  if [ "$(id -u)" -ne 0 ]; then
    fail "Run as root, for example: curl -fsSL URL | sudo bash"
  fi
}

need_systemd() {
  command -v systemctl >/dev/null 2>&1 || fail "systemd is required."
}

detect_iface() {
  local iface
  iface="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i=1;i<=NF;i++) if ($i=="dev") {print $(i+1); exit}}')"
  if [ -z "$iface" ]; then
    iface="$(ip -4 route list default 2>/dev/null | awk '{for (i=1;i<=NF;i++) if ($i=="dev") {print $(i+1); exit}}')"
  fi
  [ -n "$iface" ] || fail "Could not auto-detect the default network interface. Set VPN_IFACE=eth0."
  printf '%s\n' "$iface"
}

detect_iface_ipv4s() {
  local iface="$1" requested_ip="${2:-}" route_line route_iface route_ip cidr
  ip link show dev "$iface" >/dev/null 2>&1 || fail "VPN_IFACE '$iface' does not exist."

  if [ -n "$requested_ip" ]; then
    requested_ip="${requested_ip%%/*}"
    is_ipv4 "$requested_ip" || fail "Invalid VPN_PRIMARY_IP '$requested_ip'."
    cidr="$(ip -o -4 addr show dev "$iface" scope global 2>/dev/null |
      awk -v target="$requested_ip" '$4 ~ ("^" target "/") && $0 !~ / tentative| dadfailed/ {print $4; exit}')"
    [ -n "$cidr" ] || fail "VPN_PRIMARY_IP '$requested_ip' is not a usable local IPv4 on $iface."
    printf '%s\n' "$cidr"
    return 0
  fi

  route_line="$(ip -4 route get 1.1.1.1 2>/dev/null | head -n 1 || true)"
  route_iface="$(awk '{for (i=1;i<=NF;i++) if ($i=="dev") {print $(i+1); exit}}' <<<"$route_line")"
  route_ip="$(awk '{for (i=1;i<=NF;i++) if ($i=="src") {print $(i+1); exit}}' <<<"$route_line")"
  if [ "$route_iface" = "$iface" ] && is_ipv4 "$route_ip"; then
    cidr="$(ip -o -4 addr show dev "$iface" scope global 2>/dev/null |
      awk -v target="$route_ip" '$4 ~ ("^" target "/") && $0 !~ / tentative| dadfailed/ {print $4; exit}')"
  fi

  if [ -z "${cidr:-}" ]; then
    cidr="$(ip -o -4 addr show dev "$iface" scope global 2>/dev/null |
      awk '$0 !~ / secondary| tentative| dadfailed| deprecated/ {print $4; exit}')"
  fi
  if [ -z "${cidr:-}" ]; then
    cidr="$(ip -o -4 addr show dev "$iface" scope global 2>/dev/null |
      awk '$0 !~ / tentative| dadfailed| deprecated/ {print $4; exit}')"
  fi
  [ -n "${cidr:-}" ] || fail "No usable global IPv4 was found on $iface. Set VPN_IFACE and VPN_PRIMARY_IP explicitly."
  printf '%s\n' "$cidr"
}

expand_vip_scan_specs() {
  local raw="$1" max_count="$2" spec bounds start end current count=0 output=""
  local -a scan_specs=()
  IFS=',' read -r -a scan_specs <<<"$raw"
  for spec in "${scan_specs[@]}"; do
    spec="$(printf '%s' "$spec" | xargs)"
    [ -n "$spec" ] || continue
    if is_ipv4 "$spec"; then
      start="$(ipv4_to_int "$spec")"
      end="$start"
    elif is_ipv4_range "$spec"; then
      start="$(ipv4_to_int "${spec%-*}")"
      end="$(ipv4_to_int "${spec#*-}")"
    elif is_ipv4_cidr "$spec"; then
      bounds="$(cidr_bounds "$spec")"
      start="${bounds%% *}"
      end="${bounds#* }"
    else
      fail "Invalid VPN_VIP_SCAN_RANGE item '$spec'. Use an IPv4, CIDR, or start-end range."
    fi
    [ "$((end - start + 1))" -le "$((max_count - count))" ] || \
      fail "VIP scan scope expands beyond VPN_VIP_SCAN_MAX=$max_count addresses. Narrow it or explicitly raise the limit."
    for ((current=start; current<=end; current++)); do
      output="${output:+$output,}$(int_to_ipv4 "$current")"
      count=$((count + 1))
    done
  done
  printf '%s\n' "$output"
}

verify_platform_vip_candidates() {
  [ "${VPN_PLATFORM_SCAN:-0}" = "1" ] || return 0
  local raw item candidate probe_target verified="" temp_dir offset index batch_end
  local -a candidate_parts=() scan_ips=() existed_flags=()
  probe_target="${VPN_VIP_PROBE_TARGET:-www.baidu.com}"
  raw="${VPN_VIP_SCAN_RANGE:-${VPN_VIP_CANDIDATES:-}}"

  # A platform scan is authoritative, including when it finds no usable VIPs.
  VPN_VIPS=""
  [ -n "$raw" ] || { log "Platform VIP scan found no candidates."; return 0; }
  raw="$(expand_vip_scan_specs "$raw" "$VPN_VIP_SCAN_MAX")"

  IFS=',' read -r -a candidate_parts <<<"$raw"
  for item in "${candidate_parts[@]}"; do
    candidate="${item%%/*}"
    [ -n "$candidate" ] || continue
    if [ "$candidate" = "${VPN_IFACE_IPV4S%%/*}" ]; then
      log "Skipping primary interface address $candidate; it is not a VIP candidate."
      continue
    fi
    if printf '%s\n' "${scan_ips[*]:-}" | grep -Fwq "$candidate"; then
      continue
    fi
    if ip -o -4 addr show dev "$VPN_IFACE" | awk '{print $4}' | cut -d/ -f1 | grep -Fxq "$candidate"; then
      scan_ips+=("$candidate")
      existed_flags+=("1")
    elif ip addr add "$candidate/32" dev "$VPN_IFACE"; then
      scan_ips+=("$candidate")
      existed_flags+=("0")
    else
      warn "Could not temporarily add VIP candidate $candidate to $VPN_IFACE."
    fi
  done

  [ "${#scan_ips[@]}" -gt 0 ] || { log "No usable VIP candidates remained after validation."; return 0; }
  temp_dir="$(mktemp -d)"
  trap 'rm -rf -- "$temp_dir"' RETURN
  sleep 1
  log "Batch-verifying ${#scan_ips[@]} VIP candidate(s), concurrency=$VPN_VIP_SCAN_PARALLEL, target=$probe_target."
  for ((offset=0; offset<${#scan_ips[@]}; offset+=VPN_VIP_SCAN_PARALLEL)); do
    batch_end=$((offset + VPN_VIP_SCAN_PARALLEL))
    [ "$batch_end" -le "${#scan_ips[@]}" ] || batch_end="${#scan_ips[@]}"
    for ((index=offset; index<batch_end; index++)); do
      candidate="${scan_ips[$index]}"
      (ping -4 -I "$candidate" -c 2 -W 2 "$probe_target" >/dev/null 2>&1 && : >"$temp_dir/$index.ok") &
    done
    wait || true
  done

  for ((index=0; index<${#scan_ips[@]}; index++)); do
    candidate="${scan_ips[$index]}"
    if [ -f "$temp_dir/$index.ok" ]; then
      verified="${verified:+$verified,}$candidate/32"
      log "Verified platform VIP $candidate on $VPN_IFACE via $probe_target."
    else
      warn "VIP candidate $candidate failed the source-address ping to $probe_target."
      if [ "${existed_flags[$index]}" = "0" ]; then
        ip addr del "$candidate/32" dev "$VPN_IFACE" || warn "Could not remove failed temporary VIP $candidate."
      fi
    fi
  done
  rm -rf -- "$temp_dir"
  trap - RETURN
  VPN_VIPS="$verified"
  log "Active platform VIP verification result: ${VPN_VIPS:-none}."
}

detect_system() {
  [ -r /etc/os-release ] || fail "/etc/os-release was not found; cannot identify this Linux distribution."
  # shellcheck disable=SC1091
  . /etc/os-release
  OS_ID="$(printf '%s' "${ID:-}" | tr '[:upper:]' '[:lower:]')"
  OS_ID_LIKE="$(printf '%s' "${ID_LIKE:-}" | tr '[:upper:]' '[:lower:]')"
  OS_VERSION_ID="${VERSION_ID:-}"
  OS_CODENAME="${VERSION_CODENAME:-${UBUNTU_CODENAME:-}}"
  OS_ARCH="$(uname -m)"

  case "$OS_ID" in
    debian|ubuntu) OS_FAMILY="apt" ;;
    centos) OS_FAMILY="rhel" ;;
    *)
      case " $OS_ID_LIKE " in
        *" debian "*) OS_FAMILY="apt" ;;
        *" centos "*) OS_FAMILY="rhel" ;;
        *) fail "Unsupported Linux distribution '$OS_ID'. Supported: Debian, Ubuntu, and CentOS 7/8/Stream 9." ;;
      esac
      ;;
  esac

  case "$OS_ID" in
    debian)
      [ "${OS_VERSION_ID%%.*}" -ge 9 ] 2>/dev/null || fail "Debian 9 or newer is required."
      ;;
    ubuntu)
      [ "${OS_VERSION_ID%%.*}" -ge 18 ] 2>/dev/null || fail "Ubuntu 18.04 or newer is required."
      ;;
    centos)
      case "${OS_VERSION_ID%%.*}" in 7|8|9) ;; *) fail "Supported CentOS versions: 7, 8, and Stream 9." ;; esac
      ;;
  esac
  log "Detected system: ${PRETTY_NAME:-$OS_ID $OS_VERSION_ID} ($OS_ARCH)."
}

apt_get() {
  if [ "${VPN_APT_FORCE_IPV4:-1}" = "1" ]; then
    apt-get -o Acquire::ForceIPv4=true -o Acquire::http::Pipeline-Depth=0 "$@"
  else
    apt-get "$@"
  fi
}

configure_apt_tuna_mirror() {
  local mirror="$VPN_TUNA_MIRROR" codename="$OS_CODENAME" components archive stamp source_file
  [ -n "$codename" ] || fail "Could not determine the distribution codename from /etc/os-release."
  stamp="$(date +%Y%m%d%H%M%S)"
  install -d -m 755 /etc/apt/sources.list.d /etc/apt/apt.conf.d

  for source_file in /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list.d/ubuntu.sources; do
    [ -e "$source_file" ] || continue
    cp -a "$source_file" "${source_file}.ctyun-backup.$stamp"
  done
  printf '# Managed by ctyun L2TP installer. Original file has a .ctyun-backup.%s copy.\n' "$stamp" >/etc/apt/sources.list
  for source_file in /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list.d/ubuntu.sources; do
    [ -e "$source_file" ] || continue
    mv "$source_file" "${source_file}.ctyun-disabled.$stamp"
  done

  case "$OS_ID" in
    ubuntu)
      components="main restricted universe multiverse"
      case "$OS_ARCH" in
        x86_64|i386|i486|i586|i686) archive="ubuntu" ;;
        *) archive="ubuntu-ports" ;;
      esac
      cat >/etc/apt/sources.list.d/ctyun-tuna.sources <<EOF
Types: deb deb-src
URIs: $mirror/$archive
Suites: $codename ${codename}-updates ${codename}-backports ${codename}-security
Components: $components
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
EOF
      ;;
    debian)
      components="main contrib non-free"
      if [ "${OS_VERSION_ID%%.*}" -ge 12 ] 2>/dev/null; then
        components="$components non-free-firmware"
      fi
      if [ "${OS_VERSION_ID%%.*}" -le 10 ] 2>/dev/null; then
        cat >/etc/apt/sources.list.d/ctyun-tuna.sources <<EOF
Types: deb deb-src
URIs: http://archive.debian.org/debian
Suites: $codename ${codename}-backports
Components: $components
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
Check-Valid-Until: no

Types: deb deb-src
URIs: http://archive.debian.org/debian-security
Suites: ${codename}/updates
Components: $components
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
Check-Valid-Until: no
EOF
        log "WARNING: Debian $OS_VERSION_ID is EOL and no longer exists on TUNA; using Debian's HTTP archive. It receives no security updates."
      else
        cat >/etc/apt/sources.list.d/ctyun-tuna.sources <<EOF
Types: deb deb-src
URIs: $mirror/debian
Suites: $codename ${codename}-updates ${codename}-backports
Components: $components
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg

Types: deb deb-src
URIs: $mirror/debian-security
Suites: ${codename}-security
Components: $components
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
EOF
      fi
      ;;
    *) fail "TUNA apt source generation is supported only for Debian and Ubuntu, not '$OS_ID'." ;;
  esac

  if [ "${VPN_APT_FORCE_IPV4:-1}" = "1" ]; then
    cat >/etc/apt/apt.conf.d/99ctyun-tuna-ipv4 <<'EOF'
Acquire::ForceIPv4 "true";
Acquire::http::Pipeline-Depth "0";
EOF
  fi
  if [ "$OS_ID" = "debian" ] && [ "${OS_VERSION_ID%%.*}" -le 10 ] 2>/dev/null; then
    log "Configured Debian HTTP archive with binary/source/non-free repositories."
  else
    log "Configured TUNA apt mirror ($mirror, HTTP, IPv4, source repositories, non-free components, mirrored security updates)."
  fi
}

enable_yum_source_repos() {
  local file tmp
  for file in /etc/yum.repos.d/*.repo; do
    [ -f "$file" ] || continue
    tmp="$(mktemp)"
    awk '
      /^\[/ { source = (tolower($0) ~ /source/) }
      source && /^enabled[[:space:]]*=/ { print "enabled=1"; next }
      { print }
    ' "$file" >"$tmp"
    cat "$tmp" >"$file"
    rm -f "$tmp"
  done
}

disable_centos_repo_files() {
  local stamp="$1" repo_file
  for repo_file in /etc/yum.repos.d/CentOS-*.repo /etc/yum.repos.d/centos*.repo; do
    [ -f "$repo_file" ] || continue
    mv "$repo_file" "${repo_file}.ctyun-disabled.$stamp"
  done
}

write_centos_vault_repos() {
  local major="$1" mirror="$2" release_path gpg_key
  case "$major" in
    7)
      release_path="7.9.2009"
      gpg_key="file:///etc/pki/rpm-gpg/RPM-GPG-KEY-CentOS-7"
      cat >/etc/yum.repos.d/ctyun-tuna-centos-vault.repo <<EOF
[base]
name=CentOS 7 - Base - TUNA Vault
baseurl=$mirror/centos-vault/$release_path/os/\$basearch/
enabled=1
gpgcheck=1
gpgkey=$gpg_key

[base-source]
name=CentOS 7 - Base Source - TUNA Vault
baseurl=$mirror/centos-vault/$release_path/os/Source/
enabled=1
gpgcheck=1
gpgkey=$gpg_key

[updates]
name=CentOS 7 - Updates - TUNA Vault
baseurl=$mirror/centos-vault/$release_path/updates/\$basearch/
enabled=1
gpgcheck=1
gpgkey=$gpg_key

[updates-source]
name=CentOS 7 - Updates Source - TUNA Vault
baseurl=$mirror/centos-vault/$release_path/updates/Source/
enabled=1
gpgcheck=1
gpgkey=$gpg_key

[extras]
name=CentOS 7 - Extras - TUNA Vault
baseurl=$mirror/centos-vault/$release_path/extras/\$basearch/
enabled=1
gpgcheck=1
gpgkey=$gpg_key

[extras-source]
name=CentOS 7 - Extras Source - TUNA Vault
baseurl=$mirror/centos-vault/$release_path/extras/Source/
enabled=1
gpgcheck=1
gpgkey=$gpg_key
EOF
      ;;
    8)
      release_path="8.5.2111"
      gpg_key="file:///etc/pki/rpm-gpg/RPM-GPG-KEY-centosofficial"
      cat >/etc/yum.repos.d/ctyun-tuna-centos-vault.repo <<EOF
[baseos]
name=CentOS 8 - BaseOS - TUNA Vault
baseurl=$mirror/centos-vault/$release_path/BaseOS/\$basearch/os/
enabled=1
gpgcheck=1
gpgkey=$gpg_key

[baseos-source]
name=CentOS 8 - BaseOS Source - TUNA Vault
baseurl=$mirror/centos-vault/$release_path/BaseOS/Source/
enabled=1
gpgcheck=1
gpgkey=$gpg_key

[appstream]
name=CentOS 8 - AppStream - TUNA Vault
baseurl=$mirror/centos-vault/$release_path/AppStream/\$basearch/os/
enabled=1
gpgcheck=1
gpgkey=$gpg_key

[appstream-source]
name=CentOS 8 - AppStream Source - TUNA Vault
baseurl=$mirror/centos-vault/$release_path/AppStream/Source/
enabled=1
gpgcheck=1
gpgkey=$gpg_key

[powertools]
name=CentOS 8 - PowerTools - TUNA Vault
baseurl=$mirror/centos-vault/$release_path/PowerTools/\$basearch/os/
enabled=1
gpgcheck=1
gpgkey=$gpg_key

[powertools-source]
name=CentOS 8 - PowerTools Source - TUNA Vault
baseurl=$mirror/centos-vault/$release_path/PowerTools/Source/
enabled=1
gpgcheck=1
gpgkey=$gpg_key

[extras]
name=CentOS 8 - Extras - TUNA Vault
baseurl=$mirror/centos-vault/$release_path/extras/\$basearch/os/
enabled=1
gpgcheck=1
gpgkey=$gpg_key
EOF
      ;;
  esac
  log "WARNING: CentOS $major is EOL. TUNA Vault is static and receives no security updates."
}

configure_rhel_tuna_mirror() {
  local mirror="$VPN_TUNA_MIRROR" stamp repo_file major
  stamp="$(date +%Y%m%d%H%M%S)"
  major="${OS_VERSION_ID%%.*}"
  [ -d /etc/yum.repos.d ] || fail "/etc/yum.repos.d was not found."
  cp -a /etc/yum.repos.d "/etc/yum.repos.d.ctyun-backup.$stamp"

  case "$OS_ID" in
    rocky)
      for repo_file in /etc/yum.repos.d/*.repo; do
        [ -f "$repo_file" ] || continue
        sed -Ei \
          -e 's|^([[:space:]]*)mirrorlist=|\1#mirrorlist=|' \
          -e 's|^([[:space:]]*)metalink=|\1#metalink=|' \
          -e 's|^([[:space:]]*)#baseurl=|\1baseurl=|' \
          -e "s|https?://(dl|download)\\.rockylinux\\.org/(pub/)?rocky|$mirror/rocky|g" \
          "$repo_file"
      done
      ;;
    almalinux)
      for repo_file in /etc/yum.repos.d/*.repo; do
        [ -f "$repo_file" ] || continue
        sed -Ei \
          -e 's|^([[:space:]]*)mirrorlist=|\1#mirrorlist=|' \
          -e 's|^([[:space:]]*)metalink=|\1#metalink=|' \
          -e 's|^([[:space:]]*)#baseurl=|\1baseurl=|' \
          -e "s|https?://repo\\.almalinux\\.org/almalinux|$mirror/almalinux|g" \
          "$repo_file"
      done
      ;;
    centos)
      disable_centos_repo_files "$stamp"
      if [ "$major" = "7" ] || [ "$major" = "8" ]; then
        write_centos_vault_repos "$major" "$mirror"
      else
        [ "$major" = "9" ] || fail "Supported CentOS versions: 7, 8, and Stream 9."
      cat >/etc/yum.repos.d/ctyun-tuna-centos-stream.repo <<EOF
[baseos]
name=CentOS Stream 9 - BaseOS - TUNA
baseurl=$mirror/centos-stream/9-stream/BaseOS/\$basearch/os
enabled=1
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-centosofficial

[baseos-source]
name=CentOS Stream 9 - BaseOS Source - TUNA
baseurl=$mirror/centos-stream/9-stream/BaseOS/source/tree/
enabled=1
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-centosofficial

[appstream]
name=CentOS Stream 9 - AppStream - TUNA
baseurl=$mirror/centos-stream/9-stream/AppStream/\$basearch/os
enabled=1
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-centosofficial

[appstream-source]
name=CentOS Stream 9 - AppStream Source - TUNA
baseurl=$mirror/centos-stream/9-stream/AppStream/source/tree/
enabled=1
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-centosofficial

[crb]
name=CentOS Stream 9 - CRB - TUNA
baseurl=$mirror/centos-stream/9-stream/CRB/\$basearch/os
enabled=1
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-centosofficial

[crb-source]
name=CentOS Stream 9 - CRB Source - TUNA
baseurl=$mirror/centos-stream/9-stream/CRB/source/tree/
enabled=1
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-centosofficial

[extras-common]
name=CentOS Stream 9 - Extras - TUNA
baseurl=$mirror/centos-stream/SIGs/9-stream/extras/\$basearch/extras-common
enabled=1
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-CentOS-SIG-Extras-SHA512
EOF
      fi
      ;;
    rhel)
      fail "RHEL base repositories require a Red Hat subscription and are not mirrored by TUNA. Set VPN_CONFIGURE_TUNA_MIRROR=0 to keep registered RHEL repositories."
      ;;
    *) fail "TUNA yum source generation is not implemented for '$OS_ID'." ;;
  esac
  enable_yum_source_repos
  log "Configured TUNA repositories for $OS_ID $OS_VERSION_ID ($mirror, HTTP, source repositories enabled)."
}

configure_package_sources() {
  VPN_CONFIGURE_TUNA_MIRROR="${VPN_CONFIGURE_TUNA_MIRROR:-1}"
  VPN_TUNA_MIRROR="${VPN_TUNA_MIRROR:-http://mirrors.tuna.tsinghua.edu.cn}"
  VPN_APT_FORCE_IPV4="${VPN_APT_FORCE_IPV4:-1}"
  case "$VPN_CONFIGURE_TUNA_MIRROR" in 0|1) ;; *) fail "VPN_CONFIGURE_TUNA_MIRROR must be 0 or 1." ;; esac
  case "$VPN_APT_FORCE_IPV4" in 0|1) ;; *) fail "VPN_APT_FORCE_IPV4 must be 0 or 1." ;; esac
  if [ "$VPN_CONFIGURE_TUNA_MIRROR" = "0" ]; then
    log "Keeping existing package repositories."
    return 0
  fi
  case "$OS_FAMILY" in
    apt) configure_apt_tuna_mirror ;;
    rhel) configure_rhel_tuna_mirror ;;
  esac
}

install_debian_packages() {
  export DEBIAN_FRONTEND=noninteractive
  apt_get update
  local packages=(ca-certificates curl iproute2 iptables openssl ppp sed xl2tpd)
  if [ "$VPN_ENABLE_IPSEC" = "1" ]; then
    packages+=(strongswan)
  fi
  apt_get install -y --no-install-recommends "${packages[@]}"
}

enable_epel_if_needed() {
  local pm="$1"
  if rpm -q epel-release >/dev/null 2>&1; then
    return 0
  fi

  "$pm" install -y epel-release && return 0

  local major
  major="${OS_VERSION_ID%%.*}"
  [ -n "$major" ] || fail "Could not determine EL major version for EPEL."
  if [ "$OS_ID" = "centos" ] && [ "$major" = "7" ]; then
    "$pm" install -y "http://archives.fedoraproject.org/pub/archive/epel/7/x86_64/Packages/e/epel-release-7-14.noarch.rpm"
    return 0
  fi
  "$pm" install -y "$VPN_TUNA_MIRROR/epel/epel-release-latest-${major}.noarch.rpm"
}

configure_epel_tuna_mirror() {
  local repo_file stamp
  [ "${VPN_CONFIGURE_TUNA_MIRROR:-1}" = "1" ] || return 0
  if [ "$OS_ID" = "centos" ] && [ "${OS_VERSION_ID%%.*}" = "7" ]; then
    stamp="$(date +%Y%m%d%H%M%S)"
    for repo_file in /etc/yum.repos.d/epel*.repo; do
      [ -f "$repo_file" ] || continue
      mv "$repo_file" "${repo_file}.ctyun-disabled.$stamp"
    done
    cat >/etc/yum.repos.d/ctyun-epel-archive.repo <<'EOF'
[epel]
name=EPEL 7 Archive
baseurl=http://archives.fedoraproject.org/pub/archive/epel/7/$basearch/
enabled=1
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-EPEL-7

[epel-source]
name=EPEL 7 Source Archive
baseurl=http://archives.fedoraproject.org/pub/archive/epel/7/SRPMS/
enabled=1
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-EPEL-7
EOF
    log "WARNING: TUNA no longer carries EPEL 7; using Fedora's HTTP EPEL archive. It receives no security updates."
    return 0
  fi
  if [ -f /etc/yum.repos.d/epel-cisco-openh264.repo ]; then
    sed -Ei 's/^enabled[[:space:]]*=.*/enabled=0/' /etc/yum.repos.d/epel-cisco-openh264.repo
  fi
  for repo_file in /etc/yum.repos.d/epel*.repo; do
    [ "$(basename "$repo_file")" = "epel-cisco-openh264.repo" ] && continue
    [ -f "$repo_file" ] || continue
    sed -Ei \
      -e 's|^([[:space:]]*)metalink=|\1#metalink=|' \
      -e 's|^([[:space:]]*)#baseurl=|\1baseurl=|' \
      -e "s|https?://download\\.fedoraproject\\.org/pub/epel|$VPN_TUNA_MIRROR/epel|g" \
      -e "s|https?://download\\.example/pub/epel|$VPN_TUNA_MIRROR/epel|g" \
      "$repo_file"
  done
  enable_yum_source_repos
}

enable_rhel_optional_repos() {
  if ! command -v dnf >/dev/null 2>&1; then
    return 0
  fi

  dnf install -y dnf-plugins-core >/dev/null 2>&1 || true
  dnf config-manager --set-enabled crb >/dev/null 2>&1 || true
  dnf config-manager --set-enabled powertools >/dev/null 2>&1 || true
  dnf config-manager --set-enabled codeready-builder-for-rhel-*-rpms >/dev/null 2>&1 || true
}

install_rhel_packages() {
  local pm
  pm="$(command -v dnf || command -v yum || true)"
  [ -n "$pm" ] || fail "dnf/yum was not found."

  "$pm" clean all >/dev/null 2>&1 || true
  "$pm" makecache -y
  "$pm" install -y ca-certificates curl iproute iptables openssl sed
  enable_epel_if_needed "$pm"
  configure_epel_tuna_mirror
  "$pm" clean all >/dev/null 2>&1 || true
  "$pm" makecache -y
  enable_rhel_optional_repos
  local packages=(xl2tpd ppp iptables-services)
  if [ "$VPN_ENABLE_IPSEC" = "1" ]; then
    packages+=(strongswan)
  fi
  "$pm" install -y "${packages[@]}"
}

install_packages() {
  detect_system
  configure_package_sources

  if [ "$OS_FAMILY" = "apt" ] && command -v apt-get >/dev/null 2>&1; then
    log "Installing packages with apt..."
    install_debian_packages
    return 0
  fi

  if [ "$OS_FAMILY" = "rhel" ] && { command -v dnf >/dev/null 2>&1 || command -v yum >/dev/null 2>&1; }; then
    log "Installing packages with dnf/yum..."
    install_rhel_packages
    return 0
  fi

  fail "The expected package manager for $OS_ID was not found."
}

backup_file() {
  local path="$1"
  if [ -e "$path" ]; then
    cp -a "$path" "${path}.bak.$(date +%Y%m%d%H%M%S)"
  fi
}

read_user_config_file() {
  local file="${1:-$USER_CONFIG_FILE}"
  local line cleaned entries
  entries=()
  [ -f "$file" ] || return 1
  while IFS= read -r line || [ -n "$line" ]; do
    cleaned="${line%%#*}"
    cleaned="$(printf '%s' "$cleaned" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
    [ -n "$cleaned" ] || continue
    entries+=("$cleaned")
  done <"$file"
  [ "${#entries[@]}" -gt 0 ] || return 1
  printf '%s\n' "${entries[@]}"
}

write_user_editable_config() {
  if [ -f "$USER_CONFIG_FILE" ]; then
    return 0
  fi

  log "Writing editable user config: $USER_CONFIG_FILE"
  install -d -m 700 "$CONFIG_DIR"
  {
    echo "# L2TP VPN 用户配置"
    echo "# 每行一个账号，使用英文逗号分隔。以 # 开头的行会被忽略。"
    echo "#"
    echo "# 字段格式："
    echo "# 账号,密码,出口虚拟内网IP,共享连接数,客户端内网IP或IP段,公网IP备注"
    echo "#"
    echo "# 字段说明："
    echo "# 账号：VPN 登录用户名，只能使用字母、数字、下划线、点、@、中横线。"
    echo "# 密码：VPN 登录密码。"
    echo "# 出口虚拟内网IP：可留空；填写后，该账号的流量会用这个服务器本地 VIP 做出口 SNAT。"
    echo "# 共享连接数：同一账号允许同时在线的数量，默认 1。"
    echo "# 客户端内网IP或IP段：建议留空；留空时由 xl2tpd 从全局客户端地址池自动分配。"
    echo "# 只有客户端能主动指定固定地址，或你明确要限制某个账号可用地址时，才填写单个 IP、CIDR 网段或 IP 范围。"
    echo "# 注意：xl2tpd 在知道账号名前先分配客户端 IP，所以留空时不会按账号自动预留不重叠网段。"
    echo "# 公网IP备注：只用于人工识别，例如对应的 EIP，不参与系统配置。"
    echo "#"
    echo "# 示例："
    echo "# DF31,112233..,192.168.0.101,254,,主网卡公网IP"
    echo "# DF32,112233..,192.168.0.102,20,,虚拟IP对应公网IP"
    echo "# DF33,112233..,192.168.0.103,20,,虚拟IP对应公网IP"
    echo "# 上面三行会共用 VPN_CLIENT_POOL 客户端地址池；每个账号的出口由第 3 列内网 IP 决定。"
    echo
    local i
    for i in "${!USER_NAMES[@]}"; do
      printf '%s,%s,%s,%s,%s,%s\n' \
        "${USER_NAMES[$i]}" \
        "${USER_PASSWORDS[$i]}" \
        "${USER_EGRESS_IPS[$i]:-}" \
        "${USER_SHARE_COUNTS[$i]}" \
        "$(if [ "${USER_CLIENT_IPS[$i]}" = "*" ]; then printf ''; else printf '%s' "${USER_CLIENT_IPS[$i]}"; fi)" \
        "${USER_PUBLIC_LABELS[$i]:-}"
    done
  } >"$USER_CONFIG_FILE"
  chmod 600 "$USER_CONFIG_FILE"
}

write_server_config() {
  install -d -m 700 "$CONFIG_DIR"
  local tmp
  tmp="$(mktemp)"
  {
    echo "# L2TP VPN server config"
    echo "# Edit this file, then run: $APPLY_CONFIG_SCRIPT"
    echo
    printf 'VPN_LOCAL_IP=%s\n' "$(shell_quote "$VPN_LOCAL_IP")"
    printf 'VPN_CLIENT_POOL=%s\n' "$(shell_quote "$VPN_CLIENT_POOL")"
    printf 'VPN_CIDR=%s\n' "$(shell_quote "$VPN_CIDR")"
    printf 'VPN_L2TP_PORT=%s\n' "$(shell_quote "$VPN_L2TP_PORT")"
    printf 'VPN_DNS1=%s\n' "$(shell_quote "$VPN_DNS1")"
    printf 'VPN_DNS2=%s\n' "$(shell_quote "$VPN_DNS2")"
    printf 'VPN_MTU=%s\n' "$(shell_quote "$VPN_MTU")"
    printf 'VPN_MRU=%s\n' "$(shell_quote "$VPN_MRU")"
    printf 'VPN_DEFAULT_SHARE_COUNT=%s\n' "$(shell_quote "${VPN_DEFAULT_SHARE_COUNT:-1}")"
    echo "# 兼容旧配置项：默认不再为空账号自动预留客户端段，留空表示使用全局客户端地址池。"
    printf 'VPN_AUTO_CLIENT_SCOPE=%s\n' "$(shell_quote "${VPN_AUTO_CLIENT_SCOPE:-0}")"
    echo
    echo "# 默认出网网卡，以及这张网卡当前已经存在的 IPv4。"
    echo "# 主网卡 IP 已经在系统里，不需要写入 VPN_VIPS。"
    printf 'VPN_IFACE=%s\n' "$(shell_quote "$VPN_IFACE")"
    printf 'VPN_PRIMARY_IP=%s\n' "$(shell_quote "${VPN_PRIMARY_IP:-}")"
    printf 'VPN_IFACE_IPV4S=%s\n' "$(shell_quote "${VPN_IFACE_IPV4S:-}")"
    echo
    echo "# 额外需要添加到网卡上的虚拟内网 IP，多个用英文逗号分隔。"
    printf 'VPN_VIPS=%s\n' "$(shell_quote "${VPN_VIPS:-}")"
    echo
    echo "# L2TP ingress mode: smart = one local listener plus DNAT for other ingress IPs; bound = one listener per ingress IP."
    echo "# In smart mode, VPN_INGRESS_IP is only a local listener/hub IP and does not need a public EIP."
    printf 'VPN_INGRESS_MODE=%s\n' "$(shell_quote "${VPN_INGRESS_MODE:-smart}")"
    echo "# Client pool mode: auto/global/per_vip. per_vip requires VPN_INGRESS_MODE=bound and one client pool per local VIP."
    printf 'VPN_CLIENT_POOL_MODE=%s\n' "$(shell_quote "${VPN_CLIENT_POOL_MODE:-auto}")"
    echo "# Emergency compatibility only. Platform scans are the authoritative source of VPN_VIPS."
    printf 'VPN_AUTO_CONFIG_FROM_USERS=%s\n' "$(shell_quote "${VPN_AUTO_CONFIG_FROM_USERS:-0}")"
    printf 'VPN_INGRESS_IP=%s\n' "$(shell_quote "${VPN_INGRESS_IP:-${L2TP_INGRESS_IP:-}}")"
    printf 'VPN_LEFT_ID=%s\n' "$(shell_quote "${VPN_LEFT_ID:-}")"
    printf 'VPN_ENABLE_IPSEC=%s\n' "$(shell_quote "$VPN_ENABLE_IPSEC")"
    printf 'VPN_RANDOM_PSK=%s\n' "$(shell_quote "${VPN_RANDOM_PSK:-0}")"
    printf 'VPN_IPSEC_PSK=%s\n' "$(shell_quote "${VPN_IPSEC_PSK:-}")"
    printf 'VPN_ENABLE_BBR=%s\n' "$(shell_quote "${VPN_ENABLE_BBR:-1}")"
    printf 'VPN_DISABLE_DEFAULT_MASQ=%s\n' "$(shell_quote "${VPN_DISABLE_DEFAULT_MASQ:-0}")"
    printf 'VPN_ADD_EGRESS_AS_VIP=%s\n' "$(shell_quote "${VPN_ADD_EGRESS_AS_VIP:-0}")"
  } >"$tmp"
  if [ -f "$SERVER_CONFIG_FILE" ] && cmp -s "$tmp" "$SERVER_CONFIG_FILE"; then
    rm -f "$tmp"
  else
    cat "$tmp" >"$SERVER_CONFIG_FILE"
    rm -f "$tmp"
  fi
  chmod 600 "$SERVER_CONFIG_FILE"
}

write_apply_config_script() {
  log "Writing server config apply helper..."
  cat >"$APPLY_CONFIG_SCRIPT" <<EOF
#!/usr/bin/env bash
set -euo pipefail

INSTALLER_PATH="$INSTALLER_PATH"

if [ ! -x "\$INSTALLER_PATH" ]; then
  echo "L2TP installer was not found: \$INSTALLER_PATH" >&2
  echo "Run the platform install/update command once, then retry." >&2
  exit 1
fi

export VPN_INTERACTIVE=0
export VPN_APPLY_ONLY=1
exec "\$INSTALLER_PATH"
EOF
  chmod 700 "$APPLY_CONFIG_SCRIPT"
}

write_apply_users_script() {
  log "Writing user config apply helper..."
  cat >"$APPLY_USERS_SCRIPT" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

CONFIG_DIR="/etc/l2tp-vpn"
USER_CONFIG_FILE="/etc/l2tp-vpn/users.conf"
SERVER_CONFIG_FILE="/etc/l2tp-vpn/server.conf"
USER_MAP_PATH="/etc/l2tp-vpn/users.tsv"
HELPER_SERVICE="l2tp-vip-egress-setup.service"
APPLY_CONFIG_SCRIPT="/usr/local/sbin/l2tp-vpn-apply-config.sh"

if [ -x "$APPLY_CONFIG_SCRIPT" ] && [ "${L2TP_APPLY_USERS_FAST:-0}" != "1" ]; then
  VPN_INTERACTIVE=0 VPN_APPLY_ONLY=1 exec "$APPLY_CONFIG_SCRIPT"
fi

fail() {
  printf '[l2tp-apply-users] ERROR: %s\n' "$*" >&2
  exit 1
}

tsv_field() {
  local line="$1"
  local field="$2"
  awk -F '\t' -v field="$field" '{print $field; exit}' <<<"$line"
}

escape_double_quotes() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

is_ipv4() {
  local a b c d octet
  IFS=. read -r a b c d <<<"${1:-}"
  for octet in "$a" "$b" "$c" "$d"; do
    [[ "$octet" =~ ^[0-9]+$ ]] || return 1
    [ "$octet" -ge 0 ] && [ "$octet" -le 255 ] || return 1
  done
}

validate_number_between() {
  local name="$1" value="$2" min="$3" max="$4"
  [[ "$value" =~ ^[0-9]+$ ]] || fail "$name must be a number."
  [ "$value" -ge "$min" ] && [ "$value" -le "$max" ] || fail "$name must be between $min and $max."
}

ipv4_to_int() {
  local a b c d
  IFS=. read -r a b c d <<<"$1"
  printf '%u\n' "$(( (10#$a << 24) + (10#$b << 16) + (10#$c << 8) + 10#$d ))"
}

int_to_ipv4() {
  local n="$1"
  printf '%u.%u.%u.%u\n' "$(((n >> 24) & 255))" "$(((n >> 16) & 255))" "$(((n >> 8) & 255))" "$((n & 255))"
}

is_ipv4_cidr() {
  local ip prefix
  case "${1:-}" in */*) ;; *) return 1 ;; esac
  ip="${1%/*}"
  prefix="${1#*/}"
  is_ipv4 "$ip" || return 1
  [[ "$prefix" =~ ^[0-9]+$ ]] || return 1
  [ "$prefix" -ge 0 ] && [ "$prefix" -le 32 ]
}

is_ipv4_range() {
  local start_ip end_ip start_int end_int
  case "${1:-}" in *-*) ;; *) return 1 ;; esac
  start_ip="${1%-*}"
  end_ip="${1#*-}"
  is_ipv4 "$start_ip" || return 1
  is_ipv4 "$end_ip" || return 1
  start_int="$(ipv4_to_int "$start_ip")"
  end_int="$(ipv4_to_int "$end_ip")"
  [ "$end_int" -ge "$start_int" ]
}

cidr_bounds() {
  local cidr ip prefix ip_int total mask network broadcast start end
  cidr="$1"
  ip="${cidr%/*}"
  prefix="${cidr#*/}"
  ip_int="$(ipv4_to_int "$ip")"
  if [ "$prefix" -eq 0 ]; then
    mask=0
    total=$((1 << 32))
  else
    mask=$(( (0xffffffff << (32 - prefix)) & 0xffffffff ))
    total=$((1 << (32 - prefix)))
  fi
  network=$((ip_int & mask))
  broadcast=$((network + total - 1))
  if [ "$prefix" -le 30 ]; then
    start=$((network + 1))
    end=$((broadcast - 1))
  else
    start="$network"
    end="$broadcast"
  fi
  printf '%u %u\n' "$start" "$end"
}

vpn_client_pool_capacity() {
  local start_ip end_ip start_int end_int
  case "$VPN_CLIENT_POOL" in *-*) ;; *) fail "VPN_CLIENT_POOL must be an IPv4 range." ;; esac
  start_ip="${VPN_CLIENT_POOL%-*}"
  end_ip="${VPN_CLIENT_POOL#*-}"
  is_ipv4 "$start_ip" || fail "Invalid VPN_CLIENT_POOL start IP '$start_ip'."
  is_ipv4 "$end_ip" || fail "Invalid VPN_CLIENT_POOL end IP '$end_ip'."
  start_int="$(ipv4_to_int "$start_ip")"
  end_int="$(ipv4_to_int "$end_ip")"
  [ "$end_int" -ge "$start_int" ] || fail "VPN_CLIENT_POOL end IP must be greater than or equal to start IP."
  printf '%u\n' "$((end_int - start_int + 1))"
}

allocate_client_scope() {
  local __var="$1" count="$2" start_ip end_ip pool_start pool_end scope_start scope_end block_end host
  case "$VPN_CLIENT_POOL" in *-*) ;; *) fail "VPN_CLIENT_POOL must be an IPv4 range." ;; esac
  start_ip="${VPN_CLIENT_POOL%-*}"
  end_ip="${VPN_CLIENT_POOL#*-}"
  is_ipv4 "$start_ip" || fail "Invalid VPN_CLIENT_POOL start IP '$start_ip'."
  is_ipv4 "$end_ip" || fail "Invalid VPN_CLIENT_POOL end IP '$end_ip'."
  pool_start="$(ipv4_to_int "$start_ip")"
  pool_end="$(ipv4_to_int "$end_ip")"
  [ -n "${AUTO_CLIENT_NEXT:-}" ] || AUTO_CLIENT_NEXT="$pool_start"
  scope_start="$AUTO_CLIENT_NEXT"
  while :; do
    [ "$scope_start" -le "$pool_end" ] || fail "VPN_CLIENT_POOL does not have enough free addresses to auto-assign $count client IP(s)."
    host="$((scope_start & 255))"
    if [ "$host" -eq 0 ]; then
      scope_start="$((scope_start + 1))"
      continue
    fi
    if [ "$host" -eq 255 ]; then
      scope_start="$(((scope_start & 0xffffff00) + 257))"
      continue
    fi
    block_end="$(((scope_start & 0xffffff00) + 254))"
    scope_end="$((scope_start + count - 1))"
    if [ "$scope_end" -le "$block_end" ] && [ "$scope_end" -le "$pool_end" ]; then
      break
    fi
    scope_start="$(((scope_start & 0xffffff00) + 257))"
  done
  if [ "$scope_start" -eq "$scope_end" ]; then
    printf -v "$__var" '%s' "$(int_to_ipv4 "$scope_start")"
  else
    printf -v "$__var" '%s-%s' "$(int_to_ipv4 "$scope_start")" "$(int_to_ipv4 "$scope_end")"
  fi
  AUTO_CLIENT_NEXT="$((scope_end + 1))"
}

client_ip_scope_capacity() {
  local spec bounds start end
  spec="$1"
  if [ "$spec" = "*" ] || [ -z "$spec" ]; then
    vpn_client_pool_capacity
    return 0
  fi
  if is_ipv4 "$spec"; then
    echo 1
    return 0
  fi
  if is_ipv4_cidr "$spec"; then
    bounds="$(cidr_bounds "$spec")"
    start="${bounds%% *}"
    end="${bounds#* }"
    printf '%u\n' "$((end - start + 1))"
    return 0
  fi
  if is_ipv4_range "$spec"; then
    start="$(ipv4_to_int "${spec%-*}")"
    end="$(ipv4_to_int "${spec#*-}")"
    printf '%u\n' "$((end - start + 1))"
    return 0
  fi
  fail "Invalid vpn_ip '$spec'."
}

validate_client_scope_inside_pool() {
  local spec pool_start_ip pool_end_ip pool_start pool_end bounds start end
  spec="$1"
  [ -n "$spec" ] && [ "$spec" != "*" ] || return 0
  pool_start_ip="${VPN_CLIENT_POOL%-*}"
  pool_end_ip="${VPN_CLIENT_POOL#*-}"
  is_ipv4 "$pool_start_ip" || return 0
  is_ipv4 "$pool_end_ip" || return 0
  pool_start="$(ipv4_to_int "$pool_start_ip")"
  pool_end="$(ipv4_to_int "$pool_end_ip")"
  if is_ipv4 "$spec"; then
    start="$(ipv4_to_int "$spec")"
    end="$start"
  elif is_ipv4_cidr "$spec"; then
    bounds="$(cidr_bounds "$spec")"
    start="${bounds%% *}"
    end="${bounds#* }"
  elif is_ipv4_range "$spec"; then
    start="$(ipv4_to_int "${spec%-*}")"
    end="$(ipv4_to_int "${spec#*-}")"
  else
    return 0
  fi
  if [ "$start" -lt "$pool_start" ] || [ "$end" -gt "$pool_end" ]; then
    fail "vpn_ip '$spec' is outside VPN_CLIENT_POOL '$VPN_CLIENT_POOL'."
  fi
}

chap_ip_value_for_scope() {
  local spec start end i out
  spec="$1"
  if [ -z "$spec" ] || [ "$spec" = "*" ]; then printf '*'; return 0; fi
  if is_ipv4 "$spec" || is_ipv4_cidr "$spec"; then printf '%s' "$spec"; return 0; fi
  if is_ipv4_range "$spec"; then
    start="$(ipv4_to_int "${spec%-*}")"
    end="$(ipv4_to_int "${spec#*-}")"
    if [ "$((end - start + 1))" -gt 1024 ]; then
      fail "vpn_ip range '$spec' is too large to expand. Use CIDR instead."
    fi
    out=""
    for ((i=start; i<=end; i++)); do
      [ -z "$out" ] || out+=" "
      out+="$(int_to_ipv4 "$i")"
    done
    printf '%s' "$out"
    return 0
  fi
  fail "Invalid vpn_ip '$spec'."
}

user_config_hash() {
  printf '%s\t%s\t%s\t%s\t%s' "$1" "$2" "$3" "$4" "$6" | sha256sum | awk '{print $1}'
}

read_user_config_file() {
  local line cleaned entries
  entries=()
  [ -f "$USER_CONFIG_FILE" ] || fail "User config not found: $USER_CONFIG_FILE"
  while IFS= read -r line || [ -n "$line" ]; do
    cleaned="${line%%#*}"
    cleaned="$(printf '%s' "$cleaned" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
    [ -n "$cleaned" ] || continue
    entries+=("$cleaned")
  done <"$USER_CONFIG_FILE"
  [ "${#entries[@]}" -gt 0 ] || fail "No users found in $USER_CONFIG_FILE"
  printf '%s\n' "${entries[@]}"
}

parse_users() {
  USER_NAMES=()
  USER_PASSWORDS=()
  USER_CLIENT_IPS=()
  USER_CHAP_IPS=()
  USER_EGRESS_IPS=()
  USER_PUBLIC_LABELS=()
  USER_SHARE_COUNTS=()
  local entries entry username password egress_ip share_count client_ip public_label extra seen_users dynamic_share_total pool_capacity scope_capacity
  seen_users=""
  dynamic_share_total=0
  AUTO_CLIENT_NEXT=""
  mapfile -t entries < <(read_user_config_file)
  for entry in "${entries[@]}"; do
    if [[ "$entry" == *","* ]]; then
      IFS=',' read -r username password egress_ip share_count client_ip public_label extra <<<"$entry"
    else
      IFS=':' read -r username password egress_ip share_count client_ip public_label extra <<<"$entry"
    fi
    [ -z "${extra:-}" ] || fail "Invalid users.conf entry '$entry': too many fields."
    [ -n "${username:-}" ] || fail "VPN username cannot be empty."
    [ -n "${password:-}" ] || fail "VPN password cannot be empty for user '$username'."
    case "$username" in *[!A-Za-z0-9_.@-]*) fail "VPN username '$username' contains unsupported characters." ;; esac
    case ",$seen_users," in *,"$username",*) fail "Duplicate VPN username '$username'." ;; esac
    seen_users="${seen_users},${username}"
    share_count="${share_count:-${VPN_DEFAULT_SHARE_COUNT:-1}}"
    validate_number_between "share_count for $username" "$share_count" 1 999
    if [ -z "${client_ip:-}" ]; then
      client_ip="*"
    fi
    client_ip_scope_capacity "$client_ip" >/dev/null
    validate_client_scope_inside_pool "$client_ip"
    scope_capacity="$(client_ip_scope_capacity "$client_ip")"
    if [ "$share_count" -gt "$scope_capacity" ]; then
      fail "User '$username' has share_count=$share_count but vpn_ip '$client_ip' only has $scope_capacity address(es)."
    fi
    if [ "$client_ip" = "*" ] || is_ipv4_cidr "$client_ip" || is_ipv4_range "$client_ip"; then
      dynamic_share_total=$((dynamic_share_total + share_count))
    fi
    if [ -n "${egress_ip:-}" ]; then
      is_ipv4 "$egress_ip" || fail "Invalid egress local VIP '$egress_ip' for user '$username'."
    fi
    USER_NAMES+=("$username")
    USER_PASSWORDS+=("$password")
    USER_CLIENT_IPS+=("$client_ip")
    USER_CHAP_IPS+=("$(chap_ip_value_for_scope "$client_ip")")
    USER_EGRESS_IPS+=("${egress_ip:-}")
    USER_PUBLIC_LABELS+=("${public_label:-}")
    USER_SHARE_COUNTS+=("$share_count")
  done
  pool_capacity="$(vpn_client_pool_capacity)"
  if [ "$dynamic_share_total" -gt "$pool_capacity" ]; then
    fail "VPN_CLIENT_POOL has $pool_capacity dynamic addresses, but configured dynamic share_count total is $dynamic_share_total."
  fi
}

write_chap_users() {
  local chap=/etc/ppp/chap-secrets tmp i name pass client_ip
  touch "$chap"
  chmod 600 "$chap"
  tmp="$(mktemp)"
  sed '/^# BEGIN CODEX L2TP USERS$/,/^# END CODEX L2TP USERS$/d' "$chap" >"$tmp"
  cat "$tmp" >"$chap"
  rm -f "$tmp"
  {
    echo '# BEGIN CODEX L2TP USERS'
    for i in "${!USER_NAMES[@]}"; do
      name="$(escape_double_quotes "${USER_NAMES[$i]}")"
      pass="$(escape_double_quotes "${USER_PASSWORDS[$i]}")"
      client_ip="${USER_CHAP_IPS[$i]}"
      printf '"%s" l2tpd "%s" %s\n' "$name" "$pass" "$client_ip"
    done
    echo '# END CODEX L2TP USERS'
  } >>"$chap"
  chmod 600 "$chap"
}

write_user_runtime_config() {
  install -d -m 700 "$CONFIG_DIR"
  {
    echo "# username	egress_local_vip	share_count	public_eip_label	client_ip_scope	config_hash"
    local i
    for i in "${!USER_NAMES[@]}"; do
      printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
        "${USER_NAMES[$i]}" \
        "${USER_EGRESS_IPS[$i]:-}" \
        "${USER_SHARE_COUNTS[$i]}" \
        "${USER_PUBLIC_LABELS[$i]:-}" \
        "${USER_CLIENT_IPS[$i]}" \
        "$(user_config_hash "${USER_NAMES[$i]}" "${USER_PASSWORDS[$i]}" "${USER_EGRESS_IPS[$i]:-}" "${USER_SHARE_COUNTS[$i]}" "${USER_PUBLIC_LABELS[$i]:-}" "${USER_CLIENT_IPS[$i]}")"
    done
  } >"$USER_MAP_PATH"
  chmod 600 "$USER_MAP_PATH"
}

ip_in_scope() {
  local ip scope ip_int start_ip end_ip start_int end_int prefix mask total network broadcast bounds start end
  ip="$1"
  scope="$2"
  [ -n "$scope" ] && [ "$scope" != "*" ] || return 0
  is_ipv4 "$ip" || return 1
  ip_int="$(ipv4_to_int "$ip")"

  if is_ipv4 "$scope"; then
    [ "$ip" = "$scope" ]
    return $?
  fi

  if is_ipv4_cidr "$scope"; then
    start_ip="${scope%/*}"
    prefix="${scope#*/}"
    start_int="$(ipv4_to_int "$start_ip")"
    if [ "$prefix" -eq 0 ]; then
      mask=0
      total=$((1 << 32))
    else
      mask=$(( (0xffffffff << (32 - prefix)) & 0xffffffff ))
      total=$((1 << (32 - prefix)))
    fi
    network=$((start_int & mask))
    broadcast=$((network + total - 1))
    [ "$ip_int" -ge "$network" ] && [ "$ip_int" -le "$broadcast" ]
    return $?
  fi

  if is_ipv4_range "$scope"; then
    start_ip="${scope%-*}"
    end_ip="${scope#*-}"
    start_int="$(ipv4_to_int "$start_ip")"
    end_int="$(ipv4_to_int "$end_ip")"
    [ "$ip_int" -ge "$start_int" ] && [ "$ip_int" -le "$end_int" ]
    return $?
  fi

  bounds="$(cidr_bounds "$scope" 2>/dev/null || true)"
  [ -n "$bounds" ] || return 1
  start="${bounds%% *}"
  end="${bounds#* }"
  [ "$ip_int" -ge "$start" ] && [ "$ip_int" -le "$end" ]
}

kick_session() {
  local iface="$1" reason="$2"
  logger -t l2tp-vpn "Kicking $iface: $reason"
  printf 'Kicking %s: %s\n' "$iface" "$reason"
  ip link set "$iface" down >/dev/null 2>&1 || true
}

enforce_online_sessions() {
  local session_dir="/run/l2tp-vpn-sessions"
  local session_file iface session_user session_ip session_egress session_hash line cfg_egress share_count _public_label client_scope cfg_hash
  local user line_user line_share count

  [ -d "$session_dir" ] || return 0
  [ -r "$USER_MAP_PATH" ] || return 0

  for session_file in "$session_dir"/*; do
    [ -e "$session_file" ] || continue
    iface="$(basename "$session_file")"
    if ! ip link show dev "$iface" >/dev/null 2>&1; then
      rm -f "$session_file"
      continue
    fi

    session_line="$(cat "$session_file")"
    session_user="$(tsv_field "$session_line" 1)"
    session_ip="$(tsv_field "$session_line" 2)"
    session_egress="$(tsv_field "$session_line" 3)"
    session_hash="$(tsv_field "$session_line" 4)"
    [ -n "${session_user:-}" ] || { kick_session "$iface" "missing session username"; continue; }
    line="$(awk -F '\t' -v user="$session_user" 'NR > 1 && $1 == user {print; exit}' "$USER_MAP_PATH")"
    if [ -z "$line" ]; then
      kick_session "$iface" "user $session_user is not in users.conf"
      continue
    fi

    cfg_egress="$(tsv_field "$line" 2)"
    share_count="$(tsv_field "$line" 3)"
    client_scope="$(tsv_field "$line" 5)"
    cfg_hash="$(tsv_field "$line" 6)"
    client_scope="${client_scope:-*}"
    if [ -z "${session_hash:-}" ]; then
      kick_session "$iface" "session has no config hash; reconnect required"
      continue
    fi
    if [ -n "${cfg_hash:-}" ] && [ "$session_hash" != "$cfg_hash" ]; then
      kick_session "$iface" "user $session_user config changed"
      continue
    fi
    if [ "${session_egress:-}" != "${cfg_egress:-}" ]; then
      kick_session "$iface" "egress VIP changed for $session_user"
      continue
    fi
    if ! ip_in_scope "$session_ip" "$client_scope"; then
      kick_session "$iface" "assigned IP $session_ip is outside $client_scope"
      continue
    fi
  done

  while IFS= read -r line; do
    line_user="$(tsv_field "$line" 1)"
    line_share="$(tsv_field "$line" 3)"
    [ "$line_user" = "# username" ] && continue
    [ -n "$line_user" ] || continue
    line_share="${line_share:-1}"
    count=0
    for session_file in $(ls -1tr "$session_dir"/* 2>/dev/null || true); do
      [ -e "$session_file" ] || continue
      iface="$(basename "$session_file")"
      ip link show dev "$iface" >/dev/null 2>&1 || { rm -f "$session_file"; continue; }
      session_line="$(cat "$session_file")"
      session_user="$(tsv_field "$session_line" 1)"
      [ "$session_user" = "$line_user" ] || continue
      count=$((count + 1))
      if [ "$count" -gt "$line_share" ]; then
        kick_session "$iface" "share_count $line_share exceeded for $line_user"
      fi
    done
  done <"$USER_MAP_PATH"
}

VPN_CLIENT_POOL="172.18.0.2-172.18.255.254"
VPN_DEFAULT_SHARE_COUNT="1"
VPN_INGRESS_MODE="smart"
VPN_CLIENT_POOL_MODE="auto"
VPN_AUTO_CONFIG_FROM_USERS="0"
if [ -f "$SERVER_CONFIG_FILE" ]; then
  # shellcheck disable=SC1090
  source "$SERVER_CONFIG_FILE"
fi

parse_users
write_chap_users
write_user_runtime_config
systemctl restart "$HELPER_SERVICE" >/dev/null 2>&1 || true
enforce_online_sessions
logger -t l2tp-vpn "Applied user config from $USER_CONFIG_FILE"
printf 'Applied %s user(s) from %s\n' "${#USER_NAMES[@]}" "$USER_CONFIG_FILE"
EOF
  chmod 700 "$APPLY_USERS_SCRIPT"

  cat >"/etc/systemd/system/$APPLY_USERS_SERVICE" <<EOF
[Unit]
Description=Apply L2TP VPN user config

[Service]
Type=oneshot
ExecStart=$APPLY_USERS_SCRIPT
EOF

  cat >"/etc/systemd/system/$APPLY_USERS_PATH" <<EOF
[Unit]
Description=Watch L2TP VPN user config

[Path]
PathChanged=$USER_CONFIG_FILE
PathModified=$USER_CONFIG_FILE
PathChanged=$SERVER_CONFIG_FILE
PathModified=$SERVER_CONFIG_FILE
Unit=$APPLY_USERS_SERVICE

[Install]
WantedBy=multi-user.target
EOF
}

configure_bbr() {
  local current_cc
  [ "${VPN_ENABLE_BBR:-1}" = "1" ] || {
    log "BBR acceleration is disabled by VPN_ENABLE_BBR=0."
    return 0
  }

  log "Enabling TCP BBR acceleration when supported by the current kernel..."
  modprobe tcp_bbr >/dev/null 2>&1 || true

  if sysctl net.ipv4.tcp_available_congestion_control 2>/dev/null | grep -qw bbr; then
    cat >/etc/sysctl.d/98-l2tp-bbr.conf <<EOF
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr
EOF
    sysctl --system >/dev/null || true
    current_cc="$(sysctl -n net.ipv4.tcp_congestion_control 2>/dev/null || true)"
    if [ "$current_cc" = "bbr" ]; then
      log "BBR is enabled."
    else
      log "BBR config was written, but current congestion control is '$current_cc'. Check kernel support and sysctl policy."
    fi
    return 0
  fi

  log "BBR is not available in the current kernel. Skipping kernel upgrade for safety; L2TP installation will continue."
}

configure_sysctl() {
  log "Configuring kernel forwarding..."
  cat >/etc/sysctl.d/99-l2tp-vpn.conf <<EOF
net.ipv4.ip_forward = 1
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.all.rp_filter = 0
net.ipv4.conf.default.rp_filter = 0
EOF
  sysctl --system >/dev/null
}

configure_ipsec() {
  log "Writing strongSwan IPsec configuration..."
  backup_file /etc/ipsec.conf
  backup_file /etc/ipsec.secrets

  cat >/etc/ipsec.conf <<EOF
config setup
    uniqueids=no

conn L2TP-PSK
    keyexchange=ikev1
    type=transport
    authby=secret
    left=%any
EOF

  if [ -n "${VPN_LEFT_ID:-}" ]; then
    printf '    leftid=%s\n' "$VPN_LEFT_ID" >>/etc/ipsec.conf
  fi

  cat >>/etc/ipsec.conf <<EOF
    leftprotoport=17/$VPN_L2TP_PORT
    right=%any
    rightprotoport=17/%any
    ike=aes256-sha1-modp1024,aes128-sha1-modp1024,3des-sha1-modp1024!
    esp=aes256-sha1,aes128-sha1,3des-sha1!
    forceencaps=yes
    rekey=no
    dpddelay=30
    dpdtimeout=120
    dpdaction=clear
    auto=add
EOF

  printf ': PSK "%s"\n' "$(escape_double_quotes "$VPN_IPSEC_PSK")" >/etc/ipsec.secrets
  chmod 600 /etc/ipsec.secrets
}

append_unique_value() {
  local __var="$1" value="$2" item idx len
  [ -n "$value" ] || return 0
  eval "len=\${#${__var}[@]}"
  for ((idx=0; idx<len; idx++)); do
    eval "item=\${${__var}[\$idx]}"
    [ "$item" = "$value" ] && return 0
  done
  eval "${__var}+=(\"\$value\")"
}

first_iface_ip() {
  local raw item ip
  raw="${VPN_IFACE_IPV4S:-}"
  raw="${raw//\\,/ }"
  raw="${raw//,/ }"
  for item in $raw; do
    ip="${item%%/*}"
    if is_ipv4 "$ip"; then
      printf '%s\n' "$ip"
      return 0
    fi
  done
  return 1
}

iface_has_ip() {
  local target="$1" raw item ip
  raw="${VPN_IFACE_IPV4S:-}"
  raw="${raw//\\,/ }"
  raw="${raw//,/ }"
  for item in $raw; do
    ip="${item%%/*}"
    [ "$ip" = "$target" ] && return 0
  done
  return 1
}

append_ip_values() {
  local __var="$1" raw="$2" item ip
  raw="${raw//\\,/ }"
  raw="${raw//,/ }"
  for item in $raw; do
    ip="${item%%/*}"
    if is_ipv4 "$ip"; then
      append_unique_value "$__var" "$ip"
    fi
  done
}

vip_list_has_ip() {
  local target="$1" raw="${VPN_VIPS:-}" item ip
  raw="${raw//\\,/ }"
  raw="${raw//,/ }"
  for item in $raw; do
    ip="${item%%/*}"
    [ "$ip" = "$target" ] && return 0
  done
  return 1
}

auto_config_from_users() {
  [ "${VPN_AUTO_CONFIG_FROM_USERS:-0}" = "1" ] || return 0
  local i egress_ip client_scope has_dedicated=0 added_vips=0

  for i in "${!USER_NAMES[@]}"; do
    egress_ip="${USER_EGRESS_IPS[$i]:-}"
    client_scope="${USER_CLIENT_IPS[$i]:-*}"

    if [ -n "$client_scope" ] && [ "$client_scope" != "*" ]; then
      has_dedicated=1
    fi

    if [ -n "$egress_ip" ] && is_ipv4 "$egress_ip"; then
      if ! iface_has_ip "$egress_ip" && ! vip_list_has_ip "$egress_ip"; then
        VPN_VIPS="${VPN_VIPS:+$VPN_VIPS,}$egress_ip/32"
        append_unique_value VIP_ITEMS "$egress_ip/32"
        added_vips=1
      fi
    fi
  done

  if [ "$has_dedicated" = "1" ] && [ "${VPN_CLIENT_POOL_MODE:-auto}" = "auto" ]; then
    VPN_CLIENT_POOL_MODE="per_vip"
    log "Detected dedicated client IP/pool in users.conf; using VPN_CLIENT_POOL_MODE=per_vip."
  fi

  if [ "$has_dedicated" = "1" ] && [ "${VPN_CLIENT_POOL_MODE:-auto}" = "per_vip" ] && [ "${VPN_INGRESS_MODE:-smart}" != "bound" ]; then
    VPN_INGRESS_MODE="bound"
    log "Detected dedicated client IP/pool in users.conf; using VPN_INGRESS_MODE=bound."
  fi

  if [ "$added_vips" = "1" ]; then
    log "Added egress IPs from users.conf to VPN_VIPS: $VPN_VIPS"
  fi
}

resolve_ingress_ip() {
  local configured_ip="${VPN_INGRESS_IP:-}" scanned_ip egress_ip
  scanned_ip="$(first_iface_ip || true)"
  if is_ipv4 "$configured_ip" && iface_has_ip "$configured_ip"; then
    L2TP_INGRESS_IP="$configured_ip"
    return 0
  fi
  if is_ipv4 "$configured_ip" && [ -n "$scanned_ip" ]; then
    log "Configured VPN_INGRESS_IP $configured_ip is not present on $VPN_IFACE; using scanned local IP $scanned_ip."
  fi
  L2TP_INGRESS_IP="$scanned_ip"
  is_ipv4 "$L2TP_INGRESS_IP" || fail "Could not determine VPN_INGRESS_IP. Set it to a local IPv4 address on $VPN_IFACE."
}

collect_l2tp_bind_ips() {
  L2TP_BIND_IPS=()
  local primary_ip egress_ip
  resolve_ingress_ip
  primary_ip="$L2TP_INGRESS_IP"
  if [ "${VPN_INGRESS_MODE:-smart}" = "smart" ]; then
    append_unique_value L2TP_BIND_IPS "$primary_ip"
    return 0
  fi
  append_ip_values L2TP_BIND_IPS "${VPN_IFACE_IPV4S:-}"
  append_ip_values L2TP_BIND_IPS "${VPN_VIPS:-}"
  [ "${#L2TP_BIND_IPS[@]}" -gt 0 ] || append_unique_value L2TP_BIND_IPS "${primary_ip:-}"
}

collect_ingress_dnat_ips() {
  L2TP_INGRESS_DNAT_IPS=()
  resolve_ingress_ip
  [ "${VPN_INGRESS_MODE:-smart}" = "smart" ] || return 0
  append_ip_values L2TP_INGRESS_DNAT_IPS "${VPN_IFACE_IPV4S:-}"
  append_ip_values L2TP_INGRESS_DNAT_IPS "${VPN_VIPS:-}"
  local filtered=() ip
  for ip in "${L2TP_INGRESS_DNAT_IPS[@]:-}"; do
    [ "$ip" = "$L2TP_INGRESS_IP" ] && continue
    append_unique_value filtered "$ip"
  done
  L2TP_INGRESS_DNAT_IPS=("${filtered[@]}")
}

pool_chunk_for_index() {
  local __start_var="$1" __end_var="$2" index="$3" count="$4"
  local pool_start_ip pool_end_ip pool_start pool_end start_block end_block total_blocks chunk_start_block chunk_end_block chunk_start chunk_end
  pool_start_ip="${VPN_CLIENT_POOL%-*}"
  pool_end_ip="${VPN_CLIENT_POOL#*-}"
  pool_start="$(ipv4_to_int "$pool_start_ip")"
  pool_end="$(ipv4_to_int "$pool_end_ip")"
  start_block="$((pool_start >> 8))"
  end_block="$((pool_end >> 8))"
  total_blocks="$((end_block - start_block + 1))"
  chunk_start_block="$((start_block + (index * total_blocks / count)))"
  chunk_end_block="$((start_block + ((index + 1) * total_blocks / count) - 1))"
  [ "$chunk_end_block" -ge "$chunk_start_block" ] || chunk_end_block="$chunk_start_block"
  chunk_start="$((chunk_start_block * 256 + 1))"
  chunk_end="$((chunk_end_block * 256 + 254))"
  [ "$chunk_start" -lt "$pool_start" ] && chunk_start="$pool_start"
  [ "$chunk_end" -gt "$pool_end" ] && chunk_end="$pool_end"
  printf -v "$__start_var" '%s' "$(int_to_ipv4 "$chunk_start")"
  printf -v "$__end_var" '%s' "$(int_to_ipv4 "$chunk_end")"
}

client_scope_to_range() {
  local __start_var="$1" __end_var="$2" scope="$3"
  local bounds
  if is_ipv4 "$scope"; then
    printf -v "$__start_var" '%s' "$scope"
    printf -v "$__end_var" '%s' "$scope"
    return 0
  fi
  if is_ipv4_cidr "$scope"; then
    bounds="$(cidr_bounds "$scope")"
    printf -v "$__start_var" '%s' "$(int_to_ipv4 "${bounds%% *}")"
    printf -v "$__end_var" '%s' "$(int_to_ipv4 "${bounds#* }")"
    return 0
  fi
  if is_ipv4_range "$scope"; then
    printf -v "$__start_var" '%s' "${scope%-*}"
    printf -v "$__end_var" '%s' "${scope#*-}"
    return 0
  fi
  return 1
}

dedicated_client_scope_for_bind_ip() {
  local __start_var="$1" __end_var="$2" bind_ip="$3"
  local i scope chosen_scope user_list
  [ "${VPN_CLIENT_POOL_MODE:-auto}" != "global" ] || return 1
  chosen_scope=""
  user_list=""
  for i in "${!USER_NAMES[@]}"; do
    [ "${USER_EGRESS_IPS[$i]:-}" = "$bind_ip" ] || continue
    scope="${USER_CLIENT_IPS[$i]:-*}"
    [ -n "$scope" ] && [ "$scope" != "*" ] || continue
    if [ -n "$chosen_scope" ] && [ "$chosen_scope" != "$scope" ]; then
      fail "Local VIP $bind_ip has multiple dedicated client pools ($chosen_scope and $scope). xl2tpd can only use one pool per listening IP. Use the same pool, another VIP, another port, or leave column 5 empty."
    fi
    chosen_scope="$scope"
    user_list="${user_list}${user_list:+,}${USER_NAMES[$i]}"
  done
  [ -n "$chosen_scope" ] || return 1
  client_scope_to_range "$__start_var" "$__end_var" "$chosen_scope" ||
    fail "Invalid dedicated client pool '$chosen_scope' for local VIP $bind_ip."
  log "Using dedicated client pool $chosen_scope on local VIP $bind_ip for user(s): $user_list"
  return 0
}

validate_dedicated_scope_ingress_mode() {
  local i scope
  [ "${VPN_CLIENT_POOL_MODE:-auto}" != "global" ] || return 0
  for i in "${!USER_NAMES[@]}"; do
    scope="${USER_CLIENT_IPS[$i]:-*}"
    [ -n "$scope" ] && [ "$scope" != "*" ] || continue
    if [ "${VPN_INGRESS_MODE:-smart}" != "bound" ]; then
      fail "User '${USER_NAMES[$i]}' uses dedicated vpn_ip '$scope'. Set VPN_INGRESS_MODE=bound and connect this user to the public EIP mapped to ${USER_EGRESS_IPS[$i]:-its local VIP}, or leave column 5 empty for the global pool."
    fi
  done
}

write_xl2tpd_multi_service() {
  collect_l2tp_bind_ips
  validate_dedicated_scope_ingress_mode
  if [ "${#L2TP_BIND_IPS[@]}" -le 1 ]; then
    rm -f "/etc/systemd/system/$XL2TPD_MULTI_SERVICE" "$XL2TPD_MULTI_HELPER" 2>/dev/null || true
    rm -rf "$XL2TPD_MULTI_CONFIG_DIR" 2>/dev/null || true
    return 0
  fi

  log "Writing multi-IP xl2tpd service for ${#L2TP_BIND_IPS[@]} local IPs..."
  install -d -m 755 "$XL2TPD_MULTI_CONFIG_DIR"
  rm -f "$XL2TPD_MULTI_CONFIG_DIR"/*.conf 2>/dev/null || true

  local i bind_ip safe start_ip end_ip
  for i in "${!L2TP_BIND_IPS[@]}"; do
    bind_ip="${L2TP_BIND_IPS[$i]}"
    safe="${bind_ip//./-}"
    if ! dedicated_client_scope_for_bind_ip start_ip end_ip "$bind_ip"; then
      pool_chunk_for_index start_ip end_ip "$i" "${#L2TP_BIND_IPS[@]}"
    fi
    cat >"$XL2TPD_MULTI_CONFIG_DIR/xl2tpd-$safe.conf" <<EOF
[global]
listen-addr = $bind_ip
port = $VPN_L2TP_PORT

[lns default]
ip range = $start_ip-$end_ip
local ip = $VPN_LOCAL_IP
require chap = yes
refuse pap = yes
require authentication = yes
name = l2tpd
pppoptfile = /etc/ppp/options.xl2tpd
length bit = yes
EOF
  done

  {
    cat <<EOF
#!/usr/bin/env bash
set -euo pipefail

CONFIG_DIR=$(shell_quote "$XL2TPD_MULTI_CONFIG_DIR")
RUN_DIR=$(shell_quote "$XL2TPD_MULTI_RUN_DIR")

stop_instances() {
  local pid_file pid
  for pid_file in "\$RUN_DIR"/*.pid; do
    [ -f "\$pid_file" ] || continue
    pid="\$(cat "\$pid_file" 2>/dev/null || true)"
    if [ -n "\$pid" ]; then
      kill "\$pid" >/dev/null 2>&1 || true
    fi
    rm -f "\$pid_file"
  done
}

start_instances() {
  local config safe pid_file control_file
  mkdir -p "\$RUN_DIR"
  stop_instances
  for config in "\$CONFIG_DIR"/*.conf; do
    [ -f "\$config" ] || continue
    safe="\$(basename "\$config" .conf)"
    pid_file="\$RUN_DIR/\$safe.pid"
    control_file="\$RUN_DIR/\$safe.control"
    /usr/sbin/xl2tpd -c "\$config" -p "\$pid_file" -C "\$control_file"
  done
}

case "\${1:-start}" in
  start)
    start_instances
    ;;
  stop)
    stop_instances
    ;;
  restart)
    stop_instances
    start_instances
    ;;
  status)
    ls -l "\$RUN_DIR"/*.pid 2>/dev/null || true
    ;;
  *)
    echo "Usage: \$0 {start|stop|restart|status}" >&2
    exit 2
    ;;
esac
EOF
  } >"$XL2TPD_MULTI_HELPER"
  chmod 700 "$XL2TPD_MULTI_HELPER"

  cat >"/etc/systemd/system/$XL2TPD_MULTI_SERVICE" <<EOF
[Unit]
Description=Run one xl2tpd listener per local L2TP IP
Wants=network-online.target $HELPER_SERVICE
After=network-online.target $HELPER_SERVICE
Conflicts=xl2tpd.service

[Service]
Type=oneshot
ExecStart=$XL2TPD_MULTI_HELPER start
ExecStop=$XL2TPD_MULTI_HELPER stop
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
}

configure_xl2tpd() {
  log "Writing xl2tpd and PPP configuration..."
  install -d -m 755 /etc/xl2tpd /etc/ppp
  backup_file /etc/xl2tpd/xl2tpd.conf
  backup_file /etc/ppp/options.xl2tpd
  collect_l2tp_bind_ips
  validate_dedicated_scope_ingress_mode
  local single_pool_start single_pool_end lns_pool
  lns_pool="$VPN_CLIENT_POOL"
  if [ "${#L2TP_BIND_IPS[@]}" -eq 1 ] && [ -n "${L2TP_BIND_IPS[0]:-}" ]; then
    if dedicated_client_scope_for_bind_ip single_pool_start single_pool_end "${L2TP_BIND_IPS[0]}"; then
      lns_pool="$single_pool_start-$single_pool_end"
    fi
  fi

  cat >/etc/xl2tpd/xl2tpd.conf <<EOF
[global]
$(if [ "${#L2TP_BIND_IPS[@]}" -eq 1 ] && [ -n "${L2TP_BIND_IPS[0]:-}" ]; then printf 'listen-addr = %s\n' "${L2TP_BIND_IPS[0]}"; fi)
port = $VPN_L2TP_PORT

[lns default]
ip range = $lns_pool
local ip = $VPN_LOCAL_IP
require chap = yes
refuse pap = yes
require authentication = yes
name = l2tpd
pppoptfile = /etc/ppp/options.xl2tpd
length bit = yes
EOF

  cat >/etc/ppp/options.xl2tpd <<EOF
ipcp-accept-local
ipcp-accept-remote
refuse-pap
refuse-chap
refuse-mschap
require-mschap-v2
noccp
auth
hide-password
idle 1800
mtu $VPN_MTU
mru $VPN_MRU
nodefaultroute
debug
proxyarp
connect-delay 5000
ms-dns $VPN_DNS1
ms-dns $VPN_DNS2
lcp-echo-interval 30
lcp-echo-failure 4
EOF
  write_xl2tpd_multi_service
}

write_chap_users() {
  log "Writing PPP users..."
  local chap=/etc/ppp/chap-secrets
  local tmp
  touch "$chap"
  chmod 600 "$chap"
  backup_file "$chap"
  tmp="$(mktemp)"
  sed '/^# BEGIN CODEX L2TP USERS$/,/^# END CODEX L2TP USERS$/d' "$chap" >"$tmp"
  cat "$tmp" >"$chap"
  rm -f "$tmp"

  {
    echo '# BEGIN CODEX L2TP USERS'
    local i name pass client_ip
    for i in "${!USER_NAMES[@]}"; do
      name="$(escape_double_quotes "${USER_NAMES[$i]}")"
      pass="$(escape_double_quotes "${USER_PASSWORDS[$i]}")"
      client_ip="${USER_CHAP_IPS[$i]}"
      printf '"%s" l2tpd "%s" %s\n' "$name" "$pass" "$client_ip"
    done
    echo '# END CODEX L2TP USERS'
  } >>"$chap"
  chmod 600 "$chap"
}

write_user_runtime_config() {
  log "Writing per-user egress and share-count map..."
  install -d -m 700 "$(dirname "$USER_MAP_PATH")"
  {
    echo "# username	egress_local_vip	share_count	public_eip_label	client_ip_scope	config_hash"
    local i
    for i in "${!USER_NAMES[@]}"; do
      printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
        "${USER_NAMES[$i]}" \
        "${USER_EGRESS_IPS[$i]:-}" \
        "${USER_SHARE_COUNTS[$i]}" \
        "${USER_PUBLIC_LABELS[$i]:-}" \
        "${USER_CLIENT_IPS[$i]}" \
        "$(user_config_hash "${USER_NAMES[$i]}" "${USER_PASSWORDS[$i]}" "${USER_EGRESS_IPS[$i]:-}" "${USER_SHARE_COUNTS[$i]}" "${USER_PUBLIC_LABELS[$i]:-}" "${USER_CLIENT_IPS[$i]}")"
    done
  } >"$USER_MAP_PATH"
  chmod 600 "$USER_MAP_PATH"
}

append_local_hook() {
  local hook_file="$1"
  local target_script="$2"
  local marker="$3"

  if [ -e "$hook_file" ] && ! grep -q "$marker" "$hook_file"; then
    backup_file "$hook_file"
  fi

  if [ ! -e "$hook_file" ]; then
    cat >"$hook_file" <<EOF
#!/usr/bin/env bash
set -e
# BEGIN $marker
"$target_script" "\$@" || true
# END $marker
EOF
  elif ! grep -q "$marker" "$hook_file"; then
    cat >>"$hook_file" <<EOF

# BEGIN $marker
"$target_script" "\$@" || true
# END $marker
EOF
  fi
  chmod 700 "$hook_file"
}

write_ppp_hooks() {
  log "Writing PPP per-user egress hooks..."
  install -d -m 755 /etc/ppp/ip-up.d /etc/ppp/ip-down.d

  cat >"$PPP_UP_SCRIPT" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

VPN_IFACE_PLACEHOLDER
USER_MAP_PATH_PLACEHOLDER
SESSION_DIR="/run/l2tp-vpn-sessions"

ipv4_to_int() {
  local a b c d
  IFS=. read -r a b c d <<<"$1"
  printf '%u\n' "$(( (10#$a << 24) + (10#$b << 16) + (10#$c << 8) + 10#$d ))"
}

ip_in_scope() {
  local ip scope ip_int start_ip end_ip start_int end_int prefix mask total network broadcast
  ip="$1"
  scope="$2"
  [ -n "$scope" ] && [ "$scope" != "*" ] || return 0
  ip_int="$(ipv4_to_int "$ip")"

  case "$scope" in
    */*)
      start_ip="${scope%/*}"
      prefix="${scope#*/}"
      start_int="$(ipv4_to_int "$start_ip")"
      if [ "$prefix" -eq 0 ]; then
        mask=0
        total=$((1 << 32))
      else
        mask=$(( (0xffffffff << (32 - prefix)) & 0xffffffff ))
        total=$((1 << (32 - prefix)))
      fi
      network=$((start_int & mask))
      broadcast=$((network + total - 1))
      [ "$ip_int" -ge "$network" ] && [ "$ip_int" -le "$broadcast" ]
      ;;
    *-*)
      start_ip="${scope%-*}"
      end_ip="${scope#*-}"
      start_int="$(ipv4_to_int "$start_ip")"
      end_int="$(ipv4_to_int "$end_ip")"
      [ "$ip_int" -ge "$start_int" ] && [ "$ip_int" -le "$end_int" ]
      ;;
    *)
      [ "$ip" = "$scope" ]
      ;;
  esac
}

tsv_field() {
  local line="$1"
  local field="$2"
  awk -F '\t' -v field="$field" '{print $field; exit}' <<<"$line"
}

iface="${1:-${IFNAME:-}}"
remote_ip="${5:-${IPREMOTE:-}}"
peer="${PEERNAME:-}"

[ -n "$iface" ] || exit 0
[ -n "$remote_ip" ] || exit 0
[ -n "$peer" ] || exit 0
[ -r "$USER_MAP_PATH" ] || exit 0

line="$(awk -F '\t' -v user="$peer" '$1 == user {print; exit}' "$USER_MAP_PATH")"
[ -n "$line" ] || exit 0

egress_ip="$(tsv_field "$line" 2)"
share_count="$(tsv_field "$line" 3)"
client_scope="$(tsv_field "$line" 5)"
config_hash="$(tsv_field "$line" 6)"
share_count="${share_count:-1}"
client_scope="${client_scope:-*}"

if ! ip_in_scope "$remote_ip" "$client_scope"; then
  logger -t l2tp-vpn "Rejecting $peer on $iface: assigned IP $remote_ip is outside $client_scope"
  ip link set "$iface" down >/dev/null 2>&1 || true
  exit 0
fi

mkdir -p "$SESSION_DIR"
rm -f "$SESSION_DIR/$iface"

for session_file in "$SESSION_DIR"/*; do
  [ -e "$session_file" ] || continue
  old_iface="$(basename "$session_file")"
  if ! ip link show dev "$old_iface" >/dev/null 2>&1; then
    rm -f "$session_file"
  fi
done

active_count=0
for session_file in "$SESSION_DIR"/*; do
  [ -f "$session_file" ] || continue
  session_line="$(cat "$session_file")"
  session_user="$(tsv_field "$session_line" 1)"
  if [ "$session_user" = "$peer" ]; then
    active_count=$((active_count + 1))
  fi
done

if [ "$active_count" -ge "$share_count" ]; then
  logger -t l2tp-vpn "Rejecting $peer on $iface: share count $share_count exceeded"
  ip link set "$iface" down >/dev/null 2>&1 || true
  exit 0
fi

printf '%s\t%s\t%s\t%s\n' "$peer" "$remote_ip" "$egress_ip" "${config_hash:-}" >"$SESSION_DIR/$iface"

if [ -n "$egress_ip" ]; then
  if ! iptables -t nat -C POSTROUTING -s "$remote_ip/32" -o "$VPN_IFACE" -j SNAT --to-source "$egress_ip" >/dev/null 2>&1; then
    iptables -t nat -I POSTROUTING 1 -s "$remote_ip/32" -o "$VPN_IFACE" -j SNAT --to-source "$egress_ip" >/dev/null 2>&1 ||
      logger -t l2tp-vpn "Warning: failed to add per-session SNAT $remote_ip -> $egress_ip on $VPN_IFACE"
  fi
fi
EOF

  sed -i \
    -e "s|VPN_IFACE_PLACEHOLDER|VPN_IFACE=$(shell_quote "$VPN_IFACE")|" \
    -e "s|USER_MAP_PATH_PLACEHOLDER|USER_MAP_PATH=$(shell_quote "$USER_MAP_PATH")|" \
    "$PPP_UP_SCRIPT"
  chmod 700 "$PPP_UP_SCRIPT"

  cat >"$PPP_DOWN_SCRIPT" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

VPN_IFACE_PLACEHOLDER
USER_MAP_PATH_PLACEHOLDER
SESSION_DIR="/run/l2tp-vpn-sessions"

tsv_field() {
  local line="$1"
  local field="$2"
  awk -F '\t' -v field="$field" '{print $field; exit}' <<<"$line"
}

iface="${1:-${IFNAME:-}}"
remote_ip="${5:-${IPREMOTE:-}}"
peer="${PEERNAME:-}"
egress_ip=""

if [ -n "$iface" ] && [ -f "$SESSION_DIR/$iface" ]; then
  session_line="$(cat "$SESSION_DIR/$iface")"
  peer_from_file="$(tsv_field "$session_line" 1)"
  remote_from_file="$(tsv_field "$session_line" 2)"
  egress_from_file="$(tsv_field "$session_line" 3)"
  peer="${peer:-$peer_from_file}"
  remote_ip="${remote_ip:-$remote_from_file}"
  egress_ip="${egress_from_file:-}"
  rm -f "$SESSION_DIR/$iface"
fi

if [ -z "$egress_ip" ] && [ -n "$peer" ] && [ -r "$USER_MAP_PATH" ]; then
  line="$(awk -F '\t' -v user="$peer" '$1 == user {print; exit}' "$USER_MAP_PATH")"
  if [ -n "$line" ]; then
    egress_ip="$(tsv_field "$line" 2)"
  fi
fi

if [ -n "$remote_ip" ] && [ -n "$egress_ip" ]; then
  while iptables -t nat -D POSTROUTING -s "$remote_ip/32" -o "$VPN_IFACE" -j SNAT --to-source "$egress_ip" >/dev/null 2>&1; do
    :
  done
fi
EOF

  sed -i \
    -e "s|VPN_IFACE_PLACEHOLDER|VPN_IFACE=$(shell_quote "$VPN_IFACE")|" \
    -e "s|USER_MAP_PATH_PLACEHOLDER|USER_MAP_PATH=$(shell_quote "$USER_MAP_PATH")|" \
    "$PPP_DOWN_SCRIPT"
  chmod 700 "$PPP_DOWN_SCRIPT"

  cp -f "$PPP_UP_SCRIPT" /etc/ppp/ip-up.d/99-l2tp-user-egress
  cp -f "$PPP_DOWN_SCRIPT" /etc/ppp/ip-down.d/99-l2tp-user-egress
  chmod 700 /etc/ppp/ip-up.d/99-l2tp-user-egress /etc/ppp/ip-down.d/99-l2tp-user-egress

  append_local_hook /etc/ppp/ip-up.local "$PPP_UP_SCRIPT" "CODEX L2TP USER EGRESS UP"
  append_local_hook /etc/ppp/ip-down.local "$PPP_DOWN_SCRIPT" "CODEX L2TP USER EGRESS DOWN"
}

configure_firewalld_or_ufw() {
  if command -v firewall-cmd >/dev/null 2>&1 && systemctl is-active --quiet firewalld; then
    log "Opening ports in firewalld..."
    if [ "$VPN_ENABLE_IPSEC" = "1" ]; then
      firewall-cmd --permanent --add-port=500/udp >/dev/null || true
      firewall-cmd --permanent --add-port=4500/udp >/dev/null || true
      firewall-cmd --permanent --add-protocol=esp >/dev/null || true
    else
      firewall-cmd --permanent --remove-port=500/udp >/dev/null || true
      firewall-cmd --permanent --remove-port=4500/udp >/dev/null || true
      firewall-cmd --permanent --remove-protocol=esp >/dev/null || true
    fi
    firewall-cmd --permanent --add-port="$VPN_L2TP_PORT/udp" >/dev/null || true
    firewall-cmd --permanent --add-masquerade >/dev/null || true
    firewall-cmd --reload >/dev/null || true
  fi

  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -qi '^Status: active'; then
    log "Opening ports in ufw..."
    if [ "$VPN_ENABLE_IPSEC" = "1" ]; then
      ufw allow 500/udp >/dev/null || true
      ufw allow 4500/udp >/dev/null || true
    fi
    ufw allow "$VPN_L2TP_PORT/udp" >/dev/null || true
  fi
}

write_helper_script() {
  log "Writing VIP and egress setup helper..."
  {
    cat <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

wait_for_iface() {
  local iface="$1"
  local i
  for i in $(seq 1 30); do
    if ip link show dev "$iface" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "Network interface $iface was not found." >&2
  exit 1
}

EOF

    printf 'SERVER_CONFIG_FILE=%s\n' "$(shell_quote "$SERVER_CONFIG_FILE")"
    printf 'VPN_IFACE=%s\n' "$(shell_quote "$VPN_IFACE")"
    printf 'VPN_CIDR=%s\n' "$(shell_quote "$VPN_CIDR")"
    printf 'VPN_L2TP_PORT=%s\n' "$(shell_quote "$VPN_L2TP_PORT")"
    printf 'VPN_INGRESS_MODE=%s\n' "$(shell_quote "${VPN_INGRESS_MODE:-smart}")"
    printf 'VPN_CLIENT_POOL_MODE=%s\n' "$(shell_quote "${VPN_CLIENT_POOL_MODE:-auto}")"
    printf 'VPN_AUTO_CONFIG_FROM_USERS=%s\n' "$(shell_quote "${VPN_AUTO_CONFIG_FROM_USERS:-0}")"
    printf 'VPN_INGRESS_IP=%s\n' "$(shell_quote "${L2TP_INGRESS_IP:-${VPN_INGRESS_IP:-}}")"
    printf 'USER_MAP_PATH=%s\n' "$(shell_quote "$USER_MAP_PATH")"
    printf 'VPN_DISABLE_DEFAULT_MASQ=%s\n' "$(shell_quote "${VPN_DISABLE_DEFAULT_MASQ:-0}")"
    printf 'VPN_ENABLE_BBR=%s\n' "$(shell_quote "${VPN_ENABLE_BBR:-1}")"
    printf 'VPN_ENABLE_IPSEC=%s\n' "$(shell_quote "$VPN_ENABLE_IPSEC")"
    printf 'VPN_VIPS=%s\n' "$(shell_quote "${VPN_VIPS:-}")"
    echo 'SNAT_MAP=('
    local item
    for item in "${SNAT_MAP[@]}"; do
      printf '  %s\n' "$(shell_quote "$item")"
    done
    echo ')'
    collect_ingress_dnat_ips
    echo 'INGRESS_DNAT_IPS=('
    for item in "${L2TP_INGRESS_DNAT_IPS[@]:-}"; do
      printf '  %s\n' "$(shell_quote "$item")"
    done
    echo ')'

    cat <<'EOF'

if [ -f "$SERVER_CONFIG_FILE" ]; then
  # shellcheck disable=SC1090
  source "$SERVER_CONFIG_FILE"
fi

VIP_ITEMS=()
if [ -n "${VPN_VIPS:-}" ]; then
  IFS=',' read -r -a _vip_parts <<<"$VPN_VIPS"
  for _vip in "${_vip_parts[@]}"; do
    [ -n "$_vip" ] || continue
    VIP_ITEMS+=("$_vip")
  done
fi

wait_for_iface "$VPN_IFACE"

MANAGED_SNAT_COMMENT="ctyun-l2tp-managed-snat"
INGRESS_DNAT_COMMENT="ctyun-l2tp-ingress-dnat"

cleanup_managed_snat_rules() {
  local tmp
  tmp="$(mktemp)"
  if iptables-save -t nat >"$tmp" 2>/dev/null; then
    if grep -q -- "$MANAGED_SNAT_COMMENT" "$tmp"; then
      grep -v -- "$MANAGED_SNAT_COMMENT" "$tmp" | iptables-restore >/dev/null 2>&1 ||
        echo "Warning: failed to rebuild NAT table while cleaning managed SNAT rules." >&2
    fi
  else
    echo "Warning: failed to read NAT table; managed SNAT cleanup was skipped." >&2
  fi
  rm -f "$tmp"
}

cleanup_ingress_dnat_rules() {
  local tmp
  tmp="$(mktemp)"
  if iptables-save -t nat >"$tmp" 2>/dev/null; then
    if grep -q -- "$INGRESS_DNAT_COMMENT" "$tmp"; then
      grep -v -- "$INGRESS_DNAT_COMMENT" "$tmp" | iptables-restore >/dev/null 2>&1 ||
        echo "Warning: failed to rebuild NAT table while cleaning ingress DNAT rules." >&2
    fi
  else
    echo "Warning: failed to read NAT table; ingress DNAT cleanup was skipped." >&2
  fi
  rm -f "$tmp"
}

tsv_field() {
  local line="$1"
  local field="$2"
  awk -F '\t' -v field="$field" '{print $field; exit}' <<<"$line"
}

add_snat_scope() {
  local scope="$1"
  local egress_ip="$2"
  local src
  [ -n "$scope" ] && [ "$scope" != "*" ] && [ -n "$egress_ip" ] || return 0

  if [[ "$scope" == *-* ]]; then
    if ! iptables -t nat -C POSTROUTING -m iprange --src-range "$scope" -o "$VPN_IFACE" -m comment --comment "$MANAGED_SNAT_COMMENT" -j SNAT --to-source "$egress_ip" >/dev/null 2>&1; then
      iptables -t nat -I POSTROUTING 1 -m iprange --src-range "$scope" -o "$VPN_IFACE" -m comment --comment "$MANAGED_SNAT_COMMENT" -j SNAT --to-source "$egress_ip" >/dev/null 2>&1 ||
        iptables -t nat -C POSTROUTING -m iprange --src-range "$scope" -o "$VPN_IFACE" -j SNAT --to-source "$egress_ip" >/dev/null 2>&1 ||
        iptables -t nat -I POSTROUTING 1 -m iprange --src-range "$scope" -o "$VPN_IFACE" -j SNAT --to-source "$egress_ip" >/dev/null 2>&1 ||
        echo "Warning: failed to add persistent SNAT range $scope -> $egress_ip. PPP online hook will still add per-session SNAT." >&2
    fi
    return 0
  fi

  case "$scope" in
    */*) src="$scope" ;;
    *) src="$scope/32" ;;
  esac
  if ! iptables -t nat -C POSTROUTING -s "$src" -o "$VPN_IFACE" -m comment --comment "$MANAGED_SNAT_COMMENT" -j SNAT --to-source "$egress_ip" >/dev/null 2>&1; then
    iptables -t nat -I POSTROUTING 1 -s "$src" -o "$VPN_IFACE" -m comment --comment "$MANAGED_SNAT_COMMENT" -j SNAT --to-source "$egress_ip" >/dev/null 2>&1 ||
      iptables -t nat -C POSTROUTING -s "$src" -o "$VPN_IFACE" -j SNAT --to-source "$egress_ip" >/dev/null 2>&1 ||
      iptables -t nat -I POSTROUTING 1 -s "$src" -o "$VPN_IFACE" -j SNAT --to-source "$egress_ip" >/dev/null 2>&1 ||
      echo "Warning: failed to add persistent SNAT $src -> $egress_ip. PPP online hook will still add per-session SNAT." >&2
  fi
}

for vip in "${VIP_ITEMS[@]}"; do
  [ -n "$vip" ] || continue
  case "$vip" in
    */*) vip_with_prefix="$vip" ;;
    *) vip_with_prefix="$vip/32" ;;
  esac
  vip_addr="${vip_with_prefix%%/*}"
  if ! ip -4 addr show dev "$VPN_IFACE" | grep -qw "$vip_addr"; then
    ip addr add "$vip_with_prefix" dev "$VPN_IFACE" || true
  fi
done

cleanup_managed_snat_rules
cleanup_ingress_dnat_rules

if [ "${VPN_INGRESS_MODE:-smart}" = "smart" ] && [ -n "${VPN_INGRESS_IP:-}" ]; then
  for ingress_ip in "${INGRESS_DNAT_IPS[@]}"; do
    [ -n "$ingress_ip" ] || continue
    [ "$ingress_ip" = "$VPN_INGRESS_IP" ] && continue
    if ! iptables -t nat -C PREROUTING -p udp -d "$ingress_ip" --dport "$VPN_L2TP_PORT" -m comment --comment "$INGRESS_DNAT_COMMENT" -j DNAT --to-destination "$VPN_INGRESS_IP:$VPN_L2TP_PORT" >/dev/null 2>&1; then
      iptables -t nat -I PREROUTING 1 -p udp -d "$ingress_ip" --dport "$VPN_L2TP_PORT" -m comment --comment "$INGRESS_DNAT_COMMENT" -j DNAT --to-destination "$VPN_INGRESS_IP:$VPN_L2TP_PORT" >/dev/null 2>&1 ||
        echo "Warning: failed to add ingress DNAT $ingress_ip:$VPN_L2TP_PORT -> $VPN_INGRESS_IP:$VPN_L2TP_PORT." >&2
    fi
  done
fi

for map in "${SNAT_MAP[@]}"; do
  [ -n "$map" ] || continue
  client_ip="${map%%=*}"
  egress_ip="${map#*=}"
  add_snat_scope "$client_ip" "$egress_ip"
done

if [ -r "$USER_MAP_PATH" ]; then
  while IFS= read -r line; do
    line_user="$(tsv_field "$line" 1)"
    egress_ip="$(tsv_field "$line" 2)"
    client_scope="$(tsv_field "$line" 5)"
    [ "$line_user" = "# username" ] && continue
    [ -n "$line_user" ] || continue
    add_snat_scope "$client_scope" "$egress_ip"
  done <"$USER_MAP_PATH"
fi

if [ "$VPN_DISABLE_DEFAULT_MASQ" != "1" ]; then
  iptables -t nat -C POSTROUTING -s "$VPN_CIDR" -o "$VPN_IFACE" -j MASQUERADE >/dev/null 2>&1 ||
    iptables -t nat -A POSTROUTING -s "$VPN_CIDR" -o "$VPN_IFACE" -j MASQUERADE
fi

if [ "$VPN_ENABLE_IPSEC" = "1" ]; then
  iptables -C INPUT -p udp -m multiport --dports "500,4500,$VPN_L2TP_PORT" -j ACCEPT >/dev/null 2>&1 ||
    iptables -I INPUT 1 -p udp -m multiport --dports "500,4500,$VPN_L2TP_PORT" -j ACCEPT
  iptables -C INPUT -p esp -j ACCEPT >/dev/null 2>&1 ||
    iptables -I INPUT 1 -p esp -j ACCEPT
else
  iptables -D INPUT -p udp -m multiport --dports "500,4500,$VPN_L2TP_PORT" -j ACCEPT >/dev/null 2>&1 || true
  iptables -D INPUT -p udp -m multiport --dports 500,4500,1701 -j ACCEPT >/dev/null 2>&1 || true
  iptables -D INPUT -p esp -j ACCEPT >/dev/null 2>&1 || true
  iptables -C INPUT -p udp --dport "$VPN_L2TP_PORT" -j ACCEPT >/dev/null 2>&1 ||
    iptables -I INPUT 1 -p udp --dport "$VPN_L2TP_PORT" -j ACCEPT
fi
iptables -C FORWARD -s "$VPN_CIDR" -o "$VPN_IFACE" -j ACCEPT >/dev/null 2>&1 ||
  iptables -I FORWARD 1 -s "$VPN_CIDR" -o "$VPN_IFACE" -j ACCEPT
iptables -C FORWARD -d "$VPN_CIDR" -i "$VPN_IFACE" -j ACCEPT >/dev/null 2>&1 ||
  iptables -I FORWARD 1 -d "$VPN_CIDR" -i "$VPN_IFACE" -j ACCEPT
EOF
  } >"$HELPER_PATH"
  chmod 700 "$HELPER_PATH"

  cat >"/etc/systemd/system/$HELPER_SERVICE" <<EOF
[Unit]
Description=Apply L2TP VIP and per-user egress rules
Wants=network-online.target
After=network-online.target firewalld.service
Before=strongswan.service strongswan-starter.service xl2tpd.service $XL2TPD_MULTI_SERVICE

[Service]
Type=oneshot
ExecStart=$HELPER_PATH
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
}

restart_service_any() {
  local svc
  for svc in "$@"; do
    if systemctl cat "$svc.service" >/dev/null 2>&1; then
      systemctl enable "$svc" >/dev/null 2>&1 || true
      systemctl restart "$svc"
      printf '%s\n' "$svc"
      return 0
    fi
  done
  return 1
}

disconnect_l2tp_sessions() {
  local reason="${1:-configuration reload}"
  local session_dir="/run/l2tp-vpn-sessions"
  local session_file iface session_line remote_ip egress_ip

  log "Disconnecting active L2TP sessions for $reason..."
  if [ -d "$session_dir" ]; then
    for session_file in "$session_dir"/*; do
      [ -f "$session_file" ] || continue
      iface="$(basename "$session_file")"
      session_line="$(cat "$session_file" 2>/dev/null || true)"
      remote_ip="$(tsv_field "$session_line" 2)"
      egress_ip="$(tsv_field "$session_line" 3)"
      if [ -n "${remote_ip:-}" ] && [ -n "${egress_ip:-}" ] && [ -n "${VPN_IFACE:-}" ]; then
        while iptables -t nat -D POSTROUTING -s "$remote_ip/32" -o "$VPN_IFACE" -j SNAT --to-source "$egress_ip" >/dev/null 2>&1; do
          :
        done
      fi
      ip link set "$iface" down >/dev/null 2>&1 || true
    done
    rm -f "$session_dir"/* 2>/dev/null || true
  fi

  while IFS= read -r iface; do
    [ -n "$iface" ] || continue
    ip link set "$iface" down >/dev/null 2>&1 || true
  done < <(ip -o link show 2>/dev/null | awk -F': ' '$2 ~ /^ppp[0-9]+$/ {print $2}')
}

restart_services() {
  log "Starting services..."
  systemctl daemon-reload
  systemctl enable "$HELPER_SERVICE" >/dev/null 2>&1 || true
  systemctl enable --now "$APPLY_USERS_PATH" >/dev/null 2>&1 || true
  systemctl restart "$HELPER_SERVICE"

  if [ "$VPN_ENABLE_IPSEC" = "1" ]; then
    IPSEC_SERVICE="$(restart_service_any strongswan-starter strongswan ipsec || true)"
    if [ -z "${IPSEC_SERVICE:-}" ] && command -v ipsec >/dev/null 2>&1; then
      ipsec restart
      IPSEC_SERVICE="ipsec"
    fi
    [ -n "${IPSEC_SERVICE:-}" ] || fail "Could not restart strongSwan service."
  else
    IPSEC_SERVICE="disabled"
    systemctl stop strongswan-starter strongswan ipsec >/dev/null 2>&1 || true
    systemctl disable strongswan-starter strongswan ipsec >/dev/null 2>&1 || true
  fi

  disconnect_l2tp_sessions "configuration apply"

  collect_l2tp_bind_ips
  if [ "${#L2TP_BIND_IPS[@]}" -gt 1 ]; then
    systemctl stop xl2tpd >/dev/null 2>&1 || true
    systemctl disable xl2tpd >/dev/null 2>&1 || true
    systemctl enable "$XL2TPD_MULTI_SERVICE" >/dev/null 2>&1 || true
    systemctl restart "$XL2TPD_MULTI_SERVICE"
    XL2TPD_SERVICE="$XL2TPD_MULTI_SERVICE"
  else
    systemctl stop "$XL2TPD_MULTI_SERVICE" >/dev/null 2>&1 || true
    systemctl disable "$XL2TPD_MULTI_SERVICE" >/dev/null 2>&1 || true
    XL2TPD_SERVICE="$(restart_service_any xl2tpd || true)"
    [ -n "${XL2TPD_SERVICE:-}" ] || fail "Could not restart xl2tpd service."
  fi
}

write_credentials_summary() {
  {
    echo "L2TP/IPsec VPN credentials"
    echo "Generated at: $(date -Is)"
    echo
    echo "Server address: use the Tianyi Cloud public EIP that maps to this server/VIP"
    if [ "$VPN_ENABLE_IPSEC" = "1" ]; then
      echo "IPsec PSK: $VPN_IPSEC_PSK"
    else
      echo "IPsec PSK: disabled"
      echo "VPN mode: L2TP username/password only"
    fi
    echo
    echo "Users:"
    local i
    for i in "${!USER_NAMES[@]}"; do
      echo "  - username: ${USER_NAMES[$i]}"
      echo "    password: ${USER_PASSWORDS[$i]}"
      echo "    vpn_ip: ${USER_CLIENT_IPS[$i]}"
      echo "    egress_local_vip: ${USER_EGRESS_IPS[$i]:-default}"
      echo "    public_eip_label: ${USER_PUBLIC_LABELS[$i]:-default}"
      echo "    share_count: ${USER_SHARE_COUNTS[$i]}"
    done
    echo
    echo "Editable user config:"
    echo "  $USER_CONFIG_FILE"
    echo "Editable server config:"
    echo "  $SERVER_CONFIG_FILE"
    echo "Apply config command:"
    echo "  $APPLY_CONFIG_SCRIPT"
    echo
    echo "Cloud security group:"
    if [ "$VPN_ENABLE_IPSEC" = "1" ]; then
      echo "  open UDP 500, UDP 4500, UDP $VPN_L2TP_PORT, and ESP protocol 50."
    else
      echo "  open UDP $VPN_L2TP_PORT only."
    fi
    echo
    echo "PPP MTU/MRU:"
    echo "  MTU: $VPN_MTU"
    echo "  MRU: $VPN_MRU"
    echo
    echo "Windows client behind NAT or connecting to a NATed EIP may need:"
    echo "  reg add HKLM\\SYSTEM\\CurrentControlSet\\Services\\PolicyAgent /v AssumeUDPEncapsulationContextOnSendRule /t REG_DWORD /d 2 /f"
  } >"$CREDENTIAL_FILE"
  chmod 600 "$CREDENTIAL_FILE"
}

print_summary() {
  cat <<EOF

L2TP/IPsec installation completed.

Credential file:
  $CREDENTIAL_FILE

Editable user config:
  $USER_CONFIG_FILE

Editable server config:
  $SERVER_CONFIG_FILE

Apply config command:
  $APPLY_CONFIG_SCRIPT

IPsec:
  $(if [ "$VPN_ENABLE_IPSEC" = "1" ]; then printf 'enabled, PSK: %s' "$VPN_IPSEC_PSK"; else printf 'disabled, L2TP username/password only'; fi)

Users:
EOF
  local i
  for i in "${!USER_NAMES[@]}"; do
    cat <<EOF
  - ${USER_NAMES[$i]}
    password: ${USER_PASSWORDS[$i]}
    vpn_ip: ${USER_CLIENT_IPS[$i]}
    egress_local_vip: ${USER_EGRESS_IPS[$i]:-default}
    public_eip_label: ${USER_PUBLIC_LABELS[$i]:-default}
    share_count: ${USER_SHARE_COUNTS[$i]}
EOF
  done
  cat <<EOF

Tianyi Cloud security group must allow:
  $(if [ "$VPN_ENABLE_IPSEC" = "1" ]; then printf 'UDP 500, UDP 4500, UDP %s, ESP protocol 50' "$VPN_L2TP_PORT"; else printf 'UDP %s only' "$VPN_L2TP_PORT"; fi)

Service checks:
$(if [ "$VPN_ENABLE_IPSEC" = "1" ]; then printf '  systemctl status %s\n' "${IPSEC_SERVICE:-strongswan-starter}"; fi)
  systemctl status ${XL2TPD_SERVICE:-xl2tpd}
  systemctl status $HELPER_SERVICE

Logs:
$(if [ "$VPN_ENABLE_IPSEC" = "1" ]; then printf '  journalctl -u %s -f\n' "${IPSEC_SERVICE:-strongswan-starter}"; fi)
  journalctl -u xl2tpd -f
EOF
}

parse_users() {
  USER_NAMES=()
  USER_PASSWORDS=()
  USER_CLIENT_IPS=()
  USER_CHAP_IPS=()
  USER_EGRESS_IPS=()
  USER_PUBLIC_LABELS=()
  USER_SHARE_COUNTS=()
  SNAT_MAP=()
  VIP_ITEMS=()

  local raw_vips vip
  raw_vips="${VPN_VIPS:-}"
  if [ -n "$raw_vips" ]; then
    IFS=',' read -r -a vip_parts <<<"$raw_vips"
    for vip in "${vip_parts[@]}"; do
      [ -n "$vip" ] || continue
      VIP_ITEMS+=("$vip")
    done
  fi

  USER_ENTRIES=()
  if [ -n "${VPN_USERS:-}" ]; then
    IFS=',' read -r -a USER_ENTRIES <<<"$VPN_USERS"
  elif [ -f "$USER_CONFIG_FILE" ]; then
    local line cleaned
    while IFS= read -r line || [ -n "$line" ]; do
      cleaned="${line%%#*}"
      cleaned="$(printf '%s' "$cleaned" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
      [ -n "$cleaned" ] || continue
      USER_ENTRIES+=("$cleaned")
    done <"$USER_CONFIG_FILE"
  else
    VPN_USERS="${VPN_USER:-vpnuser}:${VPN_PASSWORD:-$(random_hex 8)}:${VPN_EGRESS_IP:-}:${VPN_SHARE_COUNT:-${VPN_DEFAULT_SHARE_COUNT:-1}}:${VPN_CLIENT_IP:-}:${VPN_PUBLIC_IP_LABEL:-}"
    IFS=',' read -r -a USER_ENTRIES <<<"$VPN_USERS"
  fi

  local entries entry username password egress_ip share_count public_label client_ip extra seen_users dynamic_share_total pool_capacity scope_capacity
  seen_users=""
  dynamic_share_total=0
  AUTO_CLIENT_NEXT=""
  entries=("${USER_ENTRIES[@]}")
  for entry in "${entries[@]}"; do
    [ -n "$entry" ] || continue
    if [[ "$entry" == *","* ]]; then
      IFS=',' read -r username password egress_ip share_count client_ip public_label extra <<<"$entry"
    else
      IFS=':' read -r username password egress_ip share_count client_ip public_label extra <<<"$entry"
    fi

    [ -z "${extra:-}" ] || fail "Invalid VPN_USERS entry '$entry': too many ':' fields."
    [ -n "${username:-}" ] || fail "VPN username cannot be empty."
    [ -n "${password:-}" ] || fail "VPN password cannot be empty for user '$username'."

    case "$username" in
      *[!A-Za-z0-9_.@-]*)
        fail "VPN username '$username' contains unsupported characters. Use A-Z, a-z, 0-9, _, ., @, -."
        ;;
    esac

    case ",$seen_users," in
      *,"$username",*) fail "Duplicate VPN username '$username' in VPN_USERS." ;;
    esac
    seen_users="${seen_users},${username}"

    share_count="${share_count:-${VPN_DEFAULT_SHARE_COUNT:-1}}"
    validate_number_between "share_count for $username" "$share_count" 1 999
    if [ -z "${client_ip:-}" ]; then
      client_ip="*"
    fi
    client_ip_scope_capacity "$client_ip" >/dev/null
    validate_client_scope_inside_pool "$client_ip"
    scope_capacity="$(client_ip_scope_capacity "$client_ip")"
    if [ "$share_count" -gt "$scope_capacity" ]; then
      fail "User '$username' has share_count=$share_count but vpn_ip '$client_ip' only has $scope_capacity address(es). Use a larger CIDR/range or reduce share_count."
    fi
    if [ "$client_ip" = "*" ] || is_ipv4_cidr "$client_ip" || is_ipv4_range "$client_ip"; then
      dynamic_share_total=$((dynamic_share_total + share_count))
    fi

    if [ -n "${egress_ip:-}" ]; then
      is_ipv4 "$egress_ip" || fail "Invalid egress local VIP '$egress_ip' for user '$username'."
      if [ "$client_ip" != "*" ]; then
        SNAT_MAP+=("$client_ip=$egress_ip")
      fi
      if [ "${VPN_ADD_EGRESS_AS_VIP:-0}" = "1" ]; then
        VIP_ITEMS+=("$egress_ip/32")
      fi
    fi

    USER_NAMES+=("$username")
    USER_PASSWORDS+=("$password")
    USER_CLIENT_IPS+=("$client_ip")
    USER_CHAP_IPS+=("$(chap_ip_value_for_scope "$client_ip")")
    USER_EGRESS_IPS+=("${egress_ip:-}")
    USER_PUBLIC_LABELS+=("${public_label:-}")
    USER_SHARE_COUNTS+=("$share_count")
  done

  [ "${#USER_NAMES[@]}" -gt 0 ] || fail "No VPN users configured."
  pool_capacity="$(vpn_client_pool_capacity)"
  if [ "$dynamic_share_total" -gt "$pool_capacity" ]; then
    fail "VPN_CLIENT_POOL has $pool_capacity dynamic addresses, but configured dynamic share_count total is $dynamic_share_total. Enlarge VPN_CLIENT_POOL/VPN_CIDR or reduce share_count."
  fi
}

main() {
  case "${1:-}" in
    -h|--help)
      usage
      exit 0
      ;;
  esac

  need_root
  need_systemd
  persist_installer_script

  local env_overrides
  env_overrides="$(mktemp)"
  write_env_overrides_file "$env_overrides"
  load_server_config
  if [ -s "$env_overrides" ]; then
    # shellcheck disable=SC1090
    source "$env_overrides"
  fi
  rm -f "$env_overrides"

  VPN_LOCAL_IP="${VPN_LOCAL_IP:-172.18.0.1}"
  VPN_CLIENT_POOL="${VPN_CLIENT_POOL:-172.18.0.2-172.18.255.254}"
  VPN_CIDR="${VPN_CIDR:-172.18.0.0/16}"
  VPN_L2TP_PORT="${VPN_L2TP_PORT:-1701}"
  VPN_DNS1="${VPN_DNS1:-223.5.5.5}"
  VPN_DNS2="${VPN_DNS2:-119.29.29.29}"
  VPN_MTU="${VPN_MTU:-1280}"
  VPN_MRU="${VPN_MRU:-$VPN_MTU}"
  VPN_PRIMARY_IP="${VPN_PRIMARY_IP:-}"
  VPN_VIPS="${VPN_VIPS:-}"
  VPN_PLATFORM_SCAN="${VPN_PLATFORM_SCAN:-0}"
  VPN_VIP_CANDIDATES="${VPN_VIP_CANDIDATES:-}"
  VPN_VIP_SCAN_RANGE="${VPN_VIP_SCAN_RANGE:-}"
  VPN_VIP_SCAN_MAX="${VPN_VIP_SCAN_MAX:-512}"
  VPN_VIP_SCAN_PARALLEL="${VPN_VIP_SCAN_PARALLEL:-32}"
  VPN_VIP_PROBE_TARGET="${VPN_VIP_PROBE_TARGET:-www.baidu.com}"
  VPN_INGRESS_MODE="${VPN_INGRESS_MODE:-smart}"
  VPN_CLIENT_POOL_MODE="${VPN_CLIENT_POOL_MODE:-auto}"
  VPN_AUTO_CONFIG_FROM_USERS="${VPN_AUTO_CONFIG_FROM_USERS:-0}"
  VPN_ENABLE_BBR="${VPN_ENABLE_BBR:-1}"

  prompt_interactive_config

  validate_number_between VPN_MTU "$VPN_MTU" 576 1500
  validate_number_between VPN_MRU "$VPN_MRU" 576 1500
  validate_number_between VPN_L2TP_PORT "$VPN_L2TP_PORT" 1 65535
  case "$VPN_INGRESS_MODE" in
    smart|bound) ;;
    *) fail "VPN_INGRESS_MODE must be smart or bound." ;;
  esac
  case "$VPN_CLIENT_POOL_MODE" in
    auto|global|per_vip) ;;
    *) fail "VPN_CLIENT_POOL_MODE must be auto, global, or per_vip." ;;
  esac
  case "$VPN_ENABLE_BBR" in
    0|1) ;;
    *) fail "VPN_ENABLE_BBR must be 0 or 1." ;;
  esac
  case "$VPN_AUTO_CONFIG_FROM_USERS" in
    0|1) ;;
    *) fail "VPN_AUTO_CONFIG_FROM_USERS must be 0 or 1." ;;
  esac
  validate_number_between VPN_VIP_SCAN_MAX "$VPN_VIP_SCAN_MAX" 1 4096
  validate_number_between VPN_VIP_SCAN_PARALLEL "$VPN_VIP_SCAN_PARALLEL" 1 128
  is_ipv4 "$VPN_LOCAL_IP" || fail "Invalid VPN_LOCAL_IP '$VPN_LOCAL_IP'."
  validate_vpn_network_layout
  resolve_ipsec_settings

  parse_users
  write_user_editable_config

  if [ "${VPN_APPLY_ONLY:-0}" = "1" ]; then
    log "Apply-only mode: skipping package installation."
  else
    install_packages
  fi

  command -v ip >/dev/null 2>&1 || fail "ip command was not found."
  VPN_IFACE="${VPN_IFACE:-$(detect_iface)}"
  VPN_IFACE_IPV4S="$(detect_iface_ipv4s "$VPN_IFACE" "$VPN_PRIMARY_IP")"
  verify_platform_vip_candidates
  auto_config_from_users
  resolve_ingress_ip
  VPN_INGRESS_IP="$L2TP_INGRESS_IP"
  write_server_config
  write_apply_config_script

  log "Using interface: $VPN_IFACE"
  log "Users: ${#USER_NAMES[@]}"
  if [ "${#SNAT_MAP[@]}" -gt 0 ]; then
    log "Per-user egress mappings: ${#SNAT_MAP[@]}"
  fi

  configure_bbr
  configure_sysctl
  if [ "$VPN_ENABLE_IPSEC" = "1" ]; then
    configure_ipsec
  else
    log "IPsec is disabled; installing L2TP username/password authentication only."
  fi
  configure_xl2tpd
  write_chap_users
  write_user_runtime_config
  write_ppp_hooks
  write_apply_users_script
  configure_firewalld_or_ufw
  write_helper_script
  restart_services
  write_credentials_summary
  print_summary
}

main "$@"

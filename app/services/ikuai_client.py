import hashlib
import json
import ssl
import time
from http.cookiejar import CookieJar
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import HTTPCookieProcessor, HTTPSHandler, Request, build_opener


class IkuaiClientError(RuntimeError):
    pass


class IkuaiClient:
    def __init__(self, base_url: str, username: str, password: str, timeout: int = 15) -> None:
        self.base_url = normalize_base_url(base_url)
        self.username = username
        self.password = password
        self.timeout = timeout
        self._last_login = 0.0
        self._cookie_jar = CookieJar()
        context = ssl._create_unverified_context()
        self._opener = build_opener(HTTPCookieProcessor(self._cookie_jar), HTTPSHandler(context=context))

    def login(self) -> dict[str, Any]:
        passwd = hashlib.md5((self.password or "").encode("utf-8")).hexdigest()
        data = self._post_json("/Action/login", {
            "username": self.username,
            "passwd": passwd,
            "pass": passwd,
            "remember_password": "",
        }, ensure_session=False)
        if data.get("Result") in {10000, 30000} or data.get("code") == 0:
            self._last_login = time.time()
            return data
        raise IkuaiClientError(data.get("ErrMsg") or data.get("message") or "爱快登录失败")

    def call(self, func_name: str, action: str = "show", param: dict[str, Any] | None = None) -> dict[str, Any]:
        if time.time() - self._last_login > 600:
            self.login()
        payload: dict[str, Any] = {"func_name": func_name, "action": action}
        if param:
            payload["param"] = param
        data = self._post_json("/Action/call", payload, ensure_session=True)
        if data.get("code") in {1003, 10014} or data.get("Result") in {10014}:
            self.login()
            data = self._post_json("/Action/call", payload, ensure_session=True)
        if data.get("code") not in {0, None}:
            raise IkuaiClientError(data.get("message") or data.get("ErrMsg") or f"爱快接口错误：{data.get('code')}")
        if data.get("Result") not in {None, 30000, 10000}:
            raise IkuaiClientError(data.get("ErrMsg") or data.get("message") or f"爱快接口错误：{data.get('Result')}")
        return data.get("results") if isinstance(data.get("results"), dict) else data

    def _post_json(self, path: str, payload: dict[str, Any], ensure_session: bool) -> dict[str, Any]:
        url = urljoin(self.base_url + "/", path.lstrip("/"))
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            url,
            data=body,
            headers={
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Content-Type": "application/json;charset=UTF-8",
                "User-Agent": "ctyun-manager/ikuai",
                "X-Requested-With": "XMLHttpRequest",
            },
            method="POST",
        )
        try:
            response = self._opener.open(request, timeout=self.timeout)
            text = response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise IkuaiClientError(f"爱快 HTTP {exc.code}：{detail}") from exc
        except URLError as exc:
            raise IkuaiClientError(f"无法连接爱快网关：{exc.reason}") from exc
        except TimeoutError as exc:
            raise IkuaiClientError("连接爱快网关超时") from exc
        try:
            return json.loads(text or "{}")
        except json.JSONDecodeError as exc:
            raise IkuaiClientError(f"爱快返回不是 JSON：{text[:200]}") from exc


def normalize_base_url(value: str) -> str:
    url = (value or "").strip().rstrip("/")
    if not url:
        raise IkuaiClientError("请填写爱快 Web 地址")
    if "://" not in url:
        url = "http://" + url
    return url


def gateway_summary(client: IkuaiClient) -> dict[str, Any]:
    sysstat_data = _ikuai_data(client.call("homepage", "show", {"TYPE": "sysstat"}))
    wan_data = _ikuai_data(client.call("homepage", "show", {"TYPE": "wan_stat", "ifname": "adsl1", "interface": "adsl1"}))
    sysstat = sysstat_data.get("sysstat") if isinstance(sysstat_data.get("sysstat"), dict) else sysstat_data
    wan = wan_data.get("wan_stat") if isinstance(wan_data.get("wan_stat"), dict) else wan_data
    if isinstance(sysstat.get("data"), list) and sysstat["data"]:
        sysstat = sysstat["data"][0]
    if isinstance(wan.get("data"), list) and wan["data"]:
        wan = wan["data"][0]
    online = sysstat.get("online_user") or {}
    stream = sysstat.get("stream") or {}
    verinfo = sysstat.get("verinfo") or {}
    return {
        "hostname": sysstat.get("hostname") or "",
        "ip_addr": sysstat.get("ip_addr") or "",
        "version": verinfo.get("verstring") or "",
        "uptime": sysstat.get("uptime") or 0,
        "cpu": sysstat.get("cpu") or [],
        "memory": sysstat.get("memory") or {},
        "online": online,
        "stream": stream,
        "wan": wan,
    }


def _ikuai_data(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("Data", "data", "results", "ResultData"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    return payload


IKUAI_MENU_GROUPS: list[dict[str, Any]] = [
    {
        "id": "status",
        "label": "状态监控",
        "items": [
            {"id": "homepage", "label": "系统概况", "func_name": "homepage", "param": {"TYPE": "sysstat"}},
            {"id": "monitor_lanip", "label": "在线终端", "func_name": "monitor_lanip", "param": {"TYPE": "data,total", "limit": "0,500", "ORDER_BY": "ip_addr_int", "ORDER": "asc"}},
            {"id": "monitor_iface", "label": "接口状态", "func_name": "monitor_iface", "param": {"TYPE": "iface_check,iface_stream"}},
            {"id": "monitor_wan", "label": "线路监控", "func_name": "monitor_iface", "param": {"TYPE": "all"}},
            {"id": "monitor_flow", "label": "流量监控", "func_name": "monitor_iface", "param": {"TYPE": "iface_stream"}},
        ],
    },
    {
        "id": "system",
        "label": "系统设置",
        "items": [
            {"id": "sys_config", "label": "基础设置", "func_name": "homepage", "param": {"TYPE": "sysstat"}},
            {"id": "login_config", "label": "登录管理", "func_name": "webuser", "param": {"TYPE": "all"}},
            {"id": "time_config", "label": "时间设置", "func_name": "homepage", "param": {"TYPE": "sysstat"}},
            {"id": "backup_config", "label": "备份恢复", "func_name": "backup", "param": {"TYPE": "all"}},
            {"id": "sys_upgrade", "label": "系统升级", "func_name": "upgrade", "param": {"TYPE": "all"}},
            {"id": "reboot", "label": "重启关机", "func_name": "homepage", "param": {"TYPE": "sysstat"}},
        ],
    },
    {
        "id": "network",
        "label": "网络设置",
        "items": [
            {"id": "wan", "label": "内外网设置 / 外网设置", "func_name": "wan", "param": {"TYPE": "total,data", "limit": "0,500"}},
            {"id": "lan", "label": "内外网设置 / 内网设置", "func_name": "lan", "param": {"TYPE": "all"}},
            {"id": "wifi", "label": "WiFi设置", "local_page": "network_wifi"},
            {"id": "mesh", "label": "Mesh快连", "local_page": "network_mesh"},
            {"id": "dhcp_server", "label": "DHCP设置 / DHCP服务端", "func_name": "dhcp_server", "param": {"TYPE": "total,data", "limit": "0,500"}},
            {"id": "dhcp_addr_bind", "label": "DHCP设置 / DHCP静态分配", "calls": [
                {"func_name": "dhcp_addr_bind", "param": {"TYPE": "total,data", "limit": "0,500"}},
                {"func_name": "dhcp_static", "param": {"TYPE": "total,data", "limit": "0,500"}},
            ]},
            {"id": "dhcp_lease", "label": "DHCP设置 / DHCP终端列表", "func_name": "dhcp_lease", "param": {"TYPE": "total,data", "limit": "0,500"}},
            {"id": "dns", "label": "DNS设置 / DNS设置", "func_name": "dns", "param": {"TYPE": "all"}},
            {"id": "multi_dns", "label": "DNS设置 / 多线路DNS", "local_page": "network_multi_dns"},
            {"id": "ipgroup", "label": "终端分组设置 / IP分组", "func_name": "ipgroup", "param": {"TYPE": "total,data", "limit": "0,500"}},
            {"id": "macgroup", "label": "终端分组设置 / MAC分组", "func_name": "macgroup", "param": {"TYPE": "total,data", "limit": "0,500"}},
            {"id": "route_static", "label": "静态路由 / 静态路由", "local_page": "network_route_static"},
            {"id": "route_table", "label": "静态路由 / 当前路由表", "local_page": "network_route_table"},
            {"id": "vlan", "label": "VLAN 设置", "func_name": "vlan", "param": {"TYPE": "total,data", "limit": "0,500"}},
            {"id": "vpn_pptp_client", "label": "VPN客户端 / PPTP客户端", "func_name": "pptp_client", "param": {"TYPE": "total,data", "limit": "0,500"}},
            {"id": "vpn_l2tp_client", "label": "VPN客户端 / L2TP客户端", "func_name": "l2tp_client", "param": {"TYPE": "total,data", "limit": "0,500"}},
            {"id": "vpn_openvpn_client", "label": "VPN客户端 / OpenVPN客户端", "local_page": "network_openvpn_client"},
            {"id": "upnp", "label": "UPnP设置 / UPnP设置", "func_name": "upnpd", "param": {"TYPE": "all"}},
            {"id": "upnp_status", "label": "UPnP设置 / UPnP状态", "func_name": "upnpd", "param": {"TYPE": "ifconf_data,ifconf_total"}},
            {"id": "nat_rule", "label": "NAT规则", "func_name": "nat_rule", "param": {"TYPE": "total,data", "limit": "0,500"}},
            {"id": "port_mapping", "label": "端口映射", "calls": [
                {"func_name": "port_mapping", "param": {"TYPE": "total,data", "limit": "0,500"}},
                {"func_name": "nat_rule", "param": {"TYPE": "total,data", "limit": "0,500"}},
            ]},
            {"id": "ipv6", "label": "IPV6", "func_name": "ipv6", "param": {"TYPE": "all"}},
            {"id": "igmp_proxy", "label": "IGMP代理", "func_name": "igmp_proxy", "param": {"TYPE": "all"}},
            {"id": "iptv", "label": "IPTV透传", "func_name": "iptv", "param": {"TYPE": "all"}},
        ],
    },
    {
        "id": "qos",
        "label": "流控分流",
        "items": [
            {"id": "flow_control", "label": "智能流控", "calls": [
                {"func_name": "flow_control", "param": {"TYPE": "total,data", "limit": "0,500"}},
                {"func_name": "simple_qos", "param": {"TYPE": "total,data", "limit": "0,500"}},
            ]},
            {"id": "stream_ipport", "label": "端口分流", "func_name": "stream_ipport", "param": {"TYPE": "total,data", "limit": "0,500"}},
            {"id": "stream_domain", "label": "域名分流", "func_name": "stream_domain", "param": {"TYPE": "total,data", "limit": "0,500"}},
            {"id": "stream_app", "label": "应用分流", "func_name": "lb_pcc", "param": {"TYPE": "total,data", "limit": "0,500"}},
            {"id": "qos_user", "label": "终端限速", "func_name": "simple_qos", "param": {"TYPE": "total,data", "limit": "0,500"}},
        ],
    },
    {
        "id": "auth",
        "label": "认证计费",
        "items": [
            {"id": "auth_online", "label": "账号在线用户", "func_name": "ppp_online", "param": {"TYPE": "total,data", "limit": "0,500"}},
            {"id": "auth_web", "label": "WEB认证服务", "func_name": "webauth", "param": {"TYPE": "all"}},
            {"id": "auth_pppoe_server", "label": "本地认证服务 / PPPoE服务端", "func_name": "pppoe_server", "param": {"TYPE": "all"}},
            {"id": "auth_pptp_server", "label": "本地认证服务 / PPTP服务端", "func_name": "pptp_server", "param": {"TYPE": "all"}},
            {"id": "auth_l2tp_server", "label": "本地认证服务 / L2TP服务端", "func_name": "l2tp_server", "param": {"TYPE": "all"}},
            {"id": "auth_openvpn_server", "label": "本地认证服务 / OpenVPN服务端", "local_page": "auth_openvpn_server"},
            {"id": "auth_ikev2_server", "label": "本地认证服务 / IKEv2/IPSEC服务端", "local_page": "auth_ikev2_server"},
            {"id": "auth_package", "label": "认证账号管理 / 套餐管理", "func_name": "ppp_package", "param": {"TYPE": "total,data", "limit": "0,500"}},
            {"id": "auth_account", "label": "认证账号管理 / 账号管理", "func_name": "pppuser", "param": {"TYPE": "total,data", "limit": "0,500"}},
            {"id": "auth_self_password", "label": "认证账号管理 / 自助密码管理", "local_page": "auth_self_password"},
            {"id": "auth_ledger", "label": "认证账号管理 / 总账管理", "local_page": "auth_ledger"},
            {"id": "auth_code", "label": "认证账号管理 / 上网码", "local_page": "auth_code"},
            {"id": "auth_proxy", "label": "代拨服务管理", "func_name": "pppoe_server", "param": {"TYPE": "all"}},
            {"id": "auth_notice", "label": "推送通知", "func_name": "syslog-notice", "param": {"TYPE": "total,data", "ORDER": "desc", "ORDER_BY": "id", "limit": "0,500"}},
        ],
    },
    {
        "id": "behavior",
        "label": "行为管控",
        "items": [
            {"id": "url_filter", "label": "网址管控", "func_name": "domain_blacklist", "param": {"TYPE": "total,data", "limit": "0,500"}},
            {"id": "app_filter", "label": "应用管控", "func_name": "mac_app", "param": {"TYPE": "total,data", "limit": "0,500"}},
            {"id": "mac_filter", "label": "MAC 管控", "func_name": "acl_mac", "param": {"TYPE": "total,data", "limit": "0,500"}},
            {"id": "behavior_log", "label": "行为记录", "calls": [
                {"func_name": "audit_url_log", "param": {"TYPE": "data", "limit": "0,500", "ORDER_BY": "timestamp", "ORDER": "desc"}},
                {"func_name": "audit_terminal_log", "param": {"TYPE": "data", "limit": "0,500", "ORDER_BY": "timestamp", "ORDER": "desc"}},
            ]},
        ],
    },
    {
        "id": "security",
        "label": "安全设置",
        "items": [
            {"id": "acl", "label": "ACL 规则", "func_name": "acl", "param": {"TYPE": "total,data", "limit": "0,500"}},
            {"id": "firewall", "label": "防火墙", "calls": [
                {"func_name": "firewall", "param": {"TYPE": "status"}},
                {"func_name": "acl", "param": {"TYPE": "total,data", "limit": "0,500"}},
            ]},
            {"id": "attack_defense", "label": "攻击防御", "func_name": "syslog-arp", "param": {"TYPE": "total,data", "ORDER": "desc", "ORDER_BY": "id", "limit": "0,500"}},
            {"id": "blacklist", "label": "黑白名单", "func_name": "domain_blacklist", "param": {"TYPE": "total,data", "limit": "0,500"}},
        ],
    },
    {
        "id": "advanced",
        "label": "高级应用",
        "items": [
            {"id": "vpn", "label": "VPN", "calls": [
                {"func_name": "l2tp_server", "param": {"TYPE": "all"}},
                {"func_name": "pptp_server", "param": {"TYPE": "all"}},
            ]},
            {"id": "ddns", "label": "动态域名", "func_name": "ddns", "param": {"TYPE": "total,data", "limit": "0,500"}},
            {"id": "snmp", "label": "SNMP", "func_name": "homepage", "param": {"TYPE": "sysstat"}},
            {"id": "wakeup", "label": "网络唤醒", "func_name": "wakeup", "param": {"TYPE": "total,data", "limit": "0,500"}},
            {"id": "schedule", "label": "计划任务", "func_name": "backup", "param": {"TYPE": "all"}},
        ],
    },
    {
        "id": "tools",
        "label": "应用工具",
        "items": [
            {"id": "tool_ping", "label": "Ping", "local_page": "ping"},
            {"id": "tool_trace", "label": "路由追踪", "local_page": "trace"},
            {"id": "tool_nslookup", "label": "DNS 查询", "local_page": "nslookup"},
            {"id": "tool_packet", "label": "抓包工具", "local_page": "packet"},
        ],
    },
    {
        "id": "logs",
        "label": "日志中心",
        "items": [
            {"id": "syslog", "label": "系统日志", "func_name": "syslog-sysevent", "param": {"TYPE": "total,data", "ORDER": "desc", "ORDER_BY": "id", "limit": "0,500"}},
            {"id": "operation_log", "label": "操作日志", "func_name": "syslog-sysevent", "param": {"TYPE": "total,data", "ORDER": "desc", "ORDER_BY": "id", "limit": "0,500"}},
            {"id": "login_log", "label": "登录日志", "func_name": "syslog-pppauth", "param": {"TYPE": "total,data", "ORDER": "desc", "ORDER_BY": "id", "limit": "0,500"}},
            {"id": "auth_log", "label": "认证日志", "func_name": "syslog-pppauth", "param": {"TYPE": "total,data", "ORDER": "desc", "ORDER_BY": "id", "limit": "0,500"}},
        ],
    },
]

def _section_call_candidates(item: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    calls = item.get("calls") or [item]
    return [
        (call["func_name"], call.get("action", "show"), call.get("param", {}))
        for call in calls
        if call.get("func_name")
    ]


SECTION_CALLS: dict[str, list[tuple[str, str, dict[str, Any]]]] = {
    item["id"]: _section_call_candidates(item)
    for group in IKUAI_MENU_GROUPS
    for item in group["items"]
}

IKUAI_READONLY_SECTIONS = {
    "homepage",
    "monitor_lanip",
    "monitor_iface",
    "monitor_wan",
    "monitor_flow",
    "dhcp_lease",
    "upnp_status",
    "auth_online",
    "auth_notice",
    "behavior_log",
    "attack_defense",
    "syslog",
    "operation_log",
    "login_log",
    "auth_log",
    "reboot",
}


AUTH_ACCOUNT_FIELDS = {
    "id",
    "enabled",
    "username",
    "passwd",
    "duration",
    "expires",
    "start_time",
    "ppptype",
    "share",
    "upload",
    "download",
    "ip_type",
    "ip_addr",
    "mac",
    "auto_mac",
    "bind_vlanid",
    "auto_vlanid",
    "bind_ifname",
    "address",
    "name",
    "phone",
    "packages",
    "proxy_username",
    "pppoev6_wan",
    "comment",
}
AUTH_PACKAGE_FIELDS = {
    "id",
    "enabled",
    "name",
    "packname",
    "package_name",
    "duration",
    "expire_type",
    "cycle_type",
    "price",
    "money",
    "upload",
    "download",
    "comment",
}
AUTH_SERVICE_FIELDS = {
    "id",
    "enabled",
    "server_name",
    "server_ip",
    "dns1",
    "dns2",
    "authmode",
    "nas_identifier",
    "nas_ip_address",
    "radius_ip",
    "secret",
    "authport",
    "accountport",
    "addr_pool",
    "interface",
    "rate_limit_lan",
    "drop_client",
    "force_pppoe",
    "force_verify_name",
    "enhance_check",
    "share_deny",
    "bind_vlan",
    "verify_vlan",
    "bind_iface",
    "mtu",
    "mru",
    "lcp_echo_interval",
    "lcp_echo_failure",
    "maxconnect",
    "restart_timer",
    "restart_week",
    "restart_time",
    "comment",
}
AUTH_WEB_FIELDS = {
    "id",
    "enabled",
    "interface",
    "authip_mode",
    "user_auth",
    "phone_auth",
    "static_pwd",
    "nopasswd",
    "coupon_auth",
    "weixin",
    "qq_auth",
    "weibo_auth",
    "allow_tryout",
    "tryout_time",
    "group_id",
    "group_key",
    "max_time",
    "idle_time",
}


IKUAI_SECTION_ACTIONS: dict[str, dict[str, dict[str, Any]]] = {
    "auth_account": {
        "create": {"func_name": "pppuser", "call_action": "add", "fields": AUTH_ACCOUNT_FIELDS, "required": {"username", "passwd"}},
        "update": {"func_name": "pppuser", "call_action": "edit", "fields": AUTH_ACCOUNT_FIELDS, "required": {"id"}},
        "delete": {"func_name": "pppuser", "call_action": "del", "fields": {"id"}, "required": {"id"}},
        "enable": {"func_name": "pppuser", "call_action": "edit", "fields": AUTH_ACCOUNT_FIELDS, "required": {"id"}, "inject": {"enabled": "yes"}},
        "disable": {"func_name": "pppuser", "call_action": "edit", "fields": AUTH_ACCOUNT_FIELDS, "required": {"id"}, "inject": {"enabled": "no"}},
    },
    "auth_package": {
        "create": {"func_name": "ppp_package", "call_action": "add", "fields": AUTH_PACKAGE_FIELDS, "required": {"name"}},
        "update": {"func_name": "ppp_package", "call_action": "edit", "fields": AUTH_PACKAGE_FIELDS, "required": {"id"}},
        "delete": {"func_name": "ppp_package", "call_action": "del", "fields": {"id"}, "required": {"id"}},
    },
    "auth_web": {
        "update": {"func_name": "webauth", "call_action": "edit", "fields": AUTH_WEB_FIELDS, "required": {"id"}},
        "enable": {"func_name": "webauth", "call_action": "edit", "fields": AUTH_WEB_FIELDS, "required": {"id"}, "inject": {"enabled": "yes"}},
        "disable": {"func_name": "webauth", "call_action": "edit", "fields": AUTH_WEB_FIELDS, "required": {"id"}, "inject": {"enabled": "no"}},
    },
    "auth_pppoe_server": {
        "update": {"func_name": "pppoe_server", "call_action": "edit", "fields": AUTH_SERVICE_FIELDS, "required": {"id"}},
        "enable": {"func_name": "pppoe_server", "call_action": "edit", "fields": AUTH_SERVICE_FIELDS, "required": {"id"}, "inject": {"enabled": "yes"}},
        "disable": {"func_name": "pppoe_server", "call_action": "edit", "fields": AUTH_SERVICE_FIELDS, "required": {"id"}, "inject": {"enabled": "no"}},
    },
    "auth_proxy": {
        "update": {"func_name": "pppoe_server", "call_action": "edit", "fields": AUTH_SERVICE_FIELDS, "required": {"id"}},
        "enable": {"func_name": "pppoe_server", "call_action": "edit", "fields": AUTH_SERVICE_FIELDS, "required": {"id"}, "inject": {"enabled": "yes"}},
        "disable": {"func_name": "pppoe_server", "call_action": "edit", "fields": AUTH_SERVICE_FIELDS, "required": {"id"}, "inject": {"enabled": "no"}},
    },
    "auth_pptp_server": {
        "update": {"func_name": "pptp_server", "call_action": "edit", "fields": AUTH_SERVICE_FIELDS, "required": {"id"}},
        "enable": {"func_name": "pptp_server", "call_action": "edit", "fields": AUTH_SERVICE_FIELDS, "required": {"id"}, "inject": {"enabled": "yes"}},
        "disable": {"func_name": "pptp_server", "call_action": "edit", "fields": AUTH_SERVICE_FIELDS, "required": {"id"}, "inject": {"enabled": "no"}},
    },
    "auth_l2tp_server": {
        "update": {"func_name": "l2tp_server", "call_action": "edit", "fields": AUTH_SERVICE_FIELDS, "required": {"id"}},
        "enable": {"func_name": "l2tp_server", "call_action": "edit", "fields": AUTH_SERVICE_FIELDS, "required": {"id"}, "inject": {"enabled": "yes"}},
        "disable": {"func_name": "l2tp_server", "call_action": "edit", "fields": AUTH_SERVICE_FIELDS, "required": {"id"}, "inject": {"enabled": "no"}},
    },
}


for section_id, candidates in SECTION_CALLS.items():
    if candidates and section_id not in IKUAI_SECTION_ACTIONS and section_id not in IKUAI_READONLY_SECTIONS:
        IKUAI_SECTION_ACTIONS[section_id] = {
            "update": {
                "func_name": "__section__",
                "call_action": "edit",
                "fields": "*",
                "required": set(),
            }
        }

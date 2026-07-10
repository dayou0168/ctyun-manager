import asyncio
import copy
import ipaddress
import json
import re
import shlex
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import suppress
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import settings
from .db import connect, migrate, rows_to_dicts
from .security import decrypt_text, encrypt_text, encryption_key_status, mask, sign_session, verify_password, verify_session
from .services.browser_automation import (
    activate_payment_method,
    check_recharge_payment_status,
    change_console_ecs_private_ip,
    close_browser_sessions,
    close_recharge_session,
    cleanup_idle_browser_sessions,
    create_recharge_order,
    current_totp,
    get_finance,
    browser_session_stats,
    keepalive_saved_cookie,
    get_console_eip_create_options,
    get_console_ecs_vnc_url,
    get_console_resource_stock,
    get_payment_qr,
    login_and_open_recharge,
    normalize_totp_secret,
    refresh_payment_qr,
    reset_browser_session,
)
from .services.ctyun_client import (
    CtyunClientError,
    CtyunClientSkipped,
    build_client,
    build_region_client,
    sg_rule_body,
    sg_rule_direction,
    sg_rule_id,
)
from .services.ctyun_console_api import CtyunConsoleApi, CtyunConsoleApiError
from .services.ikuai_client import IkuaiClient, IkuaiClientError, IKUAI_MENU_GROUPS, IKUAI_SECTION_ACTIONS, SECTION_CALLS, gateway_summary, normalize_base_url
from .services.rustdesk_customizer import RUSTDESK_DEFAULT_BUILD_TARGETS, RustDeskCustomizeError, customize_rustdesk
from .services.ssh_manager import (
    SSHManagerError,
    connect_ssh,
    list_remote_directory,
    normalize_host,
    read_remote_file,
    run_ssh_command,
    ssh_fingerprint,
    test_ssh_connection,
    write_remote_file,
)

APP_VERSION = "2026.07.08.0002"
BUILD_TIME = "2026-06-23 00:41 Asia/Shanghai"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
L2TP_INSTALL_SCRIPT = PROJECT_ROOT / "install-l2tp-server.sh"
L2TP_USERS_PATH = "/etc/l2tp-vpn/users.conf"
NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}
CONFIRMED_ECS_PRIVATE_IP_PROTECTION_SECONDS = 15 * 60


class NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: dict[str, Any]) -> Response:
        response = await super().get_response(path, scope)
        response.headers.update(NO_CACHE_HEADERS)
        return response


app = FastAPI(title="Ctyun Manager")
app.mount("/static", NoCacheStaticFiles(directory=settings.static_dir), name="static")
novnc_dir = Path("/usr/share/novnc")
if novnc_dir.exists():
    app.mount("/novnc", NoCacheStaticFiles(directory=novnc_dir, html=True), name="novnc")


class LoginBody(BaseModel):
    username: str
    password: str


class AccountBody(BaseModel):
    name: str
    provider_account_id: str = ""
    region: str = ""
    username: str | None = None
    password: str | None = None
    totp_secret: str | None = None
    ak: str | None = None
    sk: str | None = None
    notes: str = ""


class ActionBody(BaseModel):
    resource_type: str
    action: str
    resource_id: str | None = None
    payload: dict[str, Any] = {}


class PriceBody(BaseModel):
    payload: dict[str, Any] = {}


class RenewBody(BaseModel):
    resource_ids: list[str] = []
    month: int = Field(default=1, ge=1, le=36)
    by_year: bool = True


class RenewOrderStatusBody(BaseModel):
    master_order_id: str


class SyncBody(BaseModel):
    types: list[str]
    region_ids: list[str] = []


class OptionPrewarmBody(BaseModel):
    region_ids: list[str] = []
    available_only: bool = True
    include_images: bool = True
    limit: int = 8


class RechargeBody(BaseModel):
    amount: str
    payment_method: str = "wechat"


class PaymentBody(BaseModel):
    payment_method: str


class IkuaiGatewayBody(BaseModel):
    name: str
    base_url: str
    username: str | None = None
    password: str | None = None
    notes: str = ""


class IkuaiRawCallBody(BaseModel):
    func_name: str
    action: str = "show"
    param: dict[str, Any] = {}


class LinuxServerBody(BaseModel):
    name: str
    host: str
    port: int = 22
    username: str | None = None
    password: str | None = None
    private_key: str | None = None
    private_key_passphrase: str | None = None
    notes: str = ""


class LinuxCommandBody(BaseModel):
    command: str
    timeout: int = Field(default=30, ge=3, le=300)


class LinuxPathBody(BaseModel):
    path: str = "."


class LinuxFileBody(BaseModel):
    path: str


class LinuxFileWriteBody(BaseModel):
    path: str
    content: str = Field(default="", max_length=2_000_000)


class LinuxL2tpConfigBody(BaseModel):
    content: str = Field(default="", max_length=2_000_000)
    apply: bool = True


class LinuxL2tpInstallBody(BaseModel):
    port: int = Field(default=1701, ge=1, le=65535)
    mtu: int = Field(default=1400, ge=576, le=1500)
    mru: int = Field(default=1400, ge=576, le=1500)
    psk: str = ""
    random_psk: bool = False


class IkuaiSectionActionBody(BaseModel):
    action: str
    payload: dict[str, Any] = {}


class RustDeskAboutBody(BaseModel):
    title: str = ""
    product_name: str = ""
    vendor_name: str = ""
    support_url: str = ""
    privacy_url: str = ""
    show_official_link: bool = True
    show_license_text: bool = True


class RustDeskCustomizeBody(BaseModel):
    repo: str
    token: str
    rustdesk_version: str
    id_server: str
    rs_pub_key: str
    relay_server: str = ""
    api_server: str = ""
    default_password: str = ""
    allow_remote_config_modification: bool = True
    hide_cm: bool = True
    hide_builtin_server_values: bool = True
    build_targets: list[str] = Field(default_factory=lambda: list(RUSTDESK_DEFAULT_BUILD_TARGETS))
    icon_data_url: str = ""
    icon_file_name: str = ""
    about: RustDeskAboutBody = RustDeskAboutBody()
    commit_message: str = ""


SYNC_TYPES = (
    "ecs",
    "eip",
    "vpc",
    "subnet",
    "vip",
    "image",
    "security_group",
    "route_table",
    "acl",
)
PAYMENT_METHODS = {"wechat", "alipay", "bestpay"}
background_sync_lock = threading.Lock()
post_action_sync_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ctyun-post-action-sync")
account_sync_locks: dict[int, threading.Lock] = {}
account_sync_locks_guard = threading.Lock()
resource_sync_locks: dict[tuple[int, str], threading.Lock] = {}
resource_sync_locks_guard = threading.Lock()
resource_db_write_lock = threading.Lock()
finance_refresh_queue: asyncio.Queue[int] = asyncio.Queue()
finance_refresh_pending: set[int] = set()
finance_refresh_pending_lock = asyncio.Lock()
cookie_keepalive_queue: asyncio.Queue[int] = asyncio.Queue()
cookie_keepalive_pending: set[int] = set()
cookie_keepalive_pending_lock = asyncio.Lock()
account_id_verified: set[int] = set()
recharge_prewarm_lock = asyncio.Lock()
rustdesk_jobs_lock = threading.Lock()
rustdesk_jobs: dict[str, dict[str, Any]] = {}
option_cache: dict[tuple[Any, ...], tuple[float, list[dict[str, Any]]]] = {}
option_cache_lock = threading.Lock()
option_prewarm_keys: set[tuple[int, str]] = set()
option_prewarm_keys_lock = threading.Lock()
PERSISTENT_OPTION_KINDS = {"flavors", "disk_types", "images_public"}
UNVERIFIED_STOCK_STATUSES = {"stock_pending", "stock_error", "stock_empty", "console_stock_error"}
FLAVOR_STOCK_OPTION_TTL = 3600
OPTION_CACHE_TTLS = {
    "regions": 3600,
    "zones": 600,
    "flavors": 86400,
    "disk_types": 86400,
    "images": 21600,
    "images_public": 604800,
    "images_account": 300,
    "vpcs": 120,
    "subnets": 120,
    "security_groups": 120,
    "eips": 120,
    "ecs": 120,
    "vips": 120,
    "route_tables": 120,
    "acls": 120,
    "keypairs": 120,
    "eip_lines": 600,
    "eip_cycle_types": 600,
    "eip_demand_billing_types": 600,
}


def account_sync_lock(account_id: int) -> threading.Lock:
    with account_sync_locks_guard:
        return account_sync_locks.setdefault(int(account_id), threading.Lock())
SHARED_REGION_OPTION_KINDS = {"zones", "flavors", "disk_types"}


@app.on_event("startup")
def startup() -> None:
    migrate()
    restore_rustdesk_jobs()
    if settings.background_sync_enabled:
        worker = threading.Thread(target=background_sync_loop, name="ctyun-background-sync", daemon=True)
        worker.start()


@app.on_event("startup")
async def startup_finance_refresh() -> None:
    app.state.finance_tasks = []
    if settings.finance_refresh_enabled:
        app.state.finance_tasks.append(asyncio.create_task(finance_refresh_loop()))
        for index in range(settings.finance_refresh_workers):
            app.state.finance_tasks.append(asyncio.create_task(finance_refresh_worker(index + 1)))
    app.state.cookie_keepalive_tasks = []
    if settings.cookie_keepalive_enabled:
        app.state.cookie_keepalive_tasks.append(asyncio.create_task(cookie_keepalive_loop()))
        for index in range(settings.cookie_keepalive_workers):
            app.state.cookie_keepalive_tasks.append(asyncio.create_task(cookie_keepalive_worker(index + 1)))
    app.state.inventory_task = asyncio.create_task(option_inventory_refresh_loop())
    app.state.recharge_prewarm_task = None
    if settings.recharge_prewarm_enabled:
        app.state.recharge_prewarm_task = asyncio.create_task(recharge_prewarm_loop())
    app.state.browser_cleanup_task = asyncio.create_task(browser_session_cleanup_loop())


@app.on_event("shutdown")
async def shutdown() -> None:
    post_action_sync_executor.shutdown(wait=False, cancel_futures=True)
    for task in list(getattr(app.state, "finance_tasks", [])):
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    for task in list(getattr(app.state, "cookie_keepalive_tasks", [])):
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    inventory_task = getattr(app.state, "inventory_task", None)
    if inventory_task:
        inventory_task.cancel()
        with suppress(asyncio.CancelledError):
            await inventory_task
    recharge_prewarm_task = getattr(app.state, "recharge_prewarm_task", None)
    if recharge_prewarm_task:
        recharge_prewarm_task.cancel()
        with suppress(asyncio.CancelledError):
            await recharge_prewarm_task
    browser_cleanup_task = getattr(app.state, "browser_cleanup_task", None)
    if browser_cleanup_task:
        browser_cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await browser_cleanup_task
    for task in list(getattr(app.state, "recharge_prewarm_tasks", set())):
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    await close_browser_sessions()


def require_user(request: Request) -> dict[str, Any]:
    payload = verify_session(request.cookies.get("ctyun_manager_session"))
    if not payload:
        raise HTTPException(status_code=401, detail="not_authenticated")
    return payload


def option_cache_key(
    account: dict[str, Any],
    kind: str,
    *,
    region_id: str = "",
    vpc_id: str = "",
    az_name: str = "",
    available_only: bool = False,
) -> tuple[Any, ...]:
    shared = kind in SHARED_REGION_OPTION_KINDS
    return (
        "shared" if shared else int(account["id"]),
        "" if shared else account.get("updated_at") or "",
        settings.ctyun_mode,
        kind,
        region_id or "",
        vpc_id or "",
        az_name or "",
        bool(available_only),
    )


def option_cache_get(key: tuple[Any, ...]) -> list[dict[str, Any]] | None:
    now = time.monotonic()
    with option_cache_lock:
        cached = option_cache.get(key)
        if not cached:
            cached = None
        else:
            expires_at, value = cached
            if expires_at > now:
                return copy.deepcopy(value)
            option_cache.pop(key, None)
    kind = str(key[3]) if len(key) > 3 else ""
    if kind not in PERSISTENT_OPTION_KINDS:
        return None
    storage_key = option_cache_storage_key(key)
    with suppress(Exception):
        with connect() as conn:
            row = conn.execute(
                "select payload_json, expires_at from option_cache where cache_key=? and expires_at>?",
                (storage_key, time.time()),
            ).fetchone()
        if not row:
            return None
        value = json.loads(row["payload_json"] or "[]")
        if not isinstance(value, list):
            return None
        ttl = max(1, float(row["expires_at"]) - time.time())
        with option_cache_lock:
            option_cache[key] = (time.monotonic() + ttl, copy.deepcopy(value))
        return copy.deepcopy(value)
    return None


def option_cache_storage_key(key: tuple[Any, ...]) -> str:
    return json.dumps(list(key), ensure_ascii=False, sort_keys=False, separators=(",", ":"))


def option_cache_set(kind: str, key: tuple[Any, ...], value: list[dict[str, Any]], ttl_override: int | None = None) -> list[dict[str, Any]]:
    ttl = ttl_override if ttl_override is not None else OPTION_CACHE_TTLS.get(kind, 0)
    if ttl <= 0:
        return value
    with option_cache_lock:
        if len(option_cache) > 2000:
            option_cache.clear()
        option_cache[key] = (time.monotonic() + ttl, copy.deepcopy(value))
    if kind in PERSISTENT_OPTION_KINDS and value:
        with suppress(Exception):
            with connect() as conn:
                conn.execute(
                    """
                    insert into option_cache(cache_key, kind, payload_json, expires_at, updated_at)
                    values(?, ?, ?, ?, current_timestamp)
                    on conflict(cache_key) do update set
                      kind=excluded.kind,
                      payload_json=excluded.payload_json,
                      expires_at=excluded.expires_at,
                      updated_at=current_timestamp
                    """,
                    (option_cache_storage_key(key), kind, json.dumps(value, ensure_ascii=False), time.time() + ttl),
                )
    return value


def option_cache_clear(account_id: int | None = None, kinds: list[str] | tuple[str, ...] | None = None) -> None:
    kind_set = set(kinds or [])
    with option_cache_lock:
        if account_id is None and not kind_set:
            option_cache.clear()
            return
        for key in list(option_cache.keys()):
            key_account_id = key[0] if len(key) > 0 else None
            key_kind = key[3] if len(key) > 3 else None
            if account_id is not None and key_account_id != account_id:
                continue
            if kind_set and key_kind not in kind_set:
                continue
            option_cache.pop(key, None)
    if kind_set:
        with suppress(Exception):
            with connect() as conn:
                conn.executemany("delete from option_cache where kind=?", [(kind,) for kind in kind_set])


def option_kinds_after_action(resource_type: str, action: str) -> tuple[str, ...]:
    if resource_type == "eip":
        if action in {"bind", "unbind"}:
            return ("eips", "ecs", "vips")
        return ("eips",)
    if resource_type == "vip":
        return ("vips", "ecs", "eips")
    if resource_type == "ecs":
        if action in {"change_private_ip", "change_vpc"}:
            return ("ecs", "vpcs", "subnets", "security_groups", "vips")
        return ("ecs",)
    if resource_type == "vpc":
        return ("vpcs", "subnets", "security_groups", "vips")
    if resource_type == "subnet":
        return ("subnets", "vips")
    if resource_type == "security_group":
        return ("security_groups",)
    if resource_type == "image":
        return ("images", "images_account")
    return (f"{resource_type}s",)


def option_kinds_for_resource_types(resource_types: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    for resource_type in resource_types:
        result.extend(option_kinds_after_action(resource_type, "sync"))
    return tuple(dict.fromkeys(result))


def resource_types_after_action(resource_type: str, action: str) -> tuple[str, ...]:
    if resource_type == "eip":
        if action in {"bind", "unbind"}:
            return ("eip", "ecs", "vip")
        return ("eip",)
    if resource_type == "vip":
        if action in {"bind_ecs", "unbind_ecs"}:
            return ("vip", "ecs")
        if action in {"bind_eip", "unbind_eip"}:
            return ("vip", "eip")
        return ("vip",)
    if resource_type == "ecs":
        if action in {"change_private_ip", "change_vpc"}:
            return ("ecs", "vpc", "subnet", "vip", "security_group")
        if action in {"create_image"}:
            return ("ecs", "image")
        return ("ecs",)
    if resource_type == "vpc":
        if action in {"create_subnet"}:
            return ("vpc", "subnet", "route_table", "acl")
        return ("vpc", "subnet", "security_group", "vip", "route_table", "acl")
    if resource_type == "subnet":
        return ("vpc", "subnet", "vip", "route_table", "acl")
    if resource_type == "security_group":
        return ("security_group",)
    if resource_type == "image":
        return ("image",)
    return (resource_type,)


def post_action_region_ids(payload: dict[str, Any]) -> list[str]:
    values = [
        payload.get("regionID"),
        payload.get("regionId"),
        payload.get("region"),
        payload.get("region_id"),
        payload.get("_cached", {}).get("regionID") if isinstance(payload.get("_cached"), dict) else "",
        payload.get("_cached", {}).get("region") if isinstance(payload.get("_cached"), dict) else "",
    ]
    result: list[str] = []
    for value in values:
        for item in split_region_ids(str(value or "")):
            if item not in result:
                result.append(item)
    return result


def best_effort_post_action_sync(
    account: dict[str, Any],
    resource_type: str,
    action: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    kinds = [kind for kind in resource_types_after_action(resource_type, action) if kind in SYNC_TYPES]
    if not kinds:
        return {"ok": True, "counts": {}, "errors": {}, "skipped": {}}
    try:
        narrowed = account_for_fast_sync(account, kinds, post_action_region_ids(payload))
        result = sync_account_resources(narrowed, kinds)
        status_value = "success" if not result.get("errors") else "partial"
        verify = verify_post_action_result(account, resource_type, action, payload)
        if verify:
            status_value = verify["status"]
            result = {**result, "verify": verify}
        record_operation(
            account["id"],
            resource_type,
            str(payload.get("resource_id") or ""),
            "post_action_sync",
            status_value,
            json.dumps({"action": action, **result}, ensure_ascii=False),
        )
        return result
    except Exception as exc:
        message = str(exc)
        record_operation(account["id"], resource_type, str(payload.get("resource_id") or ""), "post_action_sync", "failed", message)
        return {"ok": False, "counts": {}, "errors": {"post_action_sync": message}, "skipped": {}}


def action_submit_summary(resource_type: str, action: str, payload: dict[str, Any]) -> dict[str, Any]:
    cached = payload.get("_cached") if isinstance(payload.get("_cached"), dict) else {}
    summary = {
        "资源池": str(payload.get("regionID") or payload.get("region") or cached.get("regionID") or cached.get("region") or ""),
        "资源ID": str(payload.get("resource_id") or ""),
    }
    if resource_type == "ecs" and action == "change_private_ip":
        return {
            "目标内网IP": str(payload.get("privateIP") or payload.get("privateIp") or payload.get("ipAddress") or ""),
            "原内网IP": str(cached.get("private_ip") or cached.get("privateIP") or ""),
            "资源池": str(payload.get("regionID") or payload.get("region") or ""),
            "子网ID": str(payload.get("subnetID") or payload.get("subnetId") or ""),
            "云主机ID": str(payload.get("instanceID") or payload.get("instanceId") or payload.get("resource_id") or ""),
            "网卡ID": str(payload.get("networkInterfaceID") or payload.get("networkCardID") or payload.get("portID") or ""),
        }
    keys = [
        ("名称", "name"),
        ("显示名称", "displayName"),
        ("云主机名称", "instanceName"),
        ("镜像名称", "imageName"),
        ("目标内网IP", "privateIP"),
        ("目标内网IP", "privateIp"),
        ("目标内网IP", "ipAddress"),
        ("VPC ID", "vpcID"),
        ("VPC ID", "vpcId"),
        ("子网ID", "subnetID"),
        ("子网ID", "subnetId"),
        ("云主机ID", "instanceID"),
        ("云主机ID", "instanceId"),
        ("网卡ID", "networkInterfaceID"),
        ("网卡ID", "networkCardID"),
        ("虚拟IP ID", "haVipID"),
        ("虚拟IP ID", "haVipId"),
        ("弹性IP ID", "eipID"),
        ("弹性IP ID", "eipId"),
        ("弹性IP ID", "floatingID"),
        ("弹性IP ID", "floatingId"),
        ("安全组ID", "securityGroupID"),
        ("安全组规则ID", "securityGroupRuleID"),
        ("绑定目标ID", "associationID"),
        ("绑定目标ID", "associationId"),
        ("接收方账号ID", "destinationAccountID"),
        ("公网IP", "eipAddress"),
        ("公网IP", "ip"),
        ("CIDR", "cidr"),
        ("CIDR", "CIDR"),
        ("DNS", "dnsList"),
        ("DNS", "dnsServers"),
        ("描述", "description"),
    ]
    for label, key in keys:
        value = payload.get(key)
        if value not in (None, "", []):
            summary.setdefault(label, str(value))
    return {key: value for key, value in summary.items() if value}


def apply_confirmed_ecs_private_ip(account_id: int, resource_id: str, target_ip: str, subnet_id: str = "", nic_id: str = "") -> None:
    if not resource_id or not target_ip:
        return
    with connect() as conn:
        row = conn.execute(
            "select * from resources where account_id=? and resource_type='ecs' and provider_id=?",
            (account_id, resource_id),
        ).fetchone()
        if not row:
            return
        data = dict(row)
        payload = json.loads(data.get("payload_json") or "{}")
        payload["private_ip"] = target_ip
        payload["privateIP"] = target_ip
        payload["_confirmed_private_ip"] = target_ip
        payload["_confirmed_private_ip_until"] = time.time() + CONFIRMED_ECS_PRIVATE_IP_PROTECTION_SECONDS
        cards = payload.get("networkCardList") if isinstance(payload.get("networkCardList"), list) else []
        for index, card in enumerate(cards):
            if not isinstance(card, dict):
                continue
            card_id = str(card.get("networkCardID") or card.get("networkInterfaceID") or card.get("id") or "").strip()
            if nic_id and card_id and card_id != nic_id:
                continue
            card["IPv4Address"] = target_ip
            if subnet_id:
                card["subnetID"] = subnet_id
            cards[index] = card
            break
        if cards:
            payload["networkCardList"] = cards
        conn.execute(
            """
            update resources
            set payload_json=?, synced_at=current_timestamp
            where account_id=? and resource_type='ecs' and provider_id=?
            """,
            (json.dumps(payload, ensure_ascii=False), account_id, resource_id),
        )


def action_needs_final_verification(resource_type: str, action: str) -> bool:
    if action in {
        "start",
        "stop",
        "reboot",
        "create",
        "delete",
        "release",
        "unsubscribe",
        "rename",
        "update",
        "bind",
        "unbind",
        "create_rule",
        "update_rule",
        "delete_rule",
        "share",
        "unshare",
        "accept",
        "reject",
    }:
        return True
    if resource_type == "ecs" and action in {"change_private_ip", "change_vpc", "deletion_protection", "auto_renew", "renew", "resize", "rebuild", "create_image"}:
        return True
    if resource_type == "vpc" and action == "create_subnet":
        return True
    if resource_type == "image" and action == "copy":
        return True
    if resource_type == "vip" and action in {"bind_ecs", "unbind_ecs", "bind_eip", "unbind_eip"}:
        return True
    return False


def _load_resource_row(account_id: int, resource_type: str, resource_id: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if not resource_id:
        return None, {}
    with connect() as conn:
        row = conn.execute(
            "select * from resources where account_id=? and resource_type=? and provider_id=?",
            (account_id, resource_type, resource_id),
        ).fetchone()
    if not row:
        return None, {}
    data = dict(row)
    payload = json.loads(data.get("payload_json") or "{}")
    return data, payload


def _resource_rows(account_id: int, resource_type: str, region_id: str = "") -> list[tuple[dict[str, Any], dict[str, Any]]]:
    params: list[Any] = [account_id, resource_type]
    where = "account_id=? and resource_type=?"
    if region_id:
        where += " and region=?"
        params.append(region_id)
    with connect() as conn:
        rows = conn.execute(f"select * from resources where {where}", params).fetchall()
    result: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for row in rows:
        data = dict(row)
        try:
            payload = json.loads(data.get("payload_json") or "{}")
        except json.JSONDecodeError:
            payload = {}
        result.append((data, payload))
    return result


def _payload_values(payload: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    result: list[str] = []
    for key in keys:
        value = payload.get(key)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, (list, tuple, set)):
            result.extend(str(item) for item in value if item not in (None, "", [], {}))
        elif isinstance(value, dict):
            result.append(_flatten_text(value))
        else:
            result.append(str(value))
    return [item for item in result if item]


def _resource_name_values(row: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    return [
        str(value)
        for value in [
            row.get("name"),
            payload.get("name"),
            payload.get("displayName"),
            payload.get("instanceName"),
            payload.get("imageName"),
            payload.get("imageDisplayName"),
            payload.get("securityGroupName"),
        ]
        if value not in (None, "")
    ]


def _payload_field_text(payload: dict[str, Any], keys: tuple[str, ...]) -> str:
    values: list[str] = []
    for key in keys:
        values.append(_flatten_text(payload.get(key)))
    return " ".join(item for item in values if item)


def _find_resource_by_name(
    account_id: int,
    resource_type: str,
    region_id: str,
    name: str,
    payload_matches: dict[str, tuple[str, ...]] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if not name:
        return None, {}
    for row, payload in _resource_rows(account_id, resource_type, region_id):
        if name not in _resource_name_values(row, payload):
            continue
        ok = True
        for expected, keys in (payload_matches or {}).items():
            if not expected:
                continue
            values = _payload_values(payload, keys)
            if expected not in values:
                ok = False
                break
        if ok:
            return row, payload
    return None, {}


def _find_vip_by_ip(account_id: int, region_id: str, ip_value: str, subnet_id: str = "") -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if not ip_value:
        return None, {}
    for row, payload in _resource_rows(account_id, "vip", region_id):
        current_ip = str(payload.get("ipv4") or payload.get("ip") or payload.get("ipAddress") or "")
        current_subnet = str(payload.get("subnetID") or payload.get("subnetId") or payload.get("subnet_id") or "")
        if current_ip == ip_value and (not subnet_id or current_subnet == subnet_id):
            return row, payload
    return None, {}


def _resource_has_binding(payload: dict[str, Any], keys: tuple[str, ...]) -> bool:
    return bool(_binding_text(payload, keys))


def _binding_text(payload: dict[str, Any], keys: tuple[str, ...]) -> str:
    values: list[str] = []
    for key in keys:
        value = payload.get(key)
        if value in (None, "", [], {}, "0", "-"):
            continue
        values.append(_flatten_text(value))
    for key in ("instanceInfo", "networkInfo", "bound_instances", "bound_eips"):
        value = payload.get(key)
        if value in (None, "", [], {}, "0", "-"):
            continue
        values.append(_flatten_text(value))
    return " ".join(item for item in values if item)


def _binding_contains(payload: dict[str, Any], keys: tuple[str, ...], target_id: str = "") -> bool:
    text = _binding_text(payload, keys)
    if not target_id:
        return bool(text)
    return target_id in text


def _bool_text(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y", "on", "enable", "enabled", "开启", "启用"}:
        return "1"
    if text in {"false", "0", "no", "n", "off", "disable", "disabled", "关闭", "停用"}:
        return "0"
    return text


def _security_group_rules(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("securityGroupRuleList", "securityGroupRules", "rules", "ruleList"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _rule_normalized(rule: dict[str, Any]) -> dict[str, str]:
    direction = sg_rule_direction(rule.get("direction"))
    protocol = str(rule.get("protocol") or "ANY").strip().upper()
    cidr = str(
        rule.get("destCidrIp")
        or rule.get("sourceCidrIp")
        or rule.get("remoteIpPrefix")
        or rule.get("remoteIPPrefix")
        or rule.get("cidr")
        or ""
    ).strip()
    ethertype = str(rule.get("ethertype") or rule.get("etherType") or ("IPv6" if ":" in cidr else "IPv4")).strip()
    return {
        "direction": direction,
        "action": str(rule.get("ruleAction") or rule.get("action") or "accept").strip().lower(),
        "protocol": protocol,
        "ethertype": ethertype,
        "cidr": cidr,
        "range": str(rule.get("range") or rule.get("portRange") or "").strip(),
        "priority": str(rule.get("priority") or "").strip(),
    }


def _rule_matches(rule: dict[str, Any], expected: dict[str, Any]) -> bool:
    current = _rule_normalized(rule)
    target = _rule_normalized(expected)
    for key in ("direction", "action", "protocol", "ethertype", "cidr", "range"):
        if target[key] and current[key] != target[key]:
            return False
    return True


def _rule_id_exists(payload: dict[str, Any], rule_id: str) -> bool:
    if not rule_id:
        return False
    for rule in _security_group_rules(payload):
        if rule_id in {str(rule.get("id") or ""), str(rule.get("securityGroupRuleID") or ""), str(rule.get("securityGroupRuleId") or "")}:
            return True
    return False


def _rule_body_exists(payload: dict[str, Any], rule: dict[str, Any]) -> bool:
    return any(_rule_matches(current, rule) for current in _security_group_rules(payload))


def _verify_message(status_value: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"status": status_value, "message": message, **{key: value for key, value in extra.items() if value not in (None, "")}}


def verify_post_action_result(
    account: dict[str, Any],
    resource_type: str,
    action: str,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    account_id = int(account["id"])
    resource_id = str(payload.get("resource_id") or payload.get("instanceID") or payload.get("instanceId") or "").strip()
    region_id = str(payload.get("regionID") or payload.get("region") or "").strip()

    if resource_type == "vpc" and action == "create_subnet":
        name = str(payload.get("name") or "").strip()
        target_vpc = str(payload.get("vpcID") or payload.get("networkID") or "").strip()
        target_cidr = str(payload.get("CIDR") or payload.get("cidr") or "").strip()
        row, _created_payload = _find_resource_by_name(
            account_id,
            "subnet",
            region_id,
            name,
            {target_vpc: ("vpcID", "vpcId", "vpc_id"), target_cidr: ("CIDR", "cidr")},
        )
        if row:
            return _verify_message("success", "官方列表已出现目标子网。", 名称=name, VPC=target_vpc, CIDR=target_cidr)
        return _verify_message("unconfirmed", "接口已受理，但官方列表暂未出现目标子网。", 名称=name, VPC=target_vpc, CIDR=target_cidr)

    if resource_type == "ecs" and action == "create_image":
        image_name = str(payload.get("imageName") or "").strip()
        row, _created_payload = _find_resource_by_name(account_id, "image", region_id, image_name)
        if row:
            return _verify_message("success", "官方列表已出现新制作的私有镜像。", 镜像名称=image_name)
        return _verify_message("unconfirmed", "接口已受理，但官方列表暂未出现新制作的私有镜像。", 镜像名称=image_name)

    if resource_type == "image" and action == "copy":
        image_name = str(payload.get("imageName") or "").strip()
        row, _created_payload = _find_resource_by_name(account_id, "image", region_id, image_name)
        if row:
            return _verify_message("success", "官方列表已出现复制后的镜像。", 镜像名称=image_name)
        return _verify_message("unconfirmed", "接口已受理，但官方列表暂未出现复制后的镜像。", 镜像名称=image_name)

    if action in {"delete", "release", "unsubscribe", "reject"} and resource_id:
        row, _current_payload = _load_resource_row(account_id, resource_type, resource_id)
        return _verify_message("success", "官方列表已无该资源。", 资源ID=resource_id) if not row else _verify_message("unconfirmed", "接口已受理，但官方列表仍存在该资源。", 资源ID=resource_id)

    if action == "create":
        if resource_type == "vip":
            target_ip = str(payload.get("ipAddress") or payload.get("ip") or "").strip()
            target_subnet = str(payload.get("subnetID") or payload.get("subnetId") or "").strip()
            row, _created_payload = _find_vip_by_ip(account_id, region_id, target_ip, target_subnet)
            if row:
                return _verify_message("success", "官方列表已出现目标虚拟 IP。", 虚拟IP=target_ip, 子网ID=target_subnet)
            if target_ip:
                return _verify_message("unconfirmed", "接口已受理，但官方列表暂未出现目标虚拟 IP。", 虚拟IP=target_ip, 子网ID=target_subnet)
            return _verify_message("unconfirmed", "接口已受理；由于未指定虚拟 IP，需等待官方列表出现新分配地址后才能确认。", 子网ID=target_subnet)
        name = str(payload.get("name") or payload.get("instanceName") or payload.get("displayName") or payload.get("securityGroupName") or "").strip()
        match_type = resource_type
        payload_matches: dict[str, tuple[str, ...]] = {}
        if resource_type == "vpc":
            target_cidr = str(payload.get("CIDR") or payload.get("cidr") or "").strip()
            if target_cidr:
                payload_matches[target_cidr] = ("CIDR", "cidr")
        if resource_type == "security_group":
            target_vpc = str(payload.get("vpcID") or payload.get("vpcId") or "").strip()
            if target_vpc:
                payload_matches[target_vpc] = ("vpcID", "vpcId", "vpc_id")
        row, _created_payload = _find_resource_by_name(account_id, match_type, region_id, name, payload_matches)
        if row:
            return _verify_message("success", "官方列表已出现目标资源。", 名称=name)
        return _verify_message("unconfirmed", "接口已受理，但官方列表暂未出现可匹配的新资源。", 名称=name)

    if not action_needs_final_verification(resource_type, action):
        return None

    row, current_payload = _load_resource_row(account_id, resource_type, resource_id)
    if not row:
        return _verify_message("unconfirmed", "同步后官方列表未返回目标资源，无法确认最终状态。", 资源ID=resource_id)
    official_status = str(current_payload.get("official_status") or row.get("status") or current_payload.get("status") or current_payload.get("instanceStatus") or "").lower()

    if resource_type == "ecs" and action in {"start", "reboot"}:
        return _verify_message("success", "官方列表已显示云主机运行中。", 当前状态=official_status) if re.search(r"running|active|started|运行", official_status) else _verify_message("unconfirmed", "接口已受理，但官方列表尚未显示云主机运行中。", 当前状态=official_status)
    if resource_type == "ecs" and action == "stop":
        return _verify_message("success", "官方列表已显示云主机关机。", 当前状态=official_status) if re.search(r"stopped|shutdown|shutoff|closed|halted|关机|停止", official_status) else _verify_message("unconfirmed", "接口已受理，但官方列表尚未显示云主机关机。", 当前状态=official_status)

    target_ip = str(payload.get("privateIP") or payload.get("privateIp") or payload.get("ipAddress") or "").strip()
    if resource_type == "ecs" and action == "change_private_ip":
        if not target_ip:
            return _verify_message("unconfirmed", "缺少目标内网 IP，无法校验最终结果。", 云主机ID=resource_id)
        confirmed_ip = str(payload.get("_confirmed_private_ip") or "").strip()
        if confirmed_ip and confirmed_ip == target_ip:
            return _verify_message("success", f"官方控制台已确认新的内网 IP：{target_ip}", 目标内网IP=target_ip, 云主机ID=resource_id)
        current_ips = _payload_private_ips(current_payload)
        current_ip = current_ips[0] if current_ips else str(current_payload.get("private_ip") or current_payload.get("privateIP") or "")
        if target_ip in current_ips or str(current_ip) == target_ip:
            return _verify_message("success", f"官方列表已显示新的内网 IP：{target_ip}", 目标内网IP=target_ip, 当前内网IP=current_ip)
        return _verify_message("unconfirmed", f"天翼云接口已受理，但官方列表仍显示 {current_ip or '-'}，未显示目标 IP {target_ip}。", 目标内网IP=target_ip, 当前内网IP=current_ip, 云主机ID=resource_id)
    if resource_type == "ecs" and action == "change_vpc":
        target_vpc = str(payload.get("vpcID") or payload.get("vpcId") or "").strip()
        target_subnet = str(payload.get("subnetID") or payload.get("subnetId") or "").strip()
        current_vpc = str(current_payload.get("vpc_id") or current_payload.get("vpcID") or current_payload.get("vpcId") or "")
        current_subnet = str(current_payload.get("subnet_id") or current_payload.get("subnetID") or current_payload.get("subnetId") or "")
        ok = (not target_vpc or current_vpc == target_vpc) and (not target_subnet or current_subnet == target_subnet)
        if target_ip:
            current_ips = _payload_private_ips(current_payload)
            ok = ok and target_ip in current_ips
        return _verify_message("success", "官方列表已显示目标网络配置。", 当前VPC=current_vpc, 当前子网=current_subnet) if ok else _verify_message("unconfirmed", "接口已受理，但官方列表尚未显示目标网络配置。", 目标VPC=target_vpc, 当前VPC=current_vpc, 目标子网=target_subnet, 当前子网=current_subnet, 目标内网IP=target_ip)

    if resource_type == "ecs" and action == "update":
        target_display = str(payload.get("displayName") or "").strip()
        target_name = str(payload.get("instanceName") or "").strip()
        target_desc = str(payload.get("instanceDescription") or "").strip()
        current_display = str(current_payload.get("displayName") or row.get("name") or "")
        current_name = str(current_payload.get("instanceName") or "")
        current_desc = str(current_payload.get("instanceDescription") or "")
        checks = []
        if target_display:
            checks.append(current_display == target_display)
        if target_name:
            checks.append(current_name == target_name)
        if target_desc:
            checks.append(current_desc == target_desc)
        if checks and all(checks):
            return _verify_message("success", "官方列表已显示更新后的云主机信息。", 显示名称=current_display, 主机名=current_name)
        return _verify_message("unconfirmed", "接口已受理，但官方列表尚未显示更新后的云主机信息。", 目标显示名称=target_display, 当前显示名称=current_display, 目标主机名=target_name, 当前主机名=current_name)

    if resource_type == "ecs" and action == "deletion_protection":
        target = _bool_text(payload.get("deletionProtection"))
        current = _bool_text(current_payload.get("deletionProtection") if "deletionProtection" in current_payload else current_payload.get("deletion_protection"))
        if current and target == current:
            return _verify_message("success", "官方列表已显示目标删除保护状态。", 当前状态=current)
        return _verify_message("unconfirmed", "接口已受理，但官方列表尚未显示目标删除保护状态。", 目标状态=target, 当前状态=current)

    if resource_type == "ecs" and action == "auto_renew":
        target = str(payload.get("autoRenewStatus") or "").strip()
        current = str(current_payload.get("autoRenewStatus") or current_payload.get("auto_renew_status") or "").strip()
        if current and target and current == target:
            return _verify_message("success", "官方列表已显示目标自动续订状态。", 当前状态=current)
        return _verify_message("unconfirmed", "接口已受理，但官方列表尚未显示目标自动续订状态。", 目标状态=target, 当前状态=current)

    if resource_type == "ecs" and action == "resize":
        target_flavor = str(payload.get("flavorID") or payload.get("flavorName") or "").strip()
        current_flavor_text = _payload_field_text(current_payload, ("flavor", "flavorID", "flavorName", "spec"))
        if target_flavor and target_flavor in current_flavor_text:
            return _verify_message("success", "官方列表已显示目标规格。", 目标规格=target_flavor)
        return _verify_message("unconfirmed", "接口已受理，但官方列表尚未显示目标规格。", 目标规格=target_flavor, 当前规格=current_flavor_text)

    if resource_type == "ecs" and action == "rebuild":
        target_image = str(payload.get("imageID") or "").strip()
        current_image_text = _payload_field_text(current_payload, ("image", "imageID", "imageName", "os"))
        if target_image and target_image in current_image_text:
            return _verify_message("success", "官方列表已显示目标镜像信息。", 目标镜像=target_image)
        return _verify_message("unconfirmed", "接口已受理，但官方列表尚未显示目标镜像信息。", 目标镜像=target_image, 当前镜像=current_image_text)

    if resource_type == "eip" and action == "bind":
        target = str(payload.get("associationID") or payload.get("associationId") or "").strip()
        current = str(current_payload.get("associationID") or current_payload.get("associationId") or "")
        bound = current == target if target else (_resource_has_binding(current_payload, ("associationID", "associationId", "instanceID", "instanceId", "deviceID", "deviceId", "bound_instances")) or str(current_payload.get("binding_status") or "").lower() == "bound")
        return _verify_message("success", "官方列表已显示弹性 IP 绑定关系。", 绑定目标ID=current) if bound else _verify_message("unconfirmed", "接口已受理，但官方列表尚未显示绑定关系。", 目标绑定ID=target, 当前绑定ID=current)
    if resource_type == "eip" and action == "unbind":
        bound = _resource_has_binding(current_payload, ("associationID", "associationId", "instanceID", "instanceId", "deviceID", "deviceId", "bound_instances")) or str(current_payload.get("binding_status") or "").lower() == "bound"
        return _verify_message("unconfirmed", "接口已受理，但官方列表仍显示绑定关系。") if bound else _verify_message("success", "官方列表已显示弹性 IP 未绑定。")

    if resource_type == "vip" and action in {"bind_ecs", "bind_eip"}:
        keys = ("bound_instances", "instanceID", "instanceId") if action == "bind_ecs" else ("bound_eips", "floatingID", "floatingId", "eipID", "eipId")
        target = str(payload.get("instanceID") or payload.get("floatingID") or payload.get("eipID") or "").strip()
        return _verify_message("success", "官方列表已显示虚拟 IP 绑定关系。", 绑定目标ID=target) if _binding_contains(current_payload, keys, target) else _verify_message("unconfirmed", "接口已受理，但官方列表尚未显示虚拟 IP 绑定关系。", 目标绑定ID=target)
    if resource_type == "vip" and action in {"unbind_ecs", "unbind_eip"}:
        keys = ("bound_instances", "instanceID", "instanceId") if action == "unbind_ecs" else ("bound_eips", "floatingID", "floatingId", "eipID", "eipId")
        target = str(payload.get("instanceID") or payload.get("floatingID") or payload.get("eipID") or "").strip()
        still_bound = _binding_contains(current_payload, keys, target) if target else _resource_has_binding(current_payload, keys)
        return _verify_message("unconfirmed", "接口已受理，但官方列表仍显示虚拟 IP 绑定关系。", 目标绑定ID=target) if still_bound else _verify_message("success", "官方列表已显示虚拟 IP 已解绑。", 目标绑定ID=target)

    if resource_type == "security_group" and action in {"create_rule", "update_rule"}:
        direction = sg_rule_direction(payload.get("direction"))
        expected = sg_rule_body(payload, direction)
        if _rule_body_exists(current_payload, expected):
            return _verify_message("success", "官方列表已显示目标安全组规则。")
        return _verify_message("unconfirmed", "接口已受理，但官方列表尚未显示目标安全组规则。")

    if resource_type == "security_group" and action == "delete_rule":
        rule_id = sg_rule_id(payload)
        if rule_id and not _rule_id_exists(current_payload, rule_id):
            return _verify_message("success", "官方列表已删除目标安全组规则。", 规则ID=rule_id)
        return _verify_message("unconfirmed", "接口已受理，但官方列表仍显示目标安全组规则。", 规则ID=rule_id)

    if action in {"rename", "update"}:
        target_name = str(payload.get("name") or payload.get("displayName") or payload.get("instanceName") or "").strip()
        if target_name:
            current_name = str(row.get("name") or current_payload.get("name") or current_payload.get("displayName") or current_payload.get("instanceName") or current_payload.get("securityGroupName") or "")
            return _verify_message("success", "官方列表已显示更新后的名称。", 当前名称=current_name) if current_name == target_name else _verify_message("unconfirmed", "接口已受理，但官方列表尚未显示更新后的名称。", 目标名称=target_name, 当前名称=current_name)

    if resource_type == "subnet" and action == "update":
        target_dns = [item.strip() for item in str(payload.get("dnsList") or payload.get("dnsServers") or "").replace("，", ",").split(",") if item.strip()]
        current_dns = _payload_values(current_payload, ("dnsList", "dnsServers"))
        if target_dns:
            current_dns_text = ",".join(current_dns)
            if all(item in current_dns_text for item in target_dns):
                return _verify_message("success", "官方列表已显示目标 DNS。", 当前DNS=current_dns_text)
            return _verify_message("unconfirmed", "接口已受理，但官方列表尚未显示目标 DNS。", 目标DNS=",".join(target_dns), 当前DNS=current_dns_text)

    if resource_type == "image" and action == "accept":
        if re.search(r"accepted|active|available|已接受|可用", official_status):
            return _verify_message("success", "官方列表已显示共享镜像可用。", 当前状态=official_status)
        return _verify_message("unconfirmed", "接口已受理，但官方列表尚未显示共享镜像可用。", 当前状态=official_status)

    if resource_type == "image" and action in {"share", "unshare"}:
        target = str(payload.get("destinationAccountID") or payload.get("destinationUser") or "").strip()
        text = _flatten_text(current_payload)
        has_target = bool(target and target in text)
        if action == "share":
            return _verify_message("success", "官方列表已显示镜像共享目标。", 接收方账号ID=target) if has_target else _verify_message("unconfirmed", "接口已受理，但官方列表尚未显示镜像共享目标。", 接收方账号ID=target)
        return _verify_message("unconfirmed", "接口已受理，但官方列表仍显示镜像共享目标。", 接收方账号ID=target) if has_target else _verify_message("success", "官方列表已不再显示该镜像共享目标。", 接收方账号ID=target)

    return _verify_message("unconfirmed", "官方列表已同步，但该操作缺少可精确比对的最终字段。", 当前状态=official_status)


def queue_post_action_sync(
    account: dict[str, Any],
    resource_type: str,
    action: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    kinds = [kind for kind in resource_types_after_action(resource_type, action) if kind in SYNC_TYPES]
    if not kinds:
        return {"queued": False, "types": []}
    post_action_sync_executor.submit(best_effort_post_action_sync, dict(account), resource_type, action, dict(payload))
    return {"queued": True, "types": kinds}


def get_account(account_id: int) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("select * from ctyun_accounts where id = ?", (account_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="account_not_found")
    return dict(row)


def is_ctyun_host(value: str) -> bool:
    host = (value or "").strip().lower().lstrip(".")
    return host == "ctyun.cn" or host.endswith(".ctyun.cn")


def storage_state_for_console_bridge(cookie_state_enc: str | None) -> dict[str, Any]:
    state_text = decrypt_text(cookie_state_enc)
    if not state_text:
        return {"cookies": [], "origins": []}
    try:
        raw = json.loads(state_text)
    except json.JSONDecodeError:
        return {"cookies": [], "origins": []}
    if not isinstance(raw, dict):
        return {"cookies": [], "origins": []}

    cookies: list[dict[str, Any]] = []
    for cookie in raw.get("cookies") or []:
        if not isinstance(cookie, dict):
            continue
        domain = str(cookie.get("domain") or "")
        url_host = urlparse(str(cookie.get("url") or "")).hostname or ""
        if is_ctyun_host(domain) or is_ctyun_host(url_host):
            cookies.append(dict(cookie))

    origins: list[dict[str, Any]] = []
    for origin in raw.get("origins") or []:
        if not isinstance(origin, dict):
            continue
        host = urlparse(str(origin.get("origin") or "")).hostname or ""
        if is_ctyun_host(host):
            origins.append(dict(origin))

    return {"cookies": cookies, "origins": origins}


def public_account(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "provider_account_id": row.get("provider_account_id", ""),
        "region": row["region"],
        "status": row["status"],
        "notes": row["notes"],
        "username_masked": mask(decrypt_text(row.get("username_enc"))),
        "ak_masked": mask(decrypt_text(row.get("ak_enc"))),
        "has_password": bool(row.get("password_enc")),
        "has_totp": bool(row.get("totp_secret_enc")),
        "has_cookie": bool(row.get("cookie_state_enc")),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_ikuai_gateway(gateway_id: int) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("select * from ikuai_gateways where id=?", (gateway_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="ikuai_gateway_not_found")
    return dict(row)


def public_ikuai_gateway(row: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    try:
        payload = json.loads(row.get("payload_json") or "{}")
    except json.JSONDecodeError:
        payload = {}
    return {
        "id": row["id"],
        "name": row["name"],
        "base_url": row["base_url"],
        "status": row["status"],
        "notes": row["notes"],
        "last_status": row.get("last_status", ""),
        "username_masked": mask(decrypt_text(row.get("username_enc"))),
        "has_password": bool(row.get("password_enc")),
        "summary": payload,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_linux_server(server_id: int) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("select * from linux_servers where id=?", (server_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="linux_server_not_found")
    return dict(row)


def public_linux_server(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "host": row["host"],
        "port": row["port"],
        "status": row["status"],
        "last_status": row.get("last_status", ""),
        "last_message": row.get("last_message", ""),
        "fingerprint": row.get("fingerprint", ""),
        "notes": row["notes"],
        "username_masked": mask(decrypt_text(row.get("username_enc"))),
        "has_password": bool(row.get("password_enc")),
        "has_private_key": bool(row.get("private_key_enc")),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def linux_server_config(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "host": row["host"],
        "port": row["port"],
        "username": decrypt_text(row.get("username_enc")) or "",
        "password": decrypt_text(row.get("password_enc")) or "",
        "private_key": decrypt_text(row.get("private_key_enc")) or "",
        "private_key_passphrase": decrypt_text(row.get("private_key_passphrase_enc")) or "",
    }


def update_linux_server_status(server_id: int, status_value: str, message: str = "", fingerprint: str = "") -> None:
    with connect() as conn:
        conn.execute(
            """
            update linux_servers
            set last_status=?, last_message=?, fingerprint=coalesce(nullif(?, ''), fingerprint), updated_at=current_timestamp
            where id=?
            """,
            (status_value, message[:500], fingerprint, server_id),
        )


def ensure_linux_fingerprint(server: dict[str, Any], observed: str) -> None:
    fingerprint = (observed or "").strip()
    if not fingerprint:
        return
    expected = (server.get("fingerprint") or "").strip()
    server_id = int(server["id"])
    if expected and expected != fingerprint:
        message = "SSH 主机指纹已变化。为避免连接到错误服务器，请编辑服务器资料后重新测试连接。"
        update_linux_server_status(server_id, "failed", message)
        record_operation(None, "linux", str(server_id), "ssh_fingerprint", "failed", f"{expected} -> {fingerprint}")
        raise HTTPException(status_code=409, detail=message)
    if not expected:
        update_linux_server_status(server_id, "ready", "首次记录 SSH 主机指纹", fingerprint)


def l2tp_default_users_config() -> str:
    return "\n".join([
        "# L2TP VPN 用户配置",
        "# 每行一个账号，使用英文逗号分隔。以 # 开头的行会被忽略。",
        "#",
        "# 字段格式：",
        "# 账号,密码,出口虚拟内网IP,共享连接数,客户端内网IP或IP段,公网IP备注",
        "#",
        "# 字段说明：",
        "# 账号：VPN 登录用户名，只能使用字母、数字、下划线、点、@、中横线。",
        "# 密码：VPN 登录密码。",
        "# 出口虚拟内网IP：可留空；填写后，该账号的流量会用这个服务器本地 VIP 做出口 SNAT。",
        "# 共享连接数：同一账号允许同时在线的数量，默认 1。",
        "# 客户端内网IP或IP段：建议留空；留空时由 xl2tpd 从全局客户端地址池自动分配。",
        "# 只有客户端能主动指定固定地址，或你明确要限制某个账号可用地址时，才填写单个 IP、CIDR 网段或 IP 范围。",
        "# 注意：xl2tpd 在知道账号名前先分配客户端 IP，所以留空时不会按账号自动预留不重叠网段。",
        "# 公网IP备注：只用于人工识别，例如对应的 EIP，不参与系统配置。",
        "#",
        "# 示例：",
        "# DF31,112233..,192.168.0.101,254,,主网卡公网IP",
        "# DF32,112233..,192.168.0.102,20,,虚拟IP对应公网IP",
        "# DF33,112233..,192.168.0.103,20,,虚拟IP对应公网IP",
        "# 上面三行会共用 VPN_CLIENT_POOL 客户端地址池；每个账号的出口由第 3 列内网 IP 决定。",
        "",
    ])


def _decode_payload(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("payload_json")
    if isinstance(value, dict):
        return value
    try:
        data = json.loads(value or "{}")
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _flatten_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return ", ".join(_flatten_text(item) for item in value if item is not None)
    if isinstance(value, dict):
        parts = []
        for key in ("name", "id", "instanceID", "instanceId", "resourceID", "eipID", "eipId", "ip", "eipAddress"):
            if value.get(key):
                parts.append(str(value.get(key)))
        return " ".join(parts) if parts else json.dumps(value, ensure_ascii=False)
    return str(value)


def _first_payload_value(payload: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, "", []):
            return value
    return ""


def _resource_ip(payload: dict[str, Any]) -> str:
    return str(_first_payload_value(payload, ["ip", "ipv4", "ipAddress", "private_ip", "privateIP", "public_ip", "publicIP", "eipAddress"]) or "")


def _payload_private_ips(payload: dict[str, Any]) -> list[str]:
    result: list[str] = []

    def add(value: Any) -> None:
        if value in (None, "", []):
            return
        if isinstance(value, dict):
            for key in ("ip", "ipv4", "ipAddress", "private_ip", "privateIP", "privateIp", "IPv4Address", "addr", "address"):
                add(value.get(key))
            return
        if isinstance(value, list):
            for item in value:
                add(item)
            return
        text = str(value).strip()
        for sep in ("，", ";", "；", "\n", "\r"):
            text = text.replace(sep, ",")
        for item in text.split(","):
            ip = item.strip().split("/", 1)[0]
            if ip and ip not in result:
                result.append(ip)

    for key in ("private_ip", "privateIP", "privateIp", "ipAddress", "IPv4Address", "fixedIpList", "fixedIPList", "privateIpList", "privateIPList"):
        add(payload.get(key))
    for card in payload.get("networkCardList") if isinstance(payload.get("networkCardList"), list) else []:
        if not isinstance(card, dict):
            continue
        for key in ("IPv4Address", "ipAddress", "privateIP", "privateIp", "fixedIpList", "fixedIPList", "privateIpList", "privateIPList"):
            add(card.get(key))
    return result


def _payload_subnet_ids(payload: dict[str, Any]) -> list[str]:
    result: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)

    for key in ("subnet_id", "subnetID", "subnetId", "subnetUUID", "subnetUuid"):
        add(payload.get(key))
    for card in payload.get("networkCardList") if isinstance(payload.get("networkCardList"), list) else []:
        if not isinstance(card, dict):
            continue
        for key in ("subnet_id", "subnetID", "subnetId", "subnetUUID", "subnetUuid"):
            add(card.get(key))
    return result


def _payload_cidr(payload: dict[str, Any]) -> str:
    return str(_first_payload_value(payload, ["cidr", "CIDR", "subnetCIDR", "subnetCidr"]) or "")


def _ip_in_cidr(ip_value: str, cidr: str) -> bool:
    try:
        return ipaddress.ip_address(str(ip_value).split("/", 1)[0]) in ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return False


def _resource_in_subnet_scope(
    account_id: str,
    region: str,
    payload: dict[str, Any],
    selected_subnet_refs: set[tuple[str, str, str]],
    selected_subnet_cidrs: set[tuple[str, str, str]],
) -> bool:
    if not selected_subnet_refs and not selected_subnet_cidrs:
        return False
    for subnet_id in _payload_subnet_ids(payload):
        if (account_id, region, subnet_id) in selected_subnet_refs:
            return True
    ips = _payload_private_ips(payload)
    fallback_ip = _resource_ip(payload)
    if fallback_ip:
        ips.append(fallback_ip)
    for ip_value in dict.fromkeys(ips):
        if any(
            scope_account == account_id and scope_region == region and _ip_in_cidr(ip_value, cidr)
            for scope_account, scope_region, cidr in selected_subnet_cidrs
        ):
            return True
    return False


def _public_ips(value: Any) -> list[str]:
    result: list[str] = []
    for ip_value in re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", str(value or "")):
        try:
            ipaddress.ip_address(ip_value)
        except ValueError:
            continue
        if ip_value not in result:
            result.append(ip_value)
    return result


def _linux_l2tp_scan_script(scan_data: dict[str, Any]) -> str:
    probes: dict[str, list[str]] = {}
    for item in scan_data.get("probe_items") or scan_data.get("items") or []:
        if not isinstance(item, dict):
            continue
        private_ip = str(item.get("private_ip") or "-")
        source = str(item.get("source") or "资源")
        account_name = str(item.get("account_name") or "-")
        for public_ip in _public_ips(item.get("public_ip") or item.get("bound_eips") or ""):
            label = f"内网 {private_ip} -> 公网 {public_ip} | {source} | {account_name}"
            probes.setdefault(public_ip, [])
            if label not in probes[public_ip]:
                probes[public_ip].append(label)
    lines = [
        "probe(){ p=\"$1\"; shift; printf '%s\\n' \"$@\"; if ping -c 2 -W 1 \"$p\" >/dev/null 2>&1; then echo \"在线：公网 $p\"; else echo \"离线或禁止ICMP：公网 $p\"; fi; }",
        "echo '===== 服务器本机 IPv4 / VIP 地址 ====='",
        "ip -o -4 addr show scope global | awk '{print $2, $4}'",
        "echo",
        "echo '===== 路由表 ====='",
        "ip route",
        "echo",
        "echo '===== 公网 IP 在线探测（按平台映射 ping） ====='",
    ]
    for public_ip, labels in probes.items():
        lines.append(f"probe {shlex.quote(public_ip)} {' '.join(shlex.quote(label) for label in labels)}")
    if not probes:
        lines.append("echo '没有可 ping 的公网 IP。请先同步云主机、弹性IP、虚拟IP资源，或确认资源已绑定公网/EIP。'")
    return "\n".join(lines) + "\n"


def _l2tp_vip_candidates(server: dict[str, Any]) -> dict[str, Any]:
    with connect() as conn:
        account_rows = conn.execute("select id, name from ctyun_accounts").fetchall()
        vip_rows = conn.execute("select * from resources where resource_type='vip' order by synced_at desc").fetchall()
        eip_rows = conn.execute("select * from resources where resource_type='eip'").fetchall()
        ecs_rows = conn.execute("select * from resources where resource_type='ecs'").fetchall()
        subnet_rows = conn.execute("select * from resources where resource_type='subnet'").fetchall()
    account_names = {int(row["id"]): row["name"] for row in account_rows}
    subnet_records: list[dict[str, Any]] = []
    for row in subnet_rows:
        data = dict(row)
        payload = _decode_payload(data)
        account_id = str(data.get("account_id") or "")
        region = str(data.get("region") or "")
        cidr = _payload_cidr(payload)
        refs = {str(data.get("provider_id") or "")}
        for key in ("id", "ID", "subnetID", "subnetId", "subnet_id", "resourceID", "resourceId"):
            if payload.get(key):
                refs.add(str(payload.get(key)))
        subnet_records.append({
            "account_id": account_id,
            "region": region,
            "cidr": cidr,
            "refs": {ref for ref in refs if ref},
            "name": data.get("name") or payload.get("name") or "",
        })
    eip_by_ref: dict[str, dict[str, Any]] = {}
    for row in eip_rows:
        data = dict(row)
        payload = _decode_payload(data)
        refs = {str(data.get("provider_id") or ""), str(data.get("name") or "")}
        for key in ("id", "eipID", "eipId", "resourceID", "resourceId"):
            if payload.get(key):
                refs.add(str(payload.get(key)))
        for ref in refs:
            if ref:
                eip_by_ref[ref] = {"row": data, "payload": payload}

    server_host = str(server.get("host") or "").strip()
    selected_ecs_refs: set[str] = set()
    selected_ecs_private_ips: list[dict[str, Any]] = []
    probe_items: list[dict[str, Any]] = []
    ecs_records: list[dict[str, Any]] = []
    selected_contexts: set[tuple[str, str]] = set()
    selected_subnet_refs: set[tuple[str, str, str]] = set()
    selected_subnet_cidrs: set[tuple[str, str, str]] = set()
    matched_ecs_count = 0
    for row in ecs_rows:
        data = dict(row)
        payload = _decode_payload(data)
        account_id = str(data.get("account_id") or "")
        region = str(data.get("region") or "")
        values = {
            str(data.get("provider_id") or ""),
            str(data.get("name") or ""),
            str(payload.get("name") or ""),
            str(payload.get("instanceID") or ""),
            str(payload.get("instanceId") or ""),
            str(payload.get("deviceUUID") or ""),
            str(payload.get("deviceUuid") or ""),
            str(payload.get("resourceID") or ""),
            str(payload.get("resourceId") or ""),
            str(payload.get("uuid") or ""),
            str(payload.get("id") or ""),
            str(payload.get("private_ip") or payload.get("privateIP") or ""),
            str(payload.get("public_ip") or payload.get("publicIP") or ""),
            str(payload.get("ip") or ""),
        }
        matched_to_server = bool(server_host and server_host in values)
        ecs_records.append({
            "data": data,
            "payload": payload,
            "values": values,
            "matched_to_server": matched_to_server,
            "account_id": account_id,
            "region": region,
        })
        if matched_to_server:
            matched_ecs_count += 1
            selected_contexts.add((account_id, region))
            selected_ecs_refs.update(value for value in values if value)
            for subnet_id in _payload_subnet_ids(payload):
                selected_subnet_refs.add((account_id, region, subnet_id))
            for subnet in subnet_records:
                if subnet["account_id"] != account_id or subnet["region"] != region:
                    continue
                if subnet["refs"] and any(ref in subnet["refs"] for ref in _payload_subnet_ids(payload)):
                    if subnet["cidr"]:
                        selected_subnet_cidrs.add((account_id, region, subnet["cidr"]))
                    for ref in subnet["refs"]:
                        selected_subnet_refs.add((account_id, region, ref))
                    continue
                for private_ip in _payload_private_ips(payload):
                    if subnet["cidr"] and _ip_in_cidr(private_ip, subnet["cidr"]):
                        selected_subnet_cidrs.add((account_id, region, subnet["cidr"]))
                        for ref in subnet["refs"]:
                            selected_subnet_refs.add((account_id, region, ref))

    excluded_ecs_count = 0
    for record in ecs_records:
        data = record["data"]
        payload = record["payload"]
        account_id = record["account_id"]
        region = record["region"]
        if not _resource_in_subnet_scope(account_id, region, payload, selected_subnet_refs, selected_subnet_cidrs):
            excluded_ecs_count += 1
            continue
        public_ip = str(payload.get("public_ip") or payload.get("publicIP") or payload.get("ip") or "")
        ecs_item_refs = _flatten_text([data.get("provider_id"), data.get("name"), payload.get("name")])
        for private_ip in _payload_private_ips(payload):
            item = {
                "account_id": data.get("account_id"),
                "account_name": account_names.get(int(data.get("account_id") or 0), ""),
                "region": data.get("region") or "",
                "provider_id": data.get("provider_id") or "",
                "name": data.get("name") or private_ip,
                "private_ip": private_ip,
                "public_ip": public_ip,
                "bound_instances": ecs_item_refs,
                "bound_eips": public_ip,
                "matched_to_server": bool(record["matched_to_server"]),
                "source": "云主机私网IP",
                "status": data.get("status") or payload.get("status") or "",
                "synced_at": data.get("synced_at") or "",
            }
            probe_items.append(item)
            if record["matched_to_server"]:
                selected_ecs_private_ips.append(item)

    result: list[dict[str, Any]] = []
    seen_private_ips: set[str] = set()
    unmatched_vip_count = 0
    for row in vip_rows:
        data = dict(row)
        payload = _decode_payload(data)
        account_id = str(data.get("account_id") or "")
        region = str(data.get("region") or "")
        if not _resource_in_subnet_scope(account_id, region, payload, selected_subnet_refs, selected_subnet_cidrs):
            unmatched_vip_count += 1
            continue
        private_ip = _resource_ip(payload) or str(data.get("name") or "")
        bound_instances = _flatten_text(_first_payload_value(payload, ["bound_instances", "boundInstances", "instanceInfo", "instance_info", "ecs", "server"]))
        bound_eips = _flatten_text(_first_payload_value(payload, ["bound_eips", "boundEips", "networkInfo", "network_info", "eip", "eips"]))
        public_ip = str(_first_payload_value(payload, ["public_ip", "publicIP", "eipAddress", "eip_ip"]) or "")
        if not public_ip:
            for ref, item in eip_by_ref.items():
                if ref and ref in bound_eips:
                    public_ip = _resource_ip(item["payload"]) or str(item["row"].get("name") or "")
                    if public_ip:
                        break
        matched = bool(selected_ecs_refs and any(ref and ref in bound_instances for ref in selected_ecs_refs))
        vip_item = {
            "account_id": data.get("account_id"),
            "account_name": account_names.get(int(data.get("account_id") or 0), ""),
            "region": data.get("region") or "",
            "provider_id": data.get("provider_id") or "",
            "name": data.get("name") or private_ip,
            "private_ip": private_ip,
            "public_ip": public_ip,
            "bound_instances": bound_instances,
            "bound_eips": bound_eips,
            "matched_to_server": matched,
            "source": "虚拟IP",
            "status": data.get("status") or payload.get("status") or "",
            "synced_at": data.get("synced_at") or "",
        }
        probe_items.append(vip_item)
        if not matched:
            unmatched_vip_count += 1
            continue
        if private_ip:
            seen_private_ips.add(private_ip)
        result.append(vip_item)
    for item in selected_ecs_private_ips:
        private_ip = str(item.get("private_ip") or "")
        if not private_ip or private_ip in seen_private_ips:
            continue
        seen_private_ips.add(private_ip)
        result.append(item)
    result.sort(key=lambda item: (not item["matched_to_server"], str(item["account_name"]), str(item["region"]), str(item["private_ip"])))
    return {
        "items": result,
        "probe_items": probe_items,
        "matched_ecs_count": matched_ecs_count,
        "excluded_ecs_count": excluded_ecs_count,
        "unmatched_vip_count": unmatched_vip_count,
        "scan_scopes": [
            {"account_id": account_id, "region": region}
            for account_id, region in sorted(selected_contexts)
        ],
        "subnet_scopes": [
            {"account_id": account_id, "region": region, "subnet_id": subnet_id}
            for account_id, region, subnet_id in sorted(selected_subnet_refs)
        ],
        "subnet_cidrs": [
            {"account_id": account_id, "region": region, "cidr": cidr}
            for account_id, region, cidr in sorted(selected_subnet_cidrs)
        ],
        "server_host": server_host,
    }


def _sudo_script(command_body: str) -> str:
    return "set -e\nSUDO=sudo\nif [ \"$(id -u)\" -eq 0 ]; then SUDO=\"\"; fi\n" + command_body


def _l2tp_install_env(body: LinuxL2tpInstallBody) -> str:
    values = {
        "VPN_L2TP_PORT": str(body.port),
        "VPN_MTU": str(body.mtu),
        "VPN_MRU": str(body.mru),
    }
    if body.random_psk:
        values["VPN_ENABLE_IPSEC"] = "1"
        values["VPN_RANDOM_PSK"] = "1"
    elif body.psk.strip():
        values["VPN_ENABLE_IPSEC"] = "1"
        values["VPN_IPSEC_PSK"] = body.psk.strip()
    else:
        values["VPN_ENABLE_IPSEC"] = "0"
    return " ".join(f"{key}={shlex.quote(value)}" for key, value in values.items())


def build_ikuai_client(row: dict[str, Any]) -> IkuaiClient:
    username = decrypt_text(row.get("username_enc")) or ""
    password = decrypt_text(row.get("password_enc")) or ""
    if not username or not password:
        raise HTTPException(status_code=422, detail="ikuai_credentials_missing")
    try:
        return IkuaiClient(row["base_url"], username, password)
    except IkuaiClientError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def refresh_ikuai_gateway_status(gateway_id: int) -> dict[str, Any]:
    gateway = get_ikuai_gateway(gateway_id)
    client = build_ikuai_client(gateway)
    try:
        summary = gateway_summary(client)
        with connect() as conn:
            conn.execute(
                """
                update ikuai_gateways
                set last_status='ready', payload_json=?, updated_at=current_timestamp
                where id=?
                """,
                (json.dumps(summary, ensure_ascii=False), gateway_id),
            )
        record_operation(None, "ikuai", str(gateway_id), "refresh_gateway", "success", gateway["name"])
        return summary
    except IkuaiClientError as exc:
        with connect() as conn:
            conn.execute(
                "update ikuai_gateways set last_status=?, updated_at=current_timestamp where id=?",
                (str(exc)[:200], gateway_id),
            )
        record_operation(None, "ikuai", str(gateway_id), "refresh_gateway", "failed", str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def record_operation(account_id: int | None, resource_type: str | None, resource_id: str | None, action: str, status: str, message: str = "") -> None:
    with connect() as conn:
        conn.execute(
            "insert into operations(account_id, resource_type, resource_id, action, status, message) values(?, ?, ?, ?, ?, ?)",
            (account_id, resource_type, resource_id, action, status, message),
        )


def persist_account_session(account_id: int, result: dict[str, Any]) -> None:
    cookie_state = result.get("cookie_state_enc", "")
    provider_account_id = result.get("provider_account_id", "")
    with connect() as conn:
        if cookie_state:
            conn.execute(
                "update ctyun_accounts set cookie_state_enc=?, updated_at=current_timestamp where id=?",
                (cookie_state, account_id),
            )
        if provider_account_id:
            conn.execute(
                """
                update ctyun_accounts
                set provider_account_id=?, updated_at=current_timestamp
                where id=?
                """,
                (provider_account_id, account_id),
            )


def persist_finance(account_id: int, result: dict[str, Any]) -> None:
    persist_account_session(account_id, result)
    status = result.get("status", "")
    available = result.get("available")
    owe = result.get("owe")
    has_valid_balance = status == "ready" and available is not None
    with connect() as conn:
        if has_valid_balance:
            conn.execute(
                """
                insert into account_finance(account_id, available, owe, status, message, updated_at)
                values(?, ?, ?, ?, ?, current_timestamp)
                on conflict(account_id) do update set
                  available=excluded.available, owe=excluded.owe, status=excluded.status,
                  message=excluded.message, updated_at=current_timestamp
                """,
                (
                    account_id,
                    available,
                    owe,
                    status,
                    result.get("message", ""),
                ),
            )
            return
        conn.execute(
            """
            insert into account_finance(account_id, available, owe, status, message, updated_at)
            values(?, null, null, ?, ?, current_timestamp)
            on conflict(account_id) do update set
              status=excluded.status, message=excluded.message, updated_at=current_timestamp
            """,
            (
                account_id,
                status,
                result.get("message", ""),
            ),
        )


async def discover_provider_account_id(account: dict[str, Any]) -> str:
    account_id = int(account["id"])
    if account_id in account_id_verified:
        return str(account.get("provider_account_id") or "")
    try:
        provider_account_id = await asyncio.to_thread(
            build_client(account, settings.ctyun_mode).get_account_id
        )
    except Exception:
        return ""
    if not provider_account_id:
        return ""
    with connect() as conn:
        conn.execute(
            "update ctyun_accounts set provider_account_id=?, updated_at=current_timestamp where id=?",
            (provider_account_id, account_id),
        )
    account["provider_account_id"] = provider_account_id
    account_id_verified.add(account_id)
    record_operation(
        account_id,
        "account",
        str(account_id),
        "discover_account_id",
        "success",
        provider_account_id,
    )
    return provider_account_id


async def finance_refresh_loop() -> None:
    await asyncio.sleep(2)
    while True:
        with connect() as conn:
            account_ids = [
                int(row["id"])
                for row in conn.execute(
                    """
                    select a.id
                    from ctyun_accounts a
                    left join account_finance f on f.account_id=a.id
                    where a.status='enabled'
                      and (f.updated_at is null or datetime(f.updated_at) <= datetime('now', ?))
                    order by a.id
                    """,
                    (f"-{settings.finance_refresh_seconds} seconds",),
                ).fetchall()
            ]
        for account_id in account_ids:
            await queue_finance_refresh(account_id)
        await asyncio.sleep(settings.finance_refresh_seconds)


async def queue_finance_refresh(account_id: int) -> None:
    account_id = int(account_id)
    async with finance_refresh_pending_lock:
        if account_id in finance_refresh_pending:
            return
        finance_refresh_pending.add(account_id)
    await finance_refresh_queue.put(account_id)


async def finance_refresh_worker(worker_index: int) -> None:
    while True:
        account_id = await finance_refresh_queue.get()
        try:
            account = get_account(account_id)
            result = await get_finance(account)
            if result.get("status") == "interactive":
                continue
            persist_finance(account_id, result)
            if result.get("status") != "ready":
                record_operation(
                    account_id,
                    "finance",
                    None,
                    "keepalive",
                    result.get("status", "unknown"),
                    result.get("message", ""),
                )
            await discover_provider_account_id(account)
        except Exception as exc:
            record_operation(account_id, "finance", None, "keepalive", "failed", str(exc))
        finally:
            async with finance_refresh_pending_lock:
                finance_refresh_pending.discard(account_id)
            finance_refresh_queue.task_done()


async def cookie_keepalive_loop() -> None:
    await asyncio.sleep(10)
    while True:
        with connect() as conn:
            account_ids = [
                int(row["id"])
                for row in conn.execute(
                    """
                    select id
                    from ctyun_accounts
                    where status='enabled' and coalesce(cookie_state_enc, '') != ''
                    order by id
                    """
                ).fetchall()
            ]
        for account_id in account_ids:
            await queue_cookie_keepalive(account_id)
        await asyncio.sleep(settings.cookie_keepalive_seconds)


async def queue_cookie_keepalive(account_id: int) -> None:
    account_id = int(account_id)
    async with cookie_keepalive_pending_lock:
        if account_id in cookie_keepalive_pending:
            return
        cookie_keepalive_pending.add(account_id)
    await cookie_keepalive_queue.put(account_id)


async def cookie_keepalive_worker(worker_index: int) -> None:
    while True:
        account_id = await cookie_keepalive_queue.get()
        try:
            account = get_account(account_id)
            result = await keepalive_saved_cookie(account)
            provider_account_id = str(result.get("provider_account_id") or "")
            if provider_account_id:
                with connect() as conn:
                    conn.execute(
                        "update ctyun_accounts set provider_account_id=?, updated_at=current_timestamp where id=?",
                        (provider_account_id, account_id),
                    )
                account_id_verified.add(account_id)
            record_operation(
                account_id,
                "account",
                str(account_id),
                "cookie_keepalive",
                result.get("status", "unknown"),
                result.get("message", ""),
            )
        except Exception as exc:
            record_operation(account_id, "account", str(account_id), "cookie_keepalive", "failed", str(exc))
        finally:
            async with cookie_keepalive_pending_lock:
                cookie_keepalive_pending.discard(account_id)
            cookie_keepalive_queue.task_done()


async def refresh_account_finance_once(account_id: int, delay: float = 1, force_api: bool = False) -> None:
    await asyncio.sleep(delay)
    try:
        account = get_account(account_id)
        result = await get_finance(account, force_api=force_api)
        if result.get("status") == "interactive":
            return
        persist_finance(account_id, result)
        record_operation(
            account_id,
            "finance",
            None,
            "account_initialize",
            result.get("status", "unknown"),
            result.get("message", ""),
        )
        await discover_provider_account_id(account)
    except Exception as exc:
        record_operation(account_id, "finance", None, "account_initialize", "failed", str(exc))


async def recharge_prewarm_once(reason: str = "recharge_prewarm", delay: float = 0) -> dict[str, Any]:
    if delay > 0:
        await asyncio.sleep(delay)
    if not settings.recharge_prewarm_enabled:
        return {"status": "disabled", "message": "充值页预热未启用", "accounts": 0}
    if recharge_prewarm_lock.locked():
        return {"status": "skipped", "message": "充值页预热已在执行", "accounts": 0}
    async with recharge_prewarm_lock:
        with connect() as conn:
            accounts = [
                dict(row)
                for row in conn.execute(
                    "select * from ctyun_accounts where status='enabled' order by id"
                ).fetchall()
            ]
        if not accounts:
            return {"status": "ready", "message": "没有需要预热的天翼云账号", "accounts": 0}

        semaphore = asyncio.Semaphore(settings.recharge_prewarm_workers)
        results: list[dict[str, Any]] = []

        async def prewarm_one(account: dict[str, Any]) -> None:
            async with semaphore:
                account_id = int(account["id"])
                try:
                    result = await login_and_open_recharge(account)
                    persist_account_session(account_id, result)
                    status = result.get("status", "unknown")
                    record_operation(
                        account_id,
                        "recharge",
                        None,
                        reason,
                        status,
                        result.get("message", ""),
                    )
                    results.append({"account_id": account_id, "status": status})
                except Exception as exc:
                    record_operation(account_id, "recharge", None, reason, "failed", str(exc))
                    results.append({"account_id": account_id, "status": "failed"})

        await asyncio.gather(*(prewarm_one(account) for account in accounts))
        ready = sum(1 for item in results if item.get("status") == "ready")
        return {
            "status": "ready",
            "message": f"充值页预热完成：{ready}/{len(accounts)} 个账号就绪",
            "accounts": len(accounts),
            "ready": ready,
        }


async def recharge_prewarm_loop() -> None:
    await asyncio.sleep(settings.recharge_prewarm_startup_delay_seconds)
    while True:
        try:
            await recharge_prewarm_once("scheduled_recharge_prewarm")
        except Exception as exc:
            record_operation(None, "recharge", None, "scheduled_recharge_prewarm", "failed", str(exc))
        await asyncio.sleep(settings.recharge_prewarm_interval_seconds)


async def browser_session_cleanup_loop() -> None:
    await asyncio.sleep(settings.browser_session_cleanup_seconds)
    while True:
        try:
            result = await cleanup_idle_browser_sessions()
            if result.get("closed"):
                record_operation(
                    None,
                    "browser",
                    None,
                    "idle_cleanup",
                    "success",
                    f"已关闭 {result.get('closed')} 个空闲网页登录会话",
                )
        except Exception as exc:
            record_operation(None, "browser", None, "idle_cleanup", "failed", str(exc))
        await asyncio.sleep(settings.browser_session_cleanup_seconds)


def schedule_recharge_prewarm(reason: str, delay: float = 0) -> bool:
    if not settings.recharge_prewarm_enabled:
        return False
    tasks = getattr(app.state, "recharge_prewarm_tasks", None)
    if tasks is None:
        tasks = set()
        app.state.recharge_prewarm_tasks = tasks
    task = asyncio.create_task(recharge_prewarm_once(reason, delay=delay))
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return True


@app.get("/")
def index() -> FileResponse:
    return FileResponse(settings.static_dir / "index.html", headers=NO_CACHE_HEADERS)


@app.get("/install-l2tp-server.sh")
def l2tp_install_script() -> FileResponse:
    if not L2TP_INSTALL_SCRIPT.exists():
        raise HTTPException(status_code=404, detail="install-l2tp-server.sh not found")
    return FileResponse(
        L2TP_INSTALL_SCRIPT,
        media_type="text/x-shellscript; charset=utf-8",
        filename="install-l2tp-server.sh",
        headers=NO_CACHE_HEADERS,
    )


@app.get("/ctyun-console-bridge.zip")
def ctyun_console_bridge_zip(user: dict = Depends(require_user)) -> Response:
    extension_dir = settings.static_dir / "ctyun-console-bridge" / "extension"
    if not extension_dir.exists():
        raise HTTPException(status_code=404, detail="console bridge extension not found")
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(extension_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(extension_dir).as_posix())
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={
            **NO_CACHE_HEADERS,
            "Content-Disposition": 'attachment; filename="ctyun-console-bridge.zip"',
        },
    )


@app.get("/api/version")
def version() -> dict[str, Any]:
    return {
        "version": APP_VERSION,
        "build_time": BUILD_TIME,
        "ctyun_mode": settings.ctyun_mode,
        "encryption_key_status": encryption_key_status(),
    }


@app.get("/api/runtime/status")
def runtime_status(user: dict = Depends(require_user)) -> dict[str, Any]:
    return {
        "background_sync_enabled": settings.background_sync_enabled,
        "finance_refresh_enabled": settings.finance_refresh_enabled,
        "finance_refresh_queue": finance_refresh_queue.qsize(),
        "finance_refresh_pending": len(finance_refresh_pending),
        "cookie_keepalive_enabled": settings.cookie_keepalive_enabled,
        "cookie_keepalive_queue": cookie_keepalive_queue.qsize(),
        "cookie_keepalive_pending": len(cookie_keepalive_pending),
        "browser": browser_session_stats(),
    }


@app.post("/api/login")
def login(body: LoginBody, response: Response) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("select * from users where username = ?", (body.username,)).fetchone()
    if not row or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="bad_credentials")
    token = sign_session({"sub": body.username})
    response.set_cookie(
        "ctyun_manager_session",
        token,
        httponly=True,
        samesite="lax",
        secure=settings.public_url.lower().startswith("https://"),
        max_age=86400,
    )
    return {"ok": True, "username": body.username}


@app.post("/api/recharge/prewarm")
async def recharge_prewarm(user: dict = Depends(require_user)) -> dict[str, Any]:
    queued = schedule_recharge_prewarm("login_recharge_prewarm", delay=1)
    return {
        "status": "queued" if queued else "disabled",
        "message": "充值页后台预热已排队" if queued else "充值页预热未启用",
    }


@app.post("/api/logout")
def logout(response: Response) -> dict[str, Any]:
    response.delete_cookie("ctyun_manager_session")
    return {"ok": True}


@app.get("/api/me")
def me(user: dict = Depends(require_user)) -> dict[str, Any]:
    return {
        "username": user["sub"],
        "ctyun_mode": settings.ctyun_mode,
        "version": APP_VERSION,
        "encryption_key_status": encryption_key_status(),
    }


@app.get("/api/accounts")
def list_accounts(user: dict = Depends(require_user)) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("select * from ctyun_accounts order by id desc").fetchall()
    return [public_account(dict(row)) for row in rows]


def _safe_rustdesk_payload(body: RustDeskCustomizeBody) -> dict[str, Any]:
    payload = body.dict()
    payload.pop("token", None)
    payload.pop("icon_data_url", None)
    return payload


def _public_rustdesk_job(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": job["id"],
        "status": job["status"],
        "message": job.get("message", ""),
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "payload": job.get("payload", {}),
        "logs": list(job.get("logs", []))[-240:],
        "result": job.get("result"),
        "error": job.get("error", ""),
    }


def _json_or_default(value: str | None, default: Any) -> Any:
    if not value:
        return copy.deepcopy(default)
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return copy.deepcopy(default)


def _rustdesk_job_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "status": row["status"],
        "message": row.get("message", ""),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "payload": _json_or_default(row.get("payload_json"), {}),
        "logs": _json_or_default(row.get("logs_json"), []),
        "result": _json_or_default(row.get("result_json"), None),
        "error": row.get("error", ""),
    }


def persist_rustdesk_job(job: dict[str, Any]) -> None:
    with connect() as conn:
        conn.execute(
            """
            insert into rustdesk_jobs(id, status, message, payload_json, logs_json, result_json, error, created_at, updated_at)
            values(?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(id) do update set
              status=excluded.status,
              message=excluded.message,
              payload_json=excluded.payload_json,
              logs_json=excluded.logs_json,
              result_json=excluded.result_json,
              error=excluded.error,
              updated_at=excluded.updated_at
            """,
            (
                job["id"],
                job["status"],
                job.get("message", ""),
                json.dumps(job.get("payload") or {}, ensure_ascii=False),
                json.dumps(list(job.get("logs") or [])[-300:], ensure_ascii=False),
                json.dumps(job.get("result"), ensure_ascii=False) if job.get("result") is not None else None,
                job.get("error", ""),
                job["created_at"],
                job["updated_at"],
            ),
        )


def restore_rustdesk_jobs() -> None:
    with connect() as conn:
        rows = conn.execute("select * from rustdesk_jobs order by updated_at desc limit 30").fetchall()
    restored: dict[str, dict[str, Any]] = {}
    for row in rows:
        job = _rustdesk_job_from_row(dict(row))
        if job.get("status") in {"queued", "running"}:
            job["status"] = "failed"
            job["error"] = "服务重启后任务已中断，请重新提交"
            job["message"] = job["error"]
            job["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            job.setdefault("logs", []).append({"time": time.strftime("%H:%M:%S"), "message": job["error"]})
            persist_rustdesk_job(job)
        restored[job["id"]] = job
    with rustdesk_jobs_lock:
        rustdesk_jobs.clear()
        rustdesk_jobs.update(restored)


def _append_rustdesk_log(job_id: str, message: str) -> None:
    text = str(message or "").strip()
    if not text:
        return
    changed_job: dict[str, Any] | None = None
    with rustdesk_jobs_lock:
        job = rustdesk_jobs.get(job_id)
        if not job:
            return
        job["logs"].append({"time": time.strftime("%H:%M:%S"), "message": text})
        job["logs"] = job["logs"][-300:]
        job["message"] = text.splitlines()[-1][:300]
        job["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        changed_job = copy.deepcopy(job)
    if changed_job:
        persist_rustdesk_job(changed_job)


def _run_rustdesk_job(job_id: str, payload: dict[str, Any]) -> None:
    changed_job: dict[str, Any] | None = None
    with rustdesk_jobs_lock:
        if job_id in rustdesk_jobs:
            rustdesk_jobs[job_id]["status"] = "running"
            rustdesk_jobs[job_id]["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            changed_job = copy.deepcopy(rustdesk_jobs[job_id])
    if changed_job:
        persist_rustdesk_job(changed_job)
    try:
        result = customize_rustdesk(payload, lambda message: _append_rustdesk_log(job_id, message))
        changed_job = None
        with rustdesk_jobs_lock:
            job = rustdesk_jobs[job_id]
            job["status"] = "success"
            job["result"] = result
            job["message"] = "RustDesk 定制仓库写入完成"
            job["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            changed_job = copy.deepcopy(job)
        persist_rustdesk_job(changed_job)
        record_operation(None, "rustdesk", result.get("repo", ""), "customize", "success", result.get("actions_url", ""))
    except RustDeskCustomizeError as exc:
        changed_job = None
        with rustdesk_jobs_lock:
            job = rustdesk_jobs.get(job_id)
            if job:
                job["status"] = "failed"
                job["error"] = str(exc)
                job["message"] = str(exc)
                job["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                changed_job = copy.deepcopy(job)
        if changed_job:
            persist_rustdesk_job(changed_job)
        _append_rustdesk_log(job_id, str(exc))
        record_operation(None, "rustdesk", payload.get("repo", ""), "customize", "failed", str(exc))
    except Exception as exc:
        message = f"RustDesk 定制任务异常：{exc}"
        changed_job = None
        with rustdesk_jobs_lock:
            job = rustdesk_jobs.get(job_id)
            if job:
                job["status"] = "failed"
                job["error"] = message
                job["message"] = message
                job["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                changed_job = copy.deepcopy(job)
        if changed_job:
            persist_rustdesk_job(changed_job)
        _append_rustdesk_log(job_id, message)
        record_operation(None, "rustdesk", payload.get("repo", ""), "customize", "failed", message)


@app.get("/api/tools/rustdesk/jobs")
def list_rustdesk_jobs(user: dict = Depends(require_user)) -> list[dict[str, Any]]:
    with rustdesk_jobs_lock:
        jobs = sorted(rustdesk_jobs.values(), key=lambda item: item["created_at"], reverse=True)
        return [_public_rustdesk_job(job) for job in jobs[:20]]


@app.get("/api/tools/rustdesk/jobs/{job_id}")
def get_rustdesk_job(job_id: str, user: dict = Depends(require_user)) -> dict[str, Any]:
    with rustdesk_jobs_lock:
        job = rustdesk_jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="rustdesk_job_not_found")
        return _public_rustdesk_job(job)


@app.post("/api/tools/rustdesk/jobs")
def create_rustdesk_job(body: RustDeskCustomizeBody, user: dict = Depends(require_user)) -> dict[str, Any]:
    payload = body.dict()
    if not body.hide_builtin_server_values:
        raise HTTPException(status_code=422, detail="当前定制方案固定隐藏内置服务器配置值，请保持开启")
    job_id = uuid.uuid4().hex
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    job = {
        "id": job_id,
        "status": "queued",
        "message": "任务已创建，等待后台执行",
        "created_at": now,
        "updated_at": now,
        "payload": _safe_rustdesk_payload(body),
        "logs": [{"time": time.strftime("%H:%M:%S"), "message": "任务已创建，token 仅用于本次后台任务，不会保存"}],
        "result": None,
        "error": "",
    }
    with rustdesk_jobs_lock:
        rustdesk_jobs[job_id] = job
        if len(rustdesk_jobs) > 30:
            for old_id in sorted(rustdesk_jobs, key=lambda key: rustdesk_jobs[key]["created_at"])[:-30]:
                rustdesk_jobs.pop(old_id, None)
    persist_rustdesk_job(job)
    worker = threading.Thread(target=_run_rustdesk_job, args=(job_id, payload), name=f"rustdesk-customize-{job_id[:8]}", daemon=True)
    worker.start()
    return _public_rustdesk_job(job)


@app.get("/api/ikuai/gateways")
def list_ikuai_gateways(user: dict = Depends(require_user)) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("select * from ikuai_gateways order by id desc").fetchall()
    return [public_ikuai_gateway(dict(row)) for row in rows]


@app.get("/api/ikuai/menus")
def list_ikuai_menus(user: dict = Depends(require_user)) -> list[dict[str, Any]]:
    return IKUAI_MENU_GROUPS


@app.post("/api/ikuai/gateways")
def create_ikuai_gateway(body: IkuaiGatewayBody, user: dict = Depends(require_user)) -> dict[str, Any]:
    if not body.name.strip():
        raise HTTPException(status_code=422, detail="请填写网关名称")
    if not body.username or not body.username.strip() or not body.password:
        raise HTTPException(status_code=422, detail="请填写爱快登录账号和密码")
    try:
        base_url = normalize_base_url(body.base_url)
    except IkuaiClientError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    with connect() as conn:
        cur = conn.execute(
            """
            insert into ikuai_gateways(name, base_url, username_enc, password_enc, notes)
            values(?, ?, ?, ?, ?)
            """,
            (body.name.strip(), base_url, encrypt_text(body.username), encrypt_text(body.password), body.notes),
        )
    gateway_id = int(cur.lastrowid)
    record_operation(None, "ikuai", str(gateway_id), "create_gateway", "success", body.name)
    return public_ikuai_gateway(get_ikuai_gateway(gateway_id))


@app.put("/api/ikuai/gateways/{gateway_id}")
def update_ikuai_gateway(gateway_id: int, body: IkuaiGatewayBody, user: dict = Depends(require_user)) -> dict[str, Any]:
    current = get_ikuai_gateway(gateway_id)
    if not body.name.strip():
        raise HTTPException(status_code=422, detail="请填写网关名称")
    try:
        base_url = normalize_base_url(body.base_url)
    except IkuaiClientError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    username = encrypt_text(body.username) if body.username else current.get("username_enc")
    password = encrypt_text(body.password) if body.password else current.get("password_enc")
    with connect() as conn:
        conn.execute(
            """
            update ikuai_gateways
            set name=?, base_url=?, username_enc=?, password_enc=?, notes=?, updated_at=current_timestamp
            where id=?
            """,
            (body.name.strip(), base_url, username, password, body.notes, gateway_id),
        )
    record_operation(None, "ikuai", str(gateway_id), "update_gateway", "success", body.name)
    return public_ikuai_gateway(get_ikuai_gateway(gateway_id))


@app.delete("/api/ikuai/gateways/{gateway_id}")
def delete_ikuai_gateway(gateway_id: int, user: dict = Depends(require_user)) -> dict[str, Any]:
    gateway = get_ikuai_gateway(gateway_id)
    with connect() as conn:
        conn.execute("delete from ikuai_gateways where id=?", (gateway_id,))
    record_operation(None, "ikuai", str(gateway_id), "delete_gateway", "success", gateway["name"])
    return {"ok": True}


@app.post("/api/ikuai/gateways/{gateway_id}/test")
async def test_ikuai_gateway(gateway_id: int, user: dict = Depends(require_user)) -> dict[str, Any]:
    gateway = get_ikuai_gateway(gateway_id)
    client = build_ikuai_client(gateway)
    try:
        result = await asyncio.to_thread(client.login)
        record_operation(None, "ikuai", str(gateway_id), "test_gateway", "success", gateway["name"])
        return {"ok": True, "result": result}
    except IkuaiClientError as exc:
        record_operation(None, "ikuai", str(gateway_id), "test_gateway", "failed", str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/ikuai/gateways/{gateway_id}/refresh")
async def refresh_ikuai_gateway(gateway_id: int, user: dict = Depends(require_user)) -> dict[str, Any]:
    summary = await asyncio.to_thread(refresh_ikuai_gateway_status, gateway_id)
    return {"ok": True, "summary": summary}


@app.get("/api/ikuai/gateways/{gateway_id}/sections/{section}")
async def ikuai_gateway_section(gateway_id: int, section: str, user: dict = Depends(require_user)) -> dict[str, Any]:
    if section not in SECTION_CALLS:
        raise HTTPException(status_code=404, detail="ikuai_section_not_found")
    gateway = get_ikuai_gateway(gateway_id)
    client = build_ikuai_client(gateway)
    candidates = SECTION_CALLS[section]
    errors: list[str] = []
    try:
        for func_name, action, param in candidates:
            try:
                result = await asyncio.to_thread(client.call, func_name, action, param)
                return {
                    "section": section,
                    "result": _sanitize_ikuai_result(result),
                    "matched": {"func_name": func_name, "action": action, "param": param},
                }
            except IkuaiClientError as exc:
                errors.append(f"{func_name}: {str(exc)[:120]}")
        raise IkuaiClientError("；".join(errors) or "当前爱快版本未返回可用数据")
    except IkuaiClientError as exc:
        record_operation(None, "ikuai", str(gateway_id), f"section_{section}", "failed", str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _ikuai_action_value_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True


IKUAI_SENSITIVE_RESULT_KEYS = {
    "passwd",
    "pass",
    "password",
    "secret",
    "radius_key",
    "group_key",
    "ldap_admin_passwd",
    "custom_appkey",
}


def _sanitize_ikuai_result(value: Any) -> Any:
    if isinstance(value, list):
        return [_sanitize_ikuai_result(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _sanitize_ikuai_result(item)
            for key, item in value.items()
            if key not in IKUAI_SENSITIVE_RESULT_KEYS
        }
    return value


def _ikuai_action_param(section: str, action: str, rule: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    fields = rule.get("fields") or []
    allowed_fields = None if fields == "*" else set(fields)
    param = {}
    for key, value in (payload or {}).items():
        if key.startswith("_"):
            continue
        if allowed_fields is None and key in IKUAI_SENSITIVE_RESULT_KEYS:
            continue
        if allowed_fields is not None and key not in allowed_fields:
            continue
        if _ikuai_action_value_present(value):
            param[key] = value
    for secret_key in ("passwd", "secret", "radius_key", "ldap_admin_passwd"):
        if isinstance(param.get(secret_key), str) and not param[secret_key].strip():
            param.pop(secret_key, None)
    param.update(rule.get("inject") or {})
    missing = [key for key in sorted(rule.get("required") or []) if not _ikuai_action_value_present(param.get(key))]
    if missing:
        raise HTTPException(status_code=422, detail=f"{section}/{action} 缺少必要字段：{', '.join(missing)}")
    return param


@app.post("/api/ikuai/gateways/{gateway_id}/sections/{section}/actions")
async def ikuai_gateway_section_action(gateway_id: int, section: str, body: IkuaiSectionActionBody, user: dict = Depends(require_user)) -> dict[str, Any]:
    action_rules = IKUAI_SECTION_ACTIONS.get(section)
    if not action_rules:
        raise HTTPException(status_code=404, detail="当前爱快页面暂未接入可写操作")
    action = body.action.strip()
    rule = action_rules.get(action)
    if not rule:
        raise HTTPException(status_code=422, detail="当前页面不支持该操作")
    param = _ikuai_action_param(section, action, rule, body.payload or {})
    gateway = get_ikuai_gateway(gateway_id)
    client = build_ikuai_client(gateway)
    func_name = str(rule["func_name"])
    if func_name == "__section__":
        candidates = SECTION_CALLS.get(section) or []
        candidate_names = [item[0] for item in candidates]
        requested = str((body.payload or {}).get("_func_name") or "")
        func_name = requested if requested in candidate_names else (candidate_names[0] if candidate_names else "")
        if not func_name:
            raise HTTPException(status_code=422, detail="当前页面没有可编辑接口")
    call_action = str(rule.get("call_action") or action)
    try:
        result = await asyncio.to_thread(client.call, func_name, call_action, param)
        record_operation(None, "ikuai", str(gateway_id), f"{section}_{action}", "success", func_name)
        return {
            "ok": True,
            "result": _sanitize_ikuai_result(result),
            "matched": {"func_name": func_name, "action": call_action, "param_keys": sorted(param.keys())},
        }
    except IkuaiClientError as exc:
        record_operation(None, "ikuai", str(gateway_id), f"{section}_{action}", "failed", str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/ikuai/gateways/{gateway_id}/raw")
async def ikuai_gateway_raw_call(gateway_id: int, body: IkuaiRawCallBody, user: dict = Depends(require_user)) -> dict[str, Any]:
    if not body.func_name.strip():
        raise HTTPException(status_code=422, detail="请填写 func_name")
    gateway = get_ikuai_gateway(gateway_id)
    client = build_ikuai_client(gateway)
    try:
        result = await asyncio.to_thread(client.call, body.func_name.strip(), body.action or "show", body.param or {})
        record_operation(None, "ikuai", str(gateway_id), f"raw_{body.func_name}", "success", body.action)
        return {"result": _sanitize_ikuai_result(result)}
    except IkuaiClientError as exc:
        record_operation(None, "ikuai", str(gateway_id), f"raw_{body.func_name}", "failed", str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _bounded_output(value: str, limit: int = 65536) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n\n[平台提示] 输出超过 {limit} 字符，已截断。"


@app.get("/api/linux/servers")
def list_linux_servers(user: dict = Depends(require_user)) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("select * from linux_servers order by id desc").fetchall()
    return [public_linux_server(dict(row)) for row in rows]


@app.post("/api/linux/servers")
def create_linux_server(body: LinuxServerBody, user: dict = Depends(require_user)) -> dict[str, Any]:
    if not body.name.strip():
        raise HTTPException(status_code=422, detail="请填写服务器名称")
    username = (body.username or "").strip()
    if not username:
        raise HTTPException(status_code=422, detail="请填写 SSH 登录账号")
    if not (body.password or body.private_key):
        raise HTTPException(status_code=422, detail="请填写 SSH 密码或私钥")
    try:
        host = normalize_host(body.host)
    except SSHManagerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if body.port < 1 or body.port > 65535:
        raise HTTPException(status_code=422, detail="SSH 端口必须在 1-65535 之间")
    with connect() as conn:
        cur = conn.execute(
            """
            insert into linux_servers(
              name, host, port, username_enc, password_enc, private_key_enc, private_key_passphrase_enc, notes
            ) values(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                body.name.strip(),
                host,
                body.port,
                encrypt_text(username),
                encrypt_text(body.password),
                encrypt_text(body.private_key),
                encrypt_text(body.private_key_passphrase),
                body.notes,
            ),
        )
    server_id = int(cur.lastrowid)
    record_operation(None, "linux", str(server_id), "create_server", "success", body.name)
    return public_linux_server(get_linux_server(server_id))


@app.put("/api/linux/servers/{server_id}")
def update_linux_server(server_id: int, body: LinuxServerBody, user: dict = Depends(require_user)) -> dict[str, Any]:
    current = get_linux_server(server_id)
    if not body.name.strip():
        raise HTTPException(status_code=422, detail="请填写服务器名称")
    try:
        host = normalize_host(body.host)
    except SSHManagerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if body.port < 1 or body.port > 65535:
        raise HTTPException(status_code=422, detail="SSH 端口必须在 1-65535 之间")
    username_plain = (body.username or "").strip() or (decrypt_text(current.get("username_enc")) or "")
    password_plain = body.password if body.password else (decrypt_text(current.get("password_enc")) or "")
    private_key_plain = body.private_key if body.private_key else (decrypt_text(current.get("private_key_enc")) or "")
    if not username_plain:
        raise HTTPException(status_code=422, detail="请填写 SSH 登录账号")
    if not password_plain and not private_key_plain:
        raise HTTPException(status_code=422, detail="请填写 SSH 密码或私钥")
    username_enc = encrypt_text(username_plain) if body.username else current.get("username_enc")
    password_enc = encrypt_text(body.password) if body.password else current.get("password_enc")
    private_key_enc = encrypt_text(body.private_key) if body.private_key else current.get("private_key_enc")
    passphrase_enc = encrypt_text(body.private_key_passphrase) if body.private_key_passphrase else current.get("private_key_passphrase_enc")
    fingerprint = current.get("fingerprint", "")
    if host != current.get("host") or int(body.port) != int(current.get("port") or 22) or body.username:
        fingerprint = ""
    with connect() as conn:
        conn.execute(
            """
            update linux_servers
            set name=?, host=?, port=?, username_enc=?, password_enc=?, private_key_enc=?,
                private_key_passphrase_enc=?, fingerprint=?, notes=?, updated_at=current_timestamp
            where id=?
            """,
            (
                body.name.strip(),
                host,
                body.port,
                username_enc,
                password_enc,
                private_key_enc,
                passphrase_enc,
                fingerprint,
                body.notes,
                server_id,
            ),
        )
    record_operation(None, "linux", str(server_id), "update_server", "success", body.name)
    return public_linux_server(get_linux_server(server_id))


@app.delete("/api/linux/servers/{server_id}")
def delete_linux_server(server_id: int, user: dict = Depends(require_user)) -> dict[str, Any]:
    server = get_linux_server(server_id)
    with connect() as conn:
        conn.execute("delete from linux_servers where id=?", (server_id,))
    record_operation(None, "linux", str(server_id), "delete_server", "success", server["name"])
    return {"ok": True}


@app.post("/api/linux/servers/{server_id}/test")
async def test_linux_server(server_id: int, user: dict = Depends(require_user)) -> dict[str, Any]:
    server = get_linux_server(server_id)
    try:
        result = await asyncio.to_thread(test_ssh_connection, linux_server_config(server))
        ensure_linux_fingerprint(server, result.get("fingerprint", ""))
        update_linux_server_status(server_id, "ready", "SSH 连接正常", result.get("fingerprint", ""))
        record_operation(None, "linux", str(server_id), "test_ssh", "success", server["name"])
        return {"ok": True, **result}
    except SSHManagerError as exc:
        update_linux_server_status(server_id, "failed", str(exc))
        record_operation(None, "linux", str(server_id), "test_ssh", "failed", str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/linux/servers/{server_id}/command")
async def linux_server_command(server_id: int, body: LinuxCommandBody, user: dict = Depends(require_user)) -> dict[str, Any]:
    server = get_linux_server(server_id)
    try:
        result = await asyncio.to_thread(run_ssh_command, linux_server_config(server), body.command, body.timeout)
        ensure_linux_fingerprint(server, result.get("fingerprint", ""))
        result["stdout"] = _bounded_output(result.get("stdout") or "")
        result["stderr"] = _bounded_output(result.get("stderr") or "")
        status_value = "success" if result.get("exit_status") == 0 else "failed"
        update_linux_server_status(server_id, "ready", f"命令退出码：{result.get('exit_status')}", result.get("fingerprint", ""))
        record_operation(None, "linux", str(server_id), "run_command", status_value, body.command[:160])
        return {"ok": result.get("exit_status") == 0, **result}
    except SSHManagerError as exc:
        update_linux_server_status(server_id, "failed", str(exc))
        record_operation(None, "linux", str(server_id), "run_command", "failed", str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/linux/servers/{server_id}/files/list")
async def linux_server_file_list(server_id: int, body: LinuxPathBody, user: dict = Depends(require_user)) -> dict[str, Any]:
    server = get_linux_server(server_id)
    try:
        result = await asyncio.to_thread(list_remote_directory, linux_server_config(server), body.path or ".")
        ensure_linux_fingerprint(server, result.get("fingerprint", ""))
        record_operation(None, "linux", str(server_id), "list_files", "success", result.get("path", ""))
        return result
    except SSHManagerError as exc:
        record_operation(None, "linux", str(server_id), "list_files", "failed", str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/linux/servers/{server_id}/files/read")
async def linux_server_file_read(server_id: int, body: LinuxFileBody, user: dict = Depends(require_user)) -> dict[str, Any]:
    server = get_linux_server(server_id)
    try:
        result = await asyncio.to_thread(read_remote_file, linux_server_config(server), body.path)
        ensure_linux_fingerprint(server, result.get("fingerprint", ""))
        record_operation(None, "linux", str(server_id), "read_file", "success", result.get("path", ""))
        return result
    except SSHManagerError as exc:
        record_operation(None, "linux", str(server_id), "read_file", "failed", str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/linux/servers/{server_id}/files/write")
async def linux_server_file_write(server_id: int, body: LinuxFileWriteBody, user: dict = Depends(require_user)) -> dict[str, Any]:
    server = get_linux_server(server_id)
    try:
        result = await asyncio.to_thread(write_remote_file, linux_server_config(server), body.path, body.content)
        ensure_linux_fingerprint(server, result.get("fingerprint", ""))
        record_operation(None, "linux", str(server_id), "write_file", "success", result.get("path", ""))
        return {"ok": True, **result}
    except SSHManagerError as exc:
        record_operation(None, "linux", str(server_id), "write_file", "failed", str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/linux/servers/{server_id}/l2tp/config")
async def linux_l2tp_config(server_id: int, user: dict = Depends(require_user)) -> dict[str, Any]:
    server = get_linux_server(server_id)
    try:
        result = await asyncio.to_thread(read_remote_file, linux_server_config(server), L2TP_USERS_PATH)
        ensure_linux_fingerprint(server, result.get("fingerprint", ""))
        return {"exists": True, **result}
    except SSHManagerError as exc:
        message = str(exc)
        if "No such file" in message or "not found" in message.lower() or "No such file" in message:
            return {
                "exists": False,
                "path": L2TP_USERS_PATH,
                "parent": "/etc/l2tp-vpn",
                "content": l2tp_default_users_config(),
                "size": 0,
            }
        record_operation(None, "linux", str(server_id), "read_l2tp_config", "failed", message)
        raise HTTPException(status_code=502, detail=message) from exc


@app.post("/api/linux/servers/{server_id}/l2tp/config")
async def linux_l2tp_save_config(server_id: int, body: LinuxL2tpConfigBody, user: dict = Depends(require_user)) -> dict[str, Any]:
    server = get_linux_server(server_id)
    config = linux_server_config(server)
    tmp_path = f"/tmp/ctyun-l2tp-users-{uuid.uuid4().hex}.conf"
    try:
        upload_result = await asyncio.to_thread(write_remote_file, config, tmp_path, body.content.replace("\r\n", "\n"), 2_000_000, 0o600)
        ensure_linux_fingerprint(server, upload_result.get("fingerprint", ""))
        target = shlex.quote(L2TP_USERS_PATH)
        tmp = shlex.quote(tmp_path)
        apply_command = ""
        if body.apply:
            apply_command = (
                "if [ -x /usr/local/sbin/l2tp-vpn-apply-config.sh ]; then\n"
                "  $SUDO env VPN_INTERACTIVE=0 VPN_APPLY_ONLY=1 /usr/local/sbin/l2tp-vpn-apply-config.sh\n"
                "  exit $?\n"
                "fi\n"
                "if [ ! -x /usr/local/sbin/l2tp-vpn-apply-users.sh ]; then\n"
                "  echo 'l2tp-vpn apply helper not found; run installer first' >&2\n"
                "  exit 3\n"
                "fi\n"
                "$SUDO /usr/local/sbin/l2tp-vpn-apply-users.sh\n"
            )
        command = _sudo_script(
            f"$SUDO mkdir -p /etc/l2tp-vpn\n"
            f"$SUDO install -m 600 {tmp} {target}\n"
            f"rm -f {tmp}\n"
            f"{apply_command}"
        )
        result = await asyncio.to_thread(run_ssh_command, config, command, 300)
        ensure_linux_fingerprint(server, result.get("fingerprint", ""))
        status_value = "success" if result.get("exit_status") == 0 else "failed"
        record_operation(None, "linux", str(server_id), "save_l2tp_config", status_value, result.get("stderr") or result.get("stdout") or "")
        return {"ok": result.get("exit_status") == 0, **result}
    except SSHManagerError as exc:
        record_operation(None, "linux", str(server_id), "save_l2tp_config", "failed", str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/linux/servers/{server_id}/l2tp/apply")
async def linux_l2tp_apply(server_id: int, user: dict = Depends(require_user)) -> dict[str, Any]:
    server = get_linux_server(server_id)
    command = _sudo_script(
        "if [ -x /usr/local/sbin/l2tp-vpn-apply-config.sh ]; then\n"
        "  $SUDO env VPN_INTERACTIVE=0 VPN_APPLY_ONLY=1 /usr/local/sbin/l2tp-vpn-apply-config.sh\n"
        "  exit $?\n"
        "fi\n"
        "if [ ! -x /usr/local/sbin/l2tp-vpn-apply-users.sh ]; then\n"
        "  echo 'l2tp-vpn apply helper not found; run installer first' >&2\n"
        "  exit 3\n"
        "fi\n"
        "$SUDO /usr/local/sbin/l2tp-vpn-apply-users.sh\n"
    )
    try:
        result = await asyncio.to_thread(run_ssh_command, linux_server_config(server), command, 300)
        ensure_linux_fingerprint(server, result.get("fingerprint", ""))
        status_value = "success" if result.get("exit_status") == 0 else "failed"
        record_operation(None, "linux", str(server_id), "apply_l2tp_config", status_value, result.get("stderr") or result.get("stdout") or "")
        return {"ok": result.get("exit_status") == 0, **result}
    except SSHManagerError as exc:
        record_operation(None, "linux", str(server_id), "apply_l2tp_config", "failed", str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/linux/servers/{server_id}/l2tp/install")
async def linux_l2tp_install(server_id: int, body: LinuxL2tpInstallBody, user: dict = Depends(require_user)) -> dict[str, Any]:
    if not L2TP_INSTALL_SCRIPT.exists():
        raise HTTPException(status_code=500, detail="install-l2tp-server.sh not found")
    server = get_linux_server(server_id)
    config = linux_server_config(server)
    tmp_path = f"/tmp/ctyun-install-l2tp-{uuid.uuid4().hex}.sh"
    script_text = L2TP_INSTALL_SCRIPT.read_text(encoding="utf-8")
    env_text = _l2tp_install_env(body)
    tmp = shlex.quote(tmp_path)
    command = (
        f"set -e\nchmod 700 {tmp}\n"
        "if [ \"$(id -u)\" -eq 0 ]; then\n"
        f"  env {env_text} bash {tmp}\n"
        "else\n"
        f"  sudo env {env_text} bash {tmp}\n"
        "fi\n"
        f"rm -f {tmp}\n"
    )
    try:
        upload_result = await asyncio.to_thread(write_remote_file, config, tmp_path, script_text, 600_000, 0o700)
        ensure_linux_fingerprint(server, upload_result.get("fingerprint", ""))
        result = await asyncio.to_thread(run_ssh_command, config, command, 900)
        ensure_linux_fingerprint(server, result.get("fingerprint", ""))
        status_value = "success" if result.get("exit_status") == 0 else "failed"
        record_operation(None, "linux", str(server_id), "install_l2tp", status_value, result.get("stderr") or result.get("stdout") or "")
        return {"ok": result.get("exit_status") == 0, **result}
    except SSHManagerError as exc:
        record_operation(None, "linux", str(server_id), "install_l2tp", "failed", str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc


async def _prepare_l2tp_scan_script(server_id: int, config: dict[str, Any], script_path: str, script_text: str) -> None:
    try:
        await asyncio.to_thread(write_remote_file, config, script_path, script_text, 80_000, 0o700)
        record_operation(None, "linux", str(server_id), "prepare_l2tp_scan", "success", script_path)
    except SSHManagerError as exc:
        record_operation(None, "linux", str(server_id), "prepare_l2tp_scan", "failed", str(exc))


@app.get("/api/linux/servers/{server_id}/l2tp/vips")
async def linux_l2tp_vips(
    server_id: int,
    prepare_scan: bool = True,
    user: dict = Depends(require_user),
) -> dict[str, Any]:
    server = get_linux_server(server_id)
    candidates = _l2tp_vip_candidates(server)
    if not prepare_scan:
        return {"server_id": server_id, **candidates}

    script_path = f"/tmp/ctyun-l2tp-scan-{uuid.uuid4().hex[:8]}.sh"
    quoted_path = shlex.quote(script_path)
    command = (
        f"for i in $(seq 1 30); do [ -s {quoted_path} ] && break; sleep 1; done; "
        f"[ -s {quoted_path} ] || {{ echo '扫描脚本还没准备好，请稍后重试。'; exit 1; }}; "
        f"/bin/sh {quoted_path}; rm -f {quoted_path}"
    )
    asyncio.create_task(_prepare_l2tp_scan_script(server_id, linux_server_config(server), script_path, _linux_l2tp_scan_script(candidates)))
    return {"server_id": server_id, "scan_command": command, "scan_script_path": script_path, "scan_prepare": "background", **candidates}


@app.websocket("/api/linux/servers/{server_id}/ssh")
async def linux_server_ssh_websocket(websocket: WebSocket, server_id: int) -> None:
    if not verify_session(websocket.cookies.get("ctyun_manager_session")):
        await websocket.close(code=4401)
        return
    await websocket.accept()
    client = None
    channel = None
    try:
        server = get_linux_server(server_id)
        await websocket.send_text(f"正在连接 {server['name']} ({server['host']}:{server['port']})...\r\n")
        client = await asyncio.to_thread(connect_ssh, linux_server_config(server), 15)
        ensure_linux_fingerprint(server, ssh_fingerprint(client))
        channel = await asyncio.to_thread(client.invoke_shell, term="xterm", width=120, height=36)
        update_linux_server_status(server_id, "ready", "SSH 会话已建立")
        record_operation(None, "linux", str(server_id), "open_ssh", "success", server["name"])
        await websocket.send_text("SSH 会话已连接。\r\n")

        async def ssh_to_browser() -> None:
            while channel and not channel.closed:
                if channel.recv_ready():
                    data = await asyncio.to_thread(channel.recv, 4096)
                    if not data:
                        break
                    await websocket.send_text(data.decode("utf-8", errors="replace"))
                    continue
                await asyncio.sleep(0.04)

        async def browser_to_ssh() -> None:
            while channel and not channel.closed:
                message = await websocket.receive_text()
                if message.startswith("__resize__:"):
                    parts = message.split(":")
                    if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
                        cols = max(60, min(int(parts[1]), 240))
                        rows = max(16, min(int(parts[2]), 80))
                        await asyncio.to_thread(channel.resize_pty, width=cols, height=rows)
                    continue
                await asyncio.to_thread(channel.send, message)

        tasks = [asyncio.create_task(ssh_to_browser()), asyncio.create_task(browser_to_ssh())]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in tasks:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
    except WebSocketDisconnect:
        pass
    except (HTTPException, SSHManagerError) as exc:
        message = exc.detail if isinstance(exc, HTTPException) else str(exc)
        update_linux_server_status(server_id, "failed", str(message))
        record_operation(None, "linux", str(server_id), "open_ssh", "failed", str(message))
        with suppress(Exception):
            await websocket.send_text(f"\r\nSSH 连接失败：{message}\r\n")
            await websocket.close(code=1011)
    finally:
        if channel:
            channel.close()
        if client:
            client.close()


@app.post("/api/accounts")
async def create_account(body: AccountBody, user: dict = Depends(require_user)) -> dict[str, Any]:
    required = {
        "官网登录账号": body.username,
        "官网登录密码": body.password,
        "Google 2FA": body.totp_secret,
        "AccessKey": body.ak,
        "SecretKey": body.sk,
    }
    missing = [label for label, value in required.items() if not value or not value.strip()]
    if missing:
        raise HTTPException(status_code=422, detail=f"请一次性填写：{'、'.join(missing)}")
    try:
        normalized_totp = normalize_totp_secret(body.totp_secret)
        current_totp(normalized_totp)
    except Exception:
        raise HTTPException(status_code=422, detail="Google 2FA 密钥或 otpauth URI 格式无效")
    with connect() as conn:
        cur = conn.execute(
            """
            insert into ctyun_accounts(name, provider_account_id, region, username_enc, password_enc, totp_secret_enc, ak_enc, sk_enc, notes)
            values(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                body.name,
                "",
                body.region,
                encrypt_text(body.username),
                encrypt_text(body.password),
                encrypt_text(normalized_totp),
                encrypt_text(body.ak),
                encrypt_text(body.sk),
                body.notes,
            ),
        )
    account_id = int(cur.lastrowid)
    record_operation(account_id, "account", str(account_id), "create_account", "success")
    asyncio.create_task(refresh_account_finance_once(account_id))
    return public_account(get_account(account_id))


@app.put("/api/accounts/{account_id}")
async def update_account(account_id: int, body: AccountBody, user: dict = Depends(require_user)) -> dict[str, Any]:
    current = get_account(account_id)
    username = encrypt_text(body.username) if body.username else current.get("username_enc")
    password = encrypt_text(body.password) if body.password else current.get("password_enc")
    if body.totp_secret:
        try:
            normalized_totp = normalize_totp_secret(body.totp_secret)
            current_totp(normalized_totp)
        except Exception:
            raise HTTPException(status_code=422, detail="Google 2FA 密钥或 otpauth URI 格式无效")
        totp_secret = encrypt_text(normalized_totp)
    else:
        totp_secret = current.get("totp_secret_enc")
    ak = encrypt_text(body.ak) if body.ak else current.get("ak_enc")
    sk = encrypt_text(body.sk) if body.sk else current.get("sk_enc")
    login_changed = bool(body.username or body.password or body.totp_secret)
    cookie_state = None if login_changed else current.get("cookie_state_enc")
    with connect() as conn:
        conn.execute(
            """
            update ctyun_accounts
            set name=?, region=?, username_enc=?, password_enc=?, totp_secret_enc=?,
                ak_enc=?, sk_enc=?, cookie_state_enc=?, notes=?, updated_at=current_timestamp
            where id=?
            """,
            (body.name, body.region, username, password, totp_secret, ak, sk, cookie_state, body.notes, account_id),
        )
    if login_changed:
        await reset_browser_session(account_id)
    if body.ak or body.sk:
        account_id_verified.discard(account_id)
    option_cache_clear(account_id)
    record_operation(account_id, "account", str(account_id), "update_account", "success")
    asyncio.create_task(refresh_account_finance_once(account_id))
    return public_account(get_account(account_id))


@app.delete("/api/accounts/{account_id}")
async def delete_account(account_id: int, user: dict = Depends(require_user)) -> dict[str, Any]:
    get_account(account_id)
    await reset_browser_session(account_id)
    with connect() as conn:
        conn.execute("delete from ctyun_accounts where id=?", (account_id,))
        conn.execute("delete from resources where account_id=?", (account_id,))
    record_operation(account_id, "account", str(account_id), "delete_account", "success")
    account_id_verified.discard(account_id)
    option_cache_clear(account_id)
    return {"ok": True}


def account_for_image_sync(account: dict[str, Any]) -> dict[str, Any]:
    if account.get("region"):
        return account
    with connect() as conn:
        rows = conn.execute(
            """
            select distinct region from resources
            where account_id=? and resource_type in ('ecs', 'eip', 'vpc', 'subnet', 'vip')
              and region != ''
            order by region
            """,
            (account["id"],),
        ).fetchall()
    regions = [row["region"] for row in rows if row["region"]]
    if not regions:
        return account
    narrowed = dict(account)
    narrowed["region"] = ",".join(regions)
    return narrowed


def protect_recent_confirmed_ecs_private_ip(item: dict[str, Any], existing_payload: dict[str, Any]) -> dict[str, Any]:
    confirmed_ip = str(existing_payload.get("_confirmed_private_ip") or "").strip()
    if not confirmed_ip:
        return item
    try:
        protected_until = float(existing_payload.get("_confirmed_private_ip_until") or 0)
    except (TypeError, ValueError):
        protected_until = 0
    if protected_until <= time.time():
        return item
    if confirmed_ip in _payload_private_ips(item):
        item["_confirmed_private_ip"] = confirmed_ip
        item["_confirmed_private_ip_until"] = protected_until
        return item

    protected = copy.deepcopy(item)
    stale_ips = _payload_private_ips(item)
    if stale_ips:
        protected["_openapi_private_ip_before_confirmed"] = stale_ips[0]
    protected["_confirmed_private_ip"] = confirmed_ip
    protected["_confirmed_private_ip_until"] = protected_until
    protected["private_ip"] = confirmed_ip
    protected["privateIP"] = confirmed_ip
    protected["fixedIPList"] = [confirmed_ip]
    if "fixedIpList" in protected:
        protected["fixedIpList"] = [confirmed_ip]
    cards = protected.get("networkCardList") if isinstance(protected.get("networkCardList"), list) else []
    for index, card in enumerate(cards):
        if not isinstance(card, dict):
            continue
        card = {**card, "IPv4Address": confirmed_ip, "privateIP": confirmed_ip}
        cards[index] = card
        break
    if cards:
        protected["networkCardList"] = cards
    return protected


def sync_resource_type(account: dict[str, Any], resource_type: str) -> list[dict[str, Any]]:
    lock_key = (int(account["id"]), resource_type)
    with resource_sync_locks_guard:
        resource_lock = resource_sync_locks.setdefault(lock_key, threading.Lock())
    if not resource_lock.acquire(blocking=False):
        raise CtyunClientSkipped("该资源类型正在同步，本次使用已有缓存。")
    try:
        if resource_type == "image":
            account = account_for_image_sync(account)
        client = build_client(account, settings.ctyun_mode)
        loaders = {
            "ecs": client.list_ecs,
            "eip": client.list_eips,
            "vpc": client.list_vpcs,
            "subnet": client.list_subnets,
            "vip": client.list_vips,
            "image": client.list_images,
            "security_group": client.list_security_groups,
            "route_table": client.list_route_tables,
            "acl": client.list_acls,
        }
        items = loaders[resource_type]()
        target_regions = split_region_ids(account.get("region"))
        with resource_db_write_lock:
            with connect() as conn:
                existing_payloads: dict[str, dict[str, Any]] = {}
                if resource_type == "ecs":
                    existing_rows = conn.execute(
                        "select provider_id, payload_json from resources where account_id=? and resource_type='ecs'",
                        (account["id"],),
                    ).fetchall()
                    for row in existing_rows:
                        with suppress(Exception):
                            existing_payloads[str(row["provider_id"])] = json.loads(row["payload_json"] or "{}")
                if target_regions:
                    placeholders = ",".join("?" for _ in target_regions)
                    conn.execute(
                        f"delete from resources where account_id=? and resource_type=? and region in ({placeholders})",
                        [account["id"], resource_type, *target_regions],
                    )
                else:
                    conn.execute("delete from resources where account_id=? and resource_type=?", (account["id"], resource_type))
                for item in items:
                    if not item.get("id"):
                        continue
                    if resource_type == "ecs":
                        item = protect_recent_confirmed_ecs_private_ip(item, existing_payloads.get(str(item["id"]), {}))
                    conn.execute(
                        """
                        insert into resources(account_id, resource_type, provider_id, name, region, status, billing_mode, payload_json, synced_at)
                        values(?, ?, ?, ?, ?, ?, ?, ?, current_timestamp)
                        on conflict(account_id, resource_type, provider_id) do update set
                          name=excluded.name, region=excluded.region, status=excluded.status,
                          billing_mode=excluded.billing_mode, payload_json=excluded.payload_json,
                          synced_at=current_timestamp
                        """,
                        (
                            account["id"],
                            resource_type,
                            str(item["id"]),
                            item.get("name", item["id"]),
                            item.get("region", account.get("region", "")),
                            item.get("status", ""),
                            item.get("billing_mode", ""),
                            json.dumps(item, ensure_ascii=False),
                        ),
                    )
        return items
    finally:
        resource_lock.release()


def sync_account_resources(account: dict[str, Any], kinds: list[str] | tuple[str, ...]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    errors: dict[str, str] = {}
    skipped: dict[str, str] = {}
    valid_kinds = []
    for kind in dict.fromkeys(kinds):
        if kind not in SYNC_TYPES:
            errors[kind] = "不支持的同步类型"
            continue
        valid_kinds.append(kind)
    if not valid_kinds:
        return {"ok": not errors, "counts": counts, "errors": errors, "skipped": skipped}
    workers = min(settings.openapi_workers, len(valid_kinds))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ctyun-sync") as executor:
        futures = {
            executor.submit(sync_resource_type, account, kind): kind
            for kind in valid_kinds
        }
        for future in as_completed(futures):
            kind = futures[future]
            try:
                counts[kind] = len(future.result())
            except CtyunClientSkipped as exc:
                counts[kind] = 0
                skipped[kind] = str(exc)
            except CtyunClientError as exc:
                counts[kind] = 0
                errors[kind] = str(exc)
            except Exception as exc:
                counts[kind] = 0
                errors[kind] = str(exc)
    return {"ok": not errors, "counts": counts, "errors": errors, "skipped": skipped}


def account_for_fast_sync(account: dict[str, Any], kinds: list[str], region_ids: list[str] | None = None) -> dict[str, Any]:
    explicit_regions = split_region_ids(",".join(region_ids or []))
    if explicit_regions:
        narrowed = dict(account)
        narrowed["region"] = ",".join(explicit_regions)
        return narrowed
    if not kinds or account.get("region"):
        return account
    if kinds == ["image"]:
        return account_for_image_sync(account)
    placeholders = ",".join("?" for _ in kinds)
    with connect() as conn:
        rows = conn.execute(
            f"""
            select distinct region from resources
            where account_id=? and resource_type in ({placeholders}) and region != ''
            """,
            [account["id"], *kinds],
        ).fetchall()
    regions = [row["region"] for row in rows if row["region"]]
    if not regions:
        return account
    narrowed = dict(account)
    narrowed["region"] = ",".join(regions)
    return narrowed


def background_sync_loop() -> None:
    time.sleep(20)
    while True:
        if background_sync_lock.acquire(blocking=False):
            try:
                with connect() as conn:
                    accounts = [dict(row) for row in conn.execute("select * from ctyun_accounts where status='enabled' order by id").fetchall()]
                for account in accounts:
                    account_lock = account_sync_lock(int(account["id"]))
                    if not account_lock.acquire(blocking=False):
                        record_operation(account["id"], "account", str(account["id"]), "background_sync", "skipped", "该账号已有同步任务正在运行。")
                        continue
                    try:
                        result = sync_account_resources(account, SYNC_TYPES)
                        record_operation(
                            account["id"],
                            "account",
                            str(account["id"]),
                            "background_sync",
                            "success" if not result["errors"] else "partial",
                            json.dumps(result, ensure_ascii=False),
                        )
                        option_cache_clear(account["id"])
                        time.sleep(2)
                    finally:
                        account_lock.release()
            except Exception as exc:
                record_operation(None, "account", None, "background_sync", "failed", str(exc))
            finally:
                background_sync_lock.release()
        time.sleep(settings.background_sync_seconds)


@app.post("/api/accounts/{account_id}/sync")
def sync_account(account_id: int, user: dict = Depends(require_user)) -> dict[str, Any]:
    account = get_account(account_id)
    account_lock = account_sync_lock(account_id)
    if not account_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="该账号已有同步任务正在运行，请稍后再试。")
    try:
        result = sync_account_resources(account, SYNC_TYPES)
        status_value = "success" if not result["errors"] else "partial"
        record_operation(account_id, "account", str(account_id), "sync", status_value, json.dumps(result, ensure_ascii=False))
        option_cache_clear(account_id)
        if not any(result["counts"].values()) and result["errors"]:
            raise HTTPException(status_code=501, detail="同步完成但未发现资源；接口错误：" + "；".join(f"{k}: {v}" for k, v in result["errors"].items()))
        return result
    finally:
        account_lock.release()


@app.post("/api/accounts/{account_id}/sync-types")
def sync_account_types(account_id: int, body: SyncBody, user: dict = Depends(require_user)) -> dict[str, Any]:
    account = get_account(account_id)
    kinds = list(dict.fromkeys(body.types))
    result = sync_account_resources(account_for_fast_sync(account, kinds, body.region_ids), kinds)
    status_value = "success" if not result["errors"] else "partial"
    record_operation(account_id, "account", str(account_id), "auto_sync", status_value, json.dumps(result, ensure_ascii=False))
    option_cache_clear(account_id, option_kinds_for_resource_types(kinds))
    return result


@app.get("/api/finance")
def finance_cache(user: dict = Depends(require_user)) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            select a.id as account_id, f.available, f.owe, f.status, f.message, f.updated_at
            from ctyun_accounts a
            left join account_finance f on f.account_id=a.id
            order by a.id desc
            """
        ).fetchall()
    return rows_to_dicts(rows)


@app.get("/api/dashboard/summary")
def dashboard_summary(user: dict = Depends(require_user)) -> dict[str, Any]:
    with connect() as conn:
        account_count = int(conn.execute("select count(*) as c from ctyun_accounts").fetchone()["c"])
        resource_rows = conn.execute(
            "select resource_type, count(*) as c from resources group by resource_type"
        ).fetchall()
        finance_rows = conn.execute(
            """
            select a.id as account_id, f.available, f.owe, f.status, f.message, f.updated_at
            from ctyun_accounts a
            left join account_finance f on f.account_id=a.id
            order by a.id desc
            """
        ).fetchall()
    return {
        "account_count": account_count,
        "resource_counts": {row["resource_type"]: int(row["c"]) for row in resource_rows},
        "finance": rows_to_dicts(finance_rows),
    }


@app.get("/api/resources/{resource_type}")
def list_resources(resource_type: str, account_id: int | None = None, user: dict = Depends(require_user)) -> list[dict[str, Any]]:
    params: list[Any] = [resource_type]
    where = "where resource_type = ?"
    if account_id:
        where += " and account_id = ?"
        params.append(account_id)
    with connect() as conn:
        rows = conn.execute(f"select * from resources {where} order by synced_at desc", params).fetchall()
    result = []
    for row in rows:
        data = dict(row)
        data["payload"] = json.loads(data.pop("payload_json") or "{}")
        result.append(data)
    return result


@app.get("/api/accounts/{account_id}/balance")
async def balance(account_id: int, user: dict = Depends(require_user)) -> dict[str, Any]:
    account = get_account(account_id)
    started = time.monotonic()
    result = await get_finance(account, force_api=True)
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    if result.get("status") != "interactive":
        persist_finance(account_id, result)
    result.pop("cookie_state_enc", None)
    return result


@app.get("/api/accounts/{account_id}/regions")
def regions(account_id: int, user: dict = Depends(require_user)) -> list[dict[str, Any]]:
    account = get_account(account_id)
    try:
        result = build_region_client(account, settings.ctyun_mode).list_regions()
        record_operation(account_id, "account", str(account_id), "list_regions", "success", f"{len(result)} regions")
        return result
    except CtyunClientError as exc:
        record_operation(account_id, "account", str(account_id), "list_regions", "failed", str(exc))
        raise HTTPException(status_code=501, detail=str(exc))


def resource_option_label(resource_type: str, row: dict[str, Any], payload: dict[str, Any]) -> str:
    name = row.get("name") or payload.get("name") or "未命名"
    if resource_type == "vpc":
        return f"{name} · {payload.get('cidr') or payload.get('CIDR') or '无CIDR'}"
    if resource_type == "subnet":
        return f"{name} · {payload.get('cidr') or payload.get('CIDR') or '无CIDR'}"
    if resource_type == "image":
        os_value = payload.get("os")
        if isinstance(os_value, dict):
            os_value = os_value.get("nameZh") or os_value.get("nameEn") or os_value.get("osType") or os_value.get("osName")
        os_name = os_value or payload.get("osDistro") or payload.get("osVersion") or payload.get("osType") or "未知系统"
        return f"{name} · {os_name}"
    if resource_type == "eip":
        return f"{payload.get('ip') or payload.get('eipAddress') or name} · {name}"
    if resource_type == "ecs":
        return f"{name} · {payload.get('private_ip') or payload.get('privateIP') or '无私网IP'}"
    if resource_type == "vip":
        return str(payload.get("ip") or payload.get("ipv4") or name)
    return str(name)


def image_visibility_value(payload: dict[str, Any]) -> str:
    return str(
        payload.get("visibility")
        or payload.get("imageVisibility")
        or payload.get("imageVisibilityCode")
        or payload.get("imageType")
        or payload.get("type")
        or ""
    ).strip().lower()


def image_type_text(payload: dict[str, Any]) -> str:
    return " ".join(
        str(value or "")
        for value in [
            payload.get("imageType"),
            payload.get("type"),
            payload.get("imageCategory"),
            payload.get("imageClass"),
            payload.get("imageSource"),
            payload.get("visibility"),
            payload.get("imageVisibility"),
            payload.get("imageVisibilityCode"),
        ]
    ).lower()


def image_is_shared(payload: dict[str, Any]) -> bool:
    visibility = image_visibility_value(payload)
    text = image_type_text(payload)
    return visibility in {"2", "shared"} or any(marker in text for marker in ["shared", "share", "共享"])


def image_is_private(payload: dict[str, Any]) -> bool:
    visibility = image_visibility_value(payload)
    text = image_type_text(payload)
    return visibility in {"0", "private"} or any(marker in text for marker in ["private", "personal", "私有"])


def image_is_public(payload: dict[str, Any]) -> bool:
    if image_is_private(payload) or image_is_shared(payload):
        return False
    visibility = image_visibility_value(payload)
    text = image_type_text(payload)
    return visibility in {"1", "public", "standard"} or any(marker in text for marker in ["public", "standard", "公共"])


def image_option_from_payload(payload: dict[str, Any], row: dict[str, Any] | None = None) -> dict[str, Any] | None:
    provider_id = (
        (row or {}).get("provider_id")
        or payload.get("id")
        or payload.get("imageID")
        or payload.get("imageUUID")
        or payload.get("imageUuid")
        or payload.get("image_uuid")
    )
    if not provider_id:
        return None
    name = (
        (row or {}).get("name")
        or payload.get("name")
        or payload.get("imageName")
        or payload.get("nameZh")
        or payload.get("nameEn")
        or provider_id
    )
    return {
        "value": str(provider_id),
        "label": resource_option_label("image", {"name": name}, payload),
        "meta": payload,
    }


def cached_public_image_options(account: dict[str, Any], region_id: str) -> list[dict[str, Any]]:
    cache_key = ("shared", "", settings.ctyun_mode, "images_public", region_id or "", "", "", False)
    cached = option_cache_get(cache_key)
    if cached is not None:
        return cached

    public_options: list[dict[str, Any]] = []
    with connect() as conn:
        rows = conn.execute(
            """
            select * from resources
            where resource_type='image' and region=?
            order by name
            """,
            (region_id,),
        ).fetchall()
    seen: set[str] = set()
    for source in rows:
        row = dict(source)
        payload = json.loads(row.get("payload_json") or "{}")
        if not image_is_public(payload):
            continue
        option = image_option_from_payload(payload, row)
        if not option or option["value"] in seen:
            continue
        seen.add(option["value"])
        public_options.append(option)
    if public_options:
        return option_cache_set("images_public", cache_key, public_options)

    narrowed = dict(account)
    narrowed["region"] = region_id
    try:
        items = build_client(narrowed, settings.ctyun_mode).list_images()
    except (CtyunClientError, CtyunClientSkipped):
        return option_cache_set("images_public", cache_key, public_options)
    for item in items:
        if not image_is_public(item):
            continue
        option = image_option_from_payload(item)
        if not option or option["value"] in seen:
            continue
        seen.add(option["value"])
        public_options.append(option)
    return option_cache_set("images_public", cache_key, public_options)


def account_image_options(account: dict[str, Any], region_id: str) -> list[dict[str, Any]]:
    account_id = int(account["id"])
    account_key = (account_id, account.get("updated_at") or "", settings.ctyun_mode, "images_account", region_id or "", "", "", False)
    cached = option_cache_get(account_key)
    if cached is not None:
        return cached_public_image_options(account, region_id) + cached

    account_options: list[dict[str, Any]] = []
    seen: set[str] = set()
    with connect() as conn:
        rows = conn.execute(
            """
            select * from resources
            where account_id=? and resource_type='image' and region=?
            order by name
            """,
            (account_id, region_id),
        ).fetchall()
    for source in rows:
        row = dict(source)
        payload = json.loads(row.get("payload_json") or "{}")
        if image_is_public(payload):
            continue
        option = image_option_from_payload(payload, row)
        if not option or option["value"] in seen:
            continue
        seen.add(option["value"])
        account_options.append(option)
    option_cache_set("images_account", account_key, account_options)
    return cached_public_image_options(account, region_id) + account_options


SOLD_OUT_TEXT_PATTERN = re.compile(
    r"售罄|已售完|售完|售空|无货|无库存|库存不足|暂无库存|资源不足|"
    r"不可售|不可购买|不可用|已下架|停售|停止售卖|暂不支持|"
    r"sold\s*out|soldout|out[\s_-]*of[\s_-]*stock|no[\s_-]*stock|unavailable|disabled",
    re.IGNORECASE,
)

SOLD_OUT_TRUE_KEYS = {
    "soldout", "issoldout", "soldoutflag", "sellout", "issellout", "selloutflag",
    "stockout", "isstockout", "nostock", "outofstock", "isoutofstock",
    "unavailable", "isunavailable", "disabled", "isdisabled",
}
AVAILABLE_FALSE_KEYS = {
    "available", "isavailable", "canorder", "canbuy", "cancreate", "canapply",
    "canpurchase", "canuse", "saleable", "sellable", "orderable", "support",
    "issupport", "supportorder", "supportcreate", "supportsale", "isonsale",
    "onsale", "insale", "saleenabled", "sellenabled", "enabled", "enable",
}
ZERO_STOCK_KEYS = {
    "remain", "remaincount", "remainnum", "stock", "stockcount", "stocknum",
    "inventory", "inventorycount", "inventorynum", "availablecount", "availablenum",
    "availablestock", "left", "leftcount", "surplus", "surpluscount",
}
STATUS_TEXT_KEYS = {
    "status", "statusname", "displaystatus", "sellstatus", "salestatus",
    "sellstate", "salestate", "stockstatus", "inventorystatus", "availablestatus",
    "productstatus", "flavorstatus", "specstatus",
}
UNAVAILABLE_STATUS_VALUES = {
    "soldout", "soldoutstatus", "outofstock", "nostock", "unavailable", "disabled",
    "offline", "stopped", "stop", "stopsale", "offsale", "notsale", "unsaleable",
}

BOOT_DISK_TYPE_LABELS = {
    "SSD": "超高 IO 云硬盘",
    "SSD-genric": "通用型 SSD",
    "SAS": "高 IO 云硬盘",
    "SATA": "普通 IO 云硬盘",
    "FAST-SSD": "极速型 SSD",
}
EIP_OPTION_CATALOGS: dict[str, list[dict[str, Any]]] = {
    "eip_lines": [
        {"value": "163", "label": "中国电信单线", "aliases": ["163", "chinatelecom", "chinatelecom-isp", "中国电信", "本地网络"], "default": True},
        {"value": "bgp", "label": "BGP 多线", "aliases": ["bgp", "bgp-3-isp", "bgp多线", "bgp 多线"], "default": True},
        {"value": "chinamobile", "label": "中国移动单线", "aliases": ["chinamobile", "chinamobile-isp", "中国移动"], "default": True},
        {"value": "chinaunicom", "label": "中国联通单线", "aliases": ["chinaunicom", "chinaunicom-isp", "中国联通"], "default": True},
        {"value": "CN2", "label": "精品网络", "aliases": ["cn2", "精品网络", "精品线路", "pro-crossline-isp", "cn2-1107-isp", "cn2-1124-isp"], "default": False},
        {"value": "dedicated_net", "label": "专属地址池", "aliases": ["dedicated_net", "专属地址池"], "default": False},
    ],
    "eip_cycle_types": [
        {"value": "on_demand", "label": "按需", "aliases": ["on_demand", "按需", "按量", "按需计费"], "default": True},
        {"value": "month", "label": "包月", "aliases": ["month", "monthly", "包月", "月"], "default": True},
        {"value": "year", "label": "包年", "aliases": ["year", "yearly", "包年", "年"], "default": True},
    ],
    "eip_demand_billing_types": [
        {"value": "bandwidth", "label": "按带宽", "aliases": ["bandwidth", "bandwidthpeak", "按带宽", "带宽"], "default": True},
        {"value": "upflowc", "label": "按流量", "aliases": ["upflowc", "traffic", "flow", "按流量", "流量"], "default": True},
    ],
}


def eip_dynamic_options(kind: str, config: dict[str, Any]) -> list[dict[str, Any]]:
    catalog = EIP_OPTION_CATALOGS.get(kind, [])
    status = str(config.get("status") or "")
    message = str(config.get("message") or "")
    source = str(config.get("source") or "")
    text = " ".join(_walk_values(config)).lower()
    matched: list[dict[str, Any]] = []
    if status == "ready" and text:
        for item in catalog:
            aliases = [str(value).lower() for value in item.get("aliases", [])]
            if any(alias and alias in text for alias in aliases):
                matched.append(item)
    if not matched:
        matched = [item for item in catalog if item.get("default", True)]
    return [
        {
            "value": str(item["value"]),
            "label": str(item["label"]),
            "meta": {
                "officialStatus": status,
                "officialMessage": message,
                "officialSource": source,
                "officialMatched": item in matched and status == "ready",
            },
        }
        for item in matched
    ]


def _compact_stock_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def official_stock_sold_out(item: dict[str, Any] | None) -> bool:
    if not item:
        return False
    remaining = str(item.get("remainingStatus") or item.get("remainStatus") or "").strip().upper()
    if remaining in {"Y", "YES", "TRUE", "1"}:
        return True
    if remaining in {"N", "NO", "FALSE", "0"}:
        return False
    for key in ("isSoldOut", "soldOut", "soldout", "sellOut", "is_sell_out", "isStockOut"):
        if _bool_true(item.get(key)):
            return True
    for key in (
        "available", "isAvailable", "canOrder", "canBuy", "canCreate", "canPurchase",
        "saleable", "sellable", "orderable", "hasStock", "hasRemain", "stockAvailable",
    ):
        if key in item and _bool_false(item.get(key)):
            return True
        if key in item and _bool_true(item.get(key)):
            return False
    text = " ".join(_walk_values(item))
    return bool(SOLD_OUT_TEXT_PATTERN.search(text))


def official_stock_name(item: dict[str, Any]) -> str:
    return str(
        item.get("name")
        or item.get("spec_name")
        or item.get("specName")
        or item.get("spec")
        or item.get("flavorName")
        or item.get("flavor_name")
        or item.get("resourceType")
        or item.get("resource_type")
        or ""
    ).strip()


def official_flavor_stock_map(stock: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in stock.get("flavors") or []:
        if not isinstance(item, dict):
            continue
        name = official_stock_name(item)
        if name:
            existing = result.get(name)
            if not existing or (official_stock_sold_out(existing) and not official_stock_sold_out(item)):
                result[name] = item
    return result


def official_spec_name(item: dict[str, Any]) -> str:
    return str(
        item.get("spec_name")
        or item.get("specName")
        or item.get("name")
        or item.get("flavorName")
        or item.get("flavor_name")
        or ""
    ).strip()


def official_spec_id(item: dict[str, Any]) -> str:
    return str(
        item.get("flavor_id")
        or item.get("flavorID")
        or item.get("id")
        or item.get("flavorId")
        or ""
    ).strip()


def official_spec_cpu(item: dict[str, Any]) -> Any:
    return item.get("vcpu") or item.get("vcpus") or item.get("cpuNum") or item.get("cpu") or ""


def official_spec_memory(item: dict[str, Any]) -> Any:
    value = item.get("ram") or item.get("memSize") or item.get("memory") or item.get("mem") or ""
    try:
        number = float(value)
        if number >= 1024 and number % 1024 == 0:
            return int(number / 1024)
        return int(number) if number.is_integer() else number
    except Exception:
        return value


def official_spec_arch(item: dict[str, Any]) -> str:
    return str(item.get("cpuinfo") or item.get("cpuArch") or item.get("architecture") or "x86").strip()


def storage_stock_for_disk_type(stock: dict[str, Any], disk_type: str) -> dict[str, Any] | None:
    needle = _compact_stock_key(disk_type)
    best = None
    for item in stock.get("storage") or []:
        if not isinstance(item, dict):
            continue
        text = _compact_stock_key(" ".join(_walk_values(item)))
        resource_type = _compact_stock_key(item.get("resourceType") or item.get("resource_type"))
        if needle and (needle in text or needle in resource_type):
            return item
        if not best and ("sys" in resource_type or "ebs" in resource_type or "volume" in resource_type or "disk" in resource_type):
            best = item
    return best if len(stock.get("storage") or []) == 1 else None


def boot_disk_type_options(stock: dict[str, Any]) -> list[dict[str, Any]]:
    ready = stock.get("status") == "ready"
    result = []
    for value, label in BOOT_DISK_TYPE_LABELS.items():
        stock_item = storage_stock_for_disk_type(stock, value) if ready else None
        sold_out = official_stock_sold_out(stock_item)
        result.append({
            "value": value,
            "label": f"{label}（已售罄）" if sold_out else label,
            "disabled": sold_out,
            "meta": {
                "diskType": value,
                "label": label,
                "officialStock": stock_item or {},
                "officialStockStatus": stock.get("status", ""),
                "officialStockMessage": stock.get("message", ""),
                "soldOut": sold_out,
            },
        })
    return result
_flavor_sale_cache: dict[tuple[Any, ...], tuple[float, bool]] = {}
_flavor_sale_cache_lock = threading.Lock()
_flavor_sold_out_cache: dict[tuple[str, str], tuple[float, str]] = {}
_flavor_sold_out_cache_lock = threading.Lock()
_flavor_probe_image_cache: dict[tuple[int, str, str], tuple[float, str]] = {}
_flavor_probe_image_cache_lock = threading.Lock()


def _status_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key or "").lower())


def _bool_true(value: Any) -> bool:
    return value is True or str(value).strip().lower() in {"1", "true", "yes", "y", "on", "是", "售罄", "已售罄"}


def _bool_false(value: Any) -> bool:
    return value is False or str(value).strip().lower() in {"0", "false", "no", "n", "off", "否"}


def _walk_values(value: Any) -> list[str]:
    result: list[str] = []
    stack = [value]
    while stack and len(result) < 200:
        current = stack.pop()
        if isinstance(current, dict):
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
        elif current not in (None, ""):
            result.append(str(current))
    return result


def flavor_sold_out(payload: dict[str, Any]) -> bool:
    if SOLD_OUT_TEXT_PATTERN.search(" ".join(_walk_values(payload))):
        return True
    stack = [payload]
    while stack:
        current = stack.pop()
        if isinstance(current, list):
            stack.extend(item for item in current if isinstance(item, (dict, list)))
            continue
        if not isinstance(current, dict):
            continue
        for key, value in current.items():
            key_name = _status_key(key)
            if isinstance(value, (dict, list)):
                stack.append(value)
                continue
            if key_name in SOLD_OUT_TRUE_KEYS and _bool_true(value):
                return True
            if key_name in AVAILABLE_FALSE_KEYS and _bool_false(value):
                return True
            if key_name in ZERO_STOCK_KEYS:
                with suppress(TypeError, ValueError):
                    if float(value) == 0:
                        return True
            if key_name in STATUS_TEXT_KEYS:
                normalized = _status_key(value)
                if normalized in UNAVAILABLE_STATUS_VALUES:
                    return True
    return False


def ecs_flavor_sold_out_error(message: str) -> bool:
    text = str(message or "")
    lowered = text.lower()
    if ecs_flavor_stock_unknown_error(text):
        return False
    if "EbsSoldOut" in text or "disk sold out" in lowered or re.search(r"磁盘.*售罄|云硬盘.*售罄|系统盘.*售罄", text, re.I):
        return False
    return (
        "FlavorSoldOut" in text
        or "flavor sold out" in lowered
        or bool(re.search(r"该规格.*云主机.*已售罄|该规格.*已售罄|云主机规格.*售罄", text, re.I))
    )


def ecs_flavor_stock_unknown_error(message: str) -> bool:
    text = str(message or "")
    lowered = text.lower()
    unknown_markers = [
        "salecheck.unknownerror",
        "saleyacos.accessfailed",
        "saleformats.formaterror",
        "售罄信息检查失败",
        "查询售罄信息错误",
        "查询售罄信息格式错误",
    ]
    return any(marker in lowered or marker in text for marker in unknown_markers)


def ecs_order_forbidden_on_demand(message: str) -> bool:
    text = str(message or "")
    lowered = text.lower()
    return (
        "Unknown.OrderCheck.UserForbiddenOnDemand" in text
        or "user not allowed place ondemand order" in lowered
        or "用户详情信息不符预期" in text
    )


def remember_flavor_sold_out(payload: dict[str, Any], message: str = "", ttl: int = 600) -> None:
    region_id = str(payload.get("regionID") or payload.get("regionId") or payload.get("region") or "").strip()
    if not region_id:
        return
    identifiers = {
        str(payload.get("flavorID") or "").strip(),
        str(payload.get("flavorId") or "").strip(),
        str(payload.get("flavorName") or "").strip(),
        str(payload.get("specName") or "").strip(),
    }
    identifiers.discard("")
    if not identifiers:
        return
    expires_at = time.monotonic() + max(60, ttl)
    with _flavor_sold_out_cache_lock:
        for identifier in identifiers:
            _flavor_sold_out_cache[(region_id, identifier)] = (expires_at, message[:500])
    option_cache_clear(None, ("flavors",))


def remembered_flavor_sold_out(region_id: str, *identifiers: Any) -> str:
    now = time.monotonic()
    keys = [(str(region_id or "").strip(), str(identifier or "").strip()) for identifier in identifiers if str(identifier or "").strip()]
    if not keys:
        return ""
    with _flavor_sold_out_cache_lock:
        for key in list(_flavor_sold_out_cache.keys()):
            if _flavor_sold_out_cache[key][0] <= now:
                _flavor_sold_out_cache.pop(key, None)
        for key in keys:
            cached = _flavor_sold_out_cache.get(key)
            if cached and cached[0] > now:
                return cached[1] or "官方创建接口确认该规格暂不可购买。"
    return ""


def cached_flavor_sale_available(
    client: Any,
    account_id: int,
    item: dict[str, Any],
    *,
    region_id: str,
    az_name: str,
    image_id: str,
    boot_disk_type: str,
    boot_disk_size: int,
    on_demand: bool,
    cycle_type: str,
    cycle_count: int,
) -> bool:
    flavor_name = item.get("specName") or item.get("flavorName")
    flavor_id = item.get("flavorID")
    if not flavor_name or not image_id:
        return True
    key = (
        account_id,
        region_id,
        az_name or "",
        image_id,
        boot_disk_type or "SSD",
        boot_disk_size or 40,
        on_demand,
        cycle_type or "",
        cycle_count or 1,
        flavor_id or flavor_name,
    )
    now = time.monotonic()
    with _flavor_sale_cache_lock:
        cached = _flavor_sale_cache.get(key)
        if cached and cached[0] > now:
            return cached[1]
    available = True
    try:
        client.query_ecs_new_order_price({
            "regionID": region_id,
            "azName": az_name,
            "flavorName": flavor_name,
            "imageID": image_id,
            "bootDiskType": boot_disk_type or "SSD",
            "bootDiskSize": boot_disk_size or 40,
            "onDemand": on_demand,
            "cycleType": cycle_type or "MONTH",
            "cycleCount": cycle_count or 1,
        })
    except CtyunClientError as exc:
        message = str(exc)
        if ecs_flavor_sold_out_error(message):
            available = False
        elif ecs_flavor_stock_unknown_error(message):
            available = False
        elif on_demand and ecs_order_forbidden_on_demand(message):
            try:
                client.query_ecs_new_order_price({
                    "regionID": region_id,
                    "azName": az_name,
                    "flavorName": flavor_name,
                    "imageID": image_id,
                    "bootDiskType": boot_disk_type or "SSD",
                    "bootDiskSize": boot_disk_size or 40,
                    "onDemand": False,
                    "cycleType": "MONTH",
                    "cycleCount": 1,
                })
            except CtyunClientError as retry_exc:
                retry_message = str(retry_exc)
                if ecs_flavor_sold_out_error(retry_message) or ecs_flavor_stock_unknown_error(retry_message):
                    available = False
    with _flavor_sale_cache_lock:
        if len(_flavor_sale_cache) > 3000:
            _flavor_sale_cache.clear()
        _flavor_sale_cache[key] = (now + 300, available)
    return available


def image_probe_arch(payload: dict[str, Any]) -> str:
    text = " ".join(
        str(value or "")
        for value in [
            payload.get("architecture"),
            payload.get("cpuType"),
            payload.get("imageName"),
            payload.get("name"),
        ]
    ).lower()
    if any(marker in text for marker in ["aarch", "arm", "kunpeng", "feiteng", "鲲鹏", "飞腾"]):
        return "arm"
    return "x86"


def image_probe_score(payload: dict[str, Any], preferred_arch: str = "x86") -> int:
    visibility = str(
        payload.get("visibility")
        or payload.get("imageVisibility")
        or payload.get("imageVisibilityCode")
        or payload.get("imageType")
        or ""
    ).lower()
    source_text = " ".join(
        str(value or "")
        for value in [
            payload.get("imageName"),
            payload.get("name"),
            payload.get("imageCategory"),
            payload.get("imageClass"),
            payload.get("imageSource"),
            payload.get("imageType"),
            payload.get("osType"),
            payload.get("osDistro"),
        ]
    ).lower()
    if any(marker in source_text for marker in ["安全产品镜像", "应用镜像", "市场镜像", "market", "security product", "application image"]):
        return -1
    if visibility not in {"", "1", "public", "standard"}:
        return -1
    if str(payload.get("imageStatus") or payload.get("status") or "").lower() not in {"", "active"}:
        return -1
    score = 0
    if image_probe_arch(payload) == preferred_arch:
        score += 20
    if str(payload.get("osType") or "").lower() == "linux":
        score += 8
    if "ctyunos" in source_text or "centos" in source_text or "alma" in source_text:
        score += 4
    if payload.get("diskSize") in (None, "", 40, "40"):
        score += 2
    return score


def flavor_probe_image_id(account: dict[str, Any], account_id: int, region_id: str, preferred_arch: str = "x86") -> str:
    key = (account_id, region_id, preferred_arch)
    now = time.monotonic()
    with _flavor_probe_image_cache_lock:
        cached = _flavor_probe_image_cache.get(key)
        if cached and cached[0] > now:
            return cached[1]
    image_id = ""
    narrowed = dict(account)
    narrowed["region"] = region_id
    with suppress(Exception):
        images = build_client(narrowed, settings.ctyun_mode).list_images()
        ranked = sorted(
            (
                (image_probe_score(image, preferred_arch), image)
                for image in images
                if image.get("id") or image.get("imageID") or image.get("imageUUID")
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        selected = next((image for score, image in ranked if score >= 0), None)
        if selected:
            image_id = str(selected.get("id") or selected.get("imageID") or selected.get("imageUUID") or "")
    with _flavor_probe_image_cache_lock:
        if len(_flavor_probe_image_cache) > 1000:
            _flavor_probe_image_cache.clear()
        _flavor_probe_image_cache[key] = (now + 600, image_id)
    return image_id


@app.get("/api/accounts/{account_id}/options/{kind}")
async def account_options(
    account_id: int,
    kind: str,
    region_id: str = "",
    vpc_id: str = "",
    az_name: str = "",
    available_only: bool = False,
    image_id: str = "",
    boot_disk_type: str = "SSD",
    boot_disk_size: int = 40,
    on_demand: bool = True,
    cycle_type: str = "MONTH",
    cycle_count: int = 1,
    user: dict = Depends(require_user),
) -> list[dict[str, Any]]:
    account = get_account(account_id)
    cache_key = option_cache_key(
        account,
        kind,
        region_id=region_id,
        vpc_id=vpc_id,
        az_name=az_name,
        available_only=available_only,
    )
    if kind in OPTION_CACHE_TTLS and kind != "images":
        cached_options = option_cache_get(cache_key)
        if cached_options is not None:
            return cached_options

    def cache_options(value: list[dict[str, Any]], ttl_override: int | None = None) -> list[dict[str, Any]]:
        return option_cache_set(kind, cache_key, value, ttl_override=ttl_override)

    resource_types = {
        "vpcs": "vpc",
        "subnets": "subnet",
        "images": "image",
        "eips": "eip",
        "ecs": "ecs",
        "security_groups": "security_group",
        "vips": "vip",
        "route_tables": "route_table",
        "acls": "acl",
    }
    client = None
    try:
        client = build_region_client(account, settings.ctyun_mode)
        if kind == "regions":
            return cache_options([
                {
                    "value": str(item.get("regionID") or ""),
                    "label": str(item.get("regionName") or item.get("regionID") or ""),
                    "meta": item,
                }
                for item in client.list_regions()
                if item.get("regionID")
            ])
        if not region_id:
            raise HTTPException(status_code=422, detail="请先选择资源池。")
        if kind in EIP_OPTION_CATALOGS:
            config = await get_console_eip_create_options(account, region_id)
            options = eip_dynamic_options(kind, config)
            return cache_options(options) if config.get("status") == "ready" else options
        if kind == "images":
            return account_image_options(account, region_id)
        if kind == "zones":
            return cache_options([
                {
                    "value": str(item.get("name") or ""),
                    "label": str(item.get("azDisplayName") or item.get("name") or ""),
                    "meta": item,
                }
                for item in client.list_zones(region_id)
                if item.get("name")
            ])
        if kind == "flavors":
            result = []
            cache_result = True
            started = time.monotonic()
            stock_task = asyncio.create_task(get_console_resource_stock(account, region_id))
            openapi_task = asyncio.create_task(asyncio.to_thread(client.list_flavors, region_id, az_name, available_only))
            stock_timeout = 30.0 if available_only else 1.9
            try:
                stock = await asyncio.wait_for(asyncio.shield(stock_task), timeout=stock_timeout)
            except asyncio.TimeoutError:
                track_background_task(stock_task)
                cache_result = False
                stock = {"status": "stock_pending", "message": "官方实时库存仍在后台刷新，已先返回 OpenAPI 快速规格"}
            except Exception as exc:
                cache_result = False
                stock = {"status": "stock_error", "message": str(exc)}
            stock_status = str(stock.get("status") or "")
            if available_only and stock_status != "ready":
                cache_result = False
            official_stock = official_flavor_stock_map(stock)
            official_specs = [item for item in (stock.get("specs") or []) if isinstance(item, dict)]
            if available_only and stock.get("status") == "ready" and (not official_specs or not official_stock):
                cache_result = False
                stock = {"status": "stock_empty", "message": "官方实时库存规格表为空，已先返回 OpenAPI 快速规格"}
                official_stock = {}
                official_specs = []
                stock_status = str(stock.get("status") or "")
            if available_only and stock_status in UNVERIFIED_STOCK_STATUSES:
                track_background_task(openapi_task)
                return [{
                    "value": "",
                    "label": "官方库存读取中，暂不能选择规格。请稍后重新打开创建窗口或刷新规格。",
                    "disabled": True,
                    "meta": {
                        "officialStockStatus": stock_status,
                        "officialStockMessage": stock.get("message", "官方库存仍在后台读取"),
                    },
                }]
            if official_specs:
                track_background_task(openapi_task)
                items = official_specs
            else:
                remaining_timeout = max(0.2, 6.0 - (time.monotonic() - started))
                try:
                    items = await asyncio.wait_for(asyncio.shield(openapi_task), timeout=remaining_timeout)
                except asyncio.TimeoutError:
                    track_background_task(openapi_task)
                    return [{
                        "value": "",
                        "label": "官方规格仍在加载，请稍候自动刷新",
                        "disabled": True,
                        "meta": {
                            "officialStockStatus": stock.get("status", "stock_pending"),
                            "officialStockMessage": stock.get("message", "官方规格仍在后台读取"),
                        },
                    }]
            for item in items:
                spec = official_spec_name(item) or item.get("specName") or item.get("flavorName") or "云主机规格"
                stock_item = official_stock.get(str(spec))
                sold_out = flavor_sold_out(item) or official_stock_sold_out(item) or official_stock_sold_out(stock_item)
                if available_only and official_stock and not stock_item:
                    continue
                if available_only and sold_out:
                    continue
                flavor_id = official_spec_id(item) or item.get("flavorID")
                if not flavor_id:
                    continue
                remembered_sold_out = remembered_flavor_sold_out(region_id, flavor_id, spec)
                if remembered_sold_out:
                    cache_result = False
                    sold_out = True
                cpu = official_spec_cpu(item)
                memory = official_spec_memory(item)
                detail = " / ".join(
                    value for value in [
                        f"{cpu}核" if cpu not in (None, "") else "",
                        f"{memory}GB" if memory not in (None, "") else "",
                        official_spec_arch(item),
                    ]
                    if value
                )
                meta = dict(item)
                meta.setdefault("specName", spec)
                meta.setdefault("flavorName", spec)
                meta.setdefault("flavorID", str(flavor_id))
                meta.setdefault("cpuNum", cpu)
                meta.setdefault("memSize", memory)
                meta.setdefault("cpuArch", official_spec_arch(item))
                meta["officialStockStatus"] = stock.get("status", "")
                meta["officialStockMessage"] = stock.get("message", "")
                if remembered_sold_out:
                    meta["rememberedSoldOutMessage"] = remembered_sold_out
                if stock_item:
                    meta["officialStock"] = stock_item
                if sold_out:
                    meta["soldOut"] = True
                    meta["disabled"] = True
                    meta["stockState"] = "sold_out"
                elif stock_status == "ready":
                    meta["stockState"] = "available"
                result.append({
                    "value": str(flavor_id),
                    "label": f"{spec} · {detail}" if detail else str(spec),
                    "meta": meta,
                    "disabled": sold_out,
                })
            if result:
                return cache_options(result, ttl_override=FLAVOR_STOCK_OPTION_TTL if available_only else None) if cache_result else result
            return result
        if kind == "disk_types":
            stock_task = asyncio.create_task(get_console_resource_stock(account, region_id))
            try:
                stock = await asyncio.wait_for(asyncio.shield(stock_task), timeout=2.4)
            except asyncio.TimeoutError:
                track_background_task(stock_task)
                stock = {"status": "stock_pending", "message": "官方磁盘库存仍在后台刷新，已先返回可选磁盘类型"}
            except Exception as exc:
                stock = {"status": "stock_error", "message": str(exc)}
            options = boot_disk_type_options(stock)
            if available_only:
                options = [option for option in options if not option.get("disabled")]
            return cache_options(options) if stock.get("status") == "ready" else options
        if kind == "security_groups":
            try:
                groups = [
                    {
                        "value": str(item.get("id") or item.get("securityGroupID") or ""),
                        "label": str(item.get("securityGroupName") or item.get("name") or "未命名安全组"),
                        "meta": item,
                    }
                    for item in client.list_security_groups_for_region(region_id, vpc_id)
                    if item.get("id") or item.get("securityGroupID")
                ]
                if groups:
                    return cache_options(groups)
            except CtyunClientError:
                pass
        if kind == "keypairs":
            return cache_options([
                {
                    "value": str(item.get("keyPairID") or ""),
                    "label": str(item.get("keyPairName") or item.get("keyPairID") or ""),
                    "meta": item,
                }
                for item in client.list_keypairs(region_id)
                if item.get("keyPairID")
            ])
    except HTTPException:
        raise
    except CtyunClientError as exc:
        if kind not in resource_types:
            raise HTTPException(status_code=501, detail=str(exc))
    resource_type = resource_types.get(kind)
    if not resource_type:
        raise HTTPException(status_code=404, detail="不支持的选项类型。")
    params: list[Any] = [account_id, resource_type, region_id]
    where = "account_id=? and resource_type=? and region=?"
    if vpc_id and resource_type in {"subnet", "route_table", "acl", "security_group"}:
        where += " and coalesce(json_extract(payload_json, '$.vpc_id'), json_extract(payload_json, '$.vpcID'), json_extract(payload_json, '$.vpcId'))=?"
        params.append(vpc_id)
    with connect() as conn:
        rows = conn.execute(
            f"select * from resources where {where} order by name",
            params,
        ).fetchall()
    result = []
    for source in rows:
        row = dict(source)
        payload = json.loads(row.get("payload_json") or "{}")
        result.append({
            "value": str(row["provider_id"]),
            "label": resource_option_label(resource_type, row, payload),
            "meta": payload,
        })
    if result:
        return cache_options(result)

    fallback_loaders = {
        "vpc": "list_vpcs",
        "subnet": "list_subnets",
        "image": "list_images",
        "eip": "list_eips",
        "ecs": "list_ecs",
        "security_group": "list_security_groups",
        "vip": "list_vips",
        "route_table": "list_route_tables",
        "acl": "list_acls",
    }
    loader_name = fallback_loaders.get(resource_type)
    if not loader_name:
        return cache_options(result)
    narrowed = dict(account)
    narrowed["region"] = region_id
    try:
        items = getattr(build_client(narrowed, settings.ctyun_mode), loader_name)()
    except (CtyunClientError, CtyunClientSkipped):
        return cache_options(result)
    fallback = []
    for item in items:
        if vpc_id and resource_type in {"subnet", "route_table", "acl", "security_group"}:
            item_vpc_id = item.get("vpc_id") or item.get("vpcID") or item.get("vpcId")
            if item_vpc_id and str(item_vpc_id) != str(vpc_id):
                continue
        provider_id = (
            item.get("id") or item.get("securityGroupID") or item.get("subnetID") or item.get("vpcID")
            or item.get("imageID") or item.get("imageUUID") or item.get("imageUuid") or item.get("image_uuid")
            or item.get("eipID")
        )
        if not provider_id:
            continue
        name = (
            item.get("name") or item.get("imageName") or item.get("nameZh") or item.get("nameEn")
            or item.get("securityGroupName") or item.get("subnetName") or item.get("vpcName") or provider_id
        )
        fallback.append({
            "value": str(provider_id),
            "label": resource_option_label(resource_type, {"name": name}, item),
            "meta": item,
        })
    return cache_options(fallback)


def normalize_prewarm_regions(region_ids: list[str], limit: int) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    max_count = max(1, min(int(limit or 8), 16))
    for region_id in region_ids or []:
        value = str(region_id or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        cleaned.append(value)
        if len(cleaned) >= max_count:
            break
    return cleaned


def option_prewarm_semaphore() -> asyncio.Semaphore:
    semaphore = getattr(app.state, "option_prewarm_semaphore", None)
    if semaphore is None:
        semaphore = asyncio.Semaphore(6)
        app.state.option_prewarm_semaphore = semaphore
    return semaphore


def track_background_task(task: asyncio.Task[Any]) -> None:
    tasks = getattr(app.state, "option_prewarm_tasks", None)
    if tasks is None:
        tasks = set()
        app.state.option_prewarm_tasks = tasks
    tasks.add(task)

    def done_callback(done: asyncio.Task[Any]) -> None:
        tasks.discard(done)
        with suppress(asyncio.CancelledError, Exception):
            done.exception()

    task.add_done_callback(done_callback)


async def prewarm_account_region_options(
    account: dict[str, Any],
    region_id: str,
    *,
    available_only: bool = True,
    include_images: bool = True,
) -> None:
    account_id = int(account["id"])
    key = (account_id, str(region_id))
    with option_prewarm_keys_lock:
        if key in option_prewarm_keys:
            return
        option_prewarm_keys.add(key)
    try:
        async with option_prewarm_semaphore():
            tasks: list[Any] = [
                account_options(
                    account_id,
                    "flavors",
                    region_id=region_id,
                    available_only=available_only,
                    user={"sub": "prewarm"},
                ),
                account_options(
                    account_id,
                    "disk_types",
                    region_id=region_id,
                    available_only=available_only,
                    user={"sub": "prewarm"},
                ),
            ]
            if include_images:
                tasks.append(asyncio.to_thread(account_image_options, account, region_id))
            results = await asyncio.gather(*tasks, return_exceptions=True)
            errors = [str(item) for item in results if isinstance(item, Exception)]
            if errors:
                record_operation(account_id, "options", region_id, "options_prewarm", "partial", " | ".join(errors[:3]))
    except Exception as exc:
        record_operation(account_id, "options", region_id, "options_prewarm", "failed", str(exc))
    finally:
        with option_prewarm_keys_lock:
            option_prewarm_keys.discard(key)


async def prewarm_account_options(
    account: dict[str, Any],
    region_ids: list[str],
    *,
    available_only: bool = True,
    include_images: bool = True,
) -> None:
    for offset in range(0, len(region_ids), 4):
        batch = region_ids[offset:offset + 4]
        await asyncio.gather(*(
            prewarm_account_region_options(
                account,
                region_id,
                available_only=available_only,
                include_images=include_images,
            )
            for region_id in batch
        ))
        await asyncio.sleep(0.05)


def split_region_ids(value: str | None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"[\s,，;；]+", str(value or "")):
        region_id = part.strip()
        if not region_id or region_id in seen:
            continue
        seen.add(region_id)
        result.append(region_id)
    return result


def option_inventory_cache_ready(account: dict[str, Any], region_id: str) -> bool:
    flavor_key = option_cache_key(account, "flavors", region_id=region_id, available_only=True)
    disk_key = option_cache_key(account, "disk_types", region_id=region_id, available_only=True)
    return option_cache_get(flavor_key) is not None and option_cache_get(disk_key) is not None


def region_usage_counts() -> dict[str, int]:
    with connect() as conn:
        rows = conn.execute(
            """
            select region, count(*) as total from resources
            where region != '' and resource_type in ('vpc', 'subnet', 'security_group', 'ecs', 'eip')
            group by region
            """
        ).fetchall()
    return {str(row["region"]): int(row["total"] or 0) for row in rows}


async def inventory_regions_for_account(account: dict[str, Any]) -> list[str]:
    explicit = split_region_ids(account.get("region"))
    if explicit:
        return explicit
    try:
        client = build_region_client(account, settings.ctyun_mode)
        items = await asyncio.to_thread(client.list_regions)
        return [
            str(item.get("regionID") or "").strip()
            for item in items
            if str(item.get("regionID") or "").strip()
        ]
    except Exception as exc:
        record_operation(account["id"], "options", None, "inventory_region_list", "failed", str(exc))
        return []


async def build_inventory_assignments(accounts: list[dict[str, Any]]) -> list[tuple[dict[str, Any], str]]:
    candidates: dict[str, list[dict[str, Any]]] = {}
    for account in accounts:
        for region_id in await inventory_regions_for_account(account):
            candidates.setdefault(region_id, []).append(account)
    usage = region_usage_counts()
    regions = sorted(candidates, key=lambda region: (-usage.get(region, 0), region))
    assignments: list[tuple[dict[str, Any], str]] = []
    per_account_load: dict[int, int] = {int(account["id"]): 0 for account in accounts}
    for region_id in regions:
        account = sorted(
            candidates[region_id],
            key=lambda item: (
                per_account_load.get(int(item["id"]), 0),
                0 if item.get("cookie_state_enc") else 1,
                int(item["id"]),
            ),
        )[0]
        if option_inventory_cache_ready(account, region_id):
            continue
        per_account_load[int(account["id"])] = per_account_load.get(int(account["id"]), 0) + 1
        assignments.append((account, region_id))
    return assignments


async def refresh_option_inventory_once() -> None:
    with connect() as conn:
        accounts = [
            dict(row)
            for row in conn.execute("select * from ctyun_accounts where status='enabled' order by id").fetchall()
        ]
    if not accounts:
        return
    assignments = await build_inventory_assignments(accounts)
    if not assignments:
        return
    record_operation(None, "options", None, "inventory_refresh", "started", f"待刷新资源池 {len(assignments)} 个")
    queue: asyncio.Queue[tuple[dict[str, Any], str]] = asyncio.Queue()
    for item in assignments:
        queue.put_nowait(item)

    async def worker(worker_id: int) -> None:
        while True:
            try:
                account, region_id = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                await prewarm_account_region_options(account, region_id, available_only=True, include_images=True)
                await asyncio.sleep(1.5 + worker_id * 0.3)
            finally:
                queue.task_done()

    workers = [asyncio.create_task(worker(index)) for index in range(settings.inventory_workers)]
    await asyncio.gather(*workers)
    record_operation(None, "options", None, "inventory_refresh", "success", f"已提交刷新资源池 {len(assignments)} 个")


async def option_inventory_refresh_loop() -> None:
    await asyncio.sleep(60)
    while True:
        try:
            await refresh_option_inventory_once()
        except Exception as exc:
            record_operation(None, "options", None, "inventory_refresh", "failed", str(exc))
        await asyncio.sleep(settings.inventory_refresh_seconds)


@app.post("/api/accounts/{account_id}/options/prewarm")
async def prewarm_options(account_id: int, body: OptionPrewarmBody, user: dict = Depends(require_user)) -> dict[str, Any]:
    account = get_account(account_id)
    region_ids = normalize_prewarm_regions(body.region_ids, body.limit)
    if not region_ids:
        return {"ok": True, "queued": 0}
    task = asyncio.create_task(prewarm_account_options(
        account,
        region_ids,
        available_only=body.available_only,
        include_images=body.include_images,
    ))
    track_background_task(task)
    return {"ok": True, "queued": len(region_ids)}


@app.post("/api/accounts/{account_id}/prices/{resource_type}")
def query_price(account_id: int, resource_type: str, body: PriceBody, user: dict = Depends(require_user)) -> dict[str, Any]:
    account = get_account(account_id)
    payload = dict(body.payload or {})
    try:
        client = build_client(account, settings.ctyun_mode)
        if resource_type == "eip":
            return client.query_eip_create_price(payload)
        if resource_type == "ecs":
            return client.query_ecs_create_price(payload)
    except CtyunClientSkipped as exc:
        raise HTTPException(status_code=501, detail=str(exc))
    except CtyunClientError as exc:
        if resource_type == "ecs" and ecs_flavor_sold_out_error(str(exc)):
            remember_flavor_sold_out(payload, str(exc))
        raise HTTPException(status_code=501, detail=str(exc))
    raise HTTPException(status_code=404, detail="不支持的询价类型。")


@app.post("/api/accounts/{account_id}/ecs/renew/price")
def query_ecs_renew_price(account_id: int, body: RenewBody, user: dict = Depends(require_user)) -> dict[str, Any]:
    account = get_account(account_id)
    try:
        return CtyunConsoleApi(account).renew_price(body.resource_ids, body.month, body.by_year)
    except CtyunConsoleApiError as exc:
        raise HTTPException(status_code=501, detail=str(exc))


@app.post("/api/accounts/{account_id}/ecs/renew/submit")
def submit_ecs_renew(account_id: int, body: RenewBody, user: dict = Depends(require_user)) -> dict[str, Any]:
    account = get_account(account_id)
    try:
        result = CtyunConsoleApi(account).renew_submit(body.resource_ids, body.month, body.by_year)
        record_operation(
            account_id,
            "ecs",
            ",".join(body.resource_ids),
            "renew",
            "submitted",
            json.dumps(result, ensure_ascii=False),
        )
        option_cache_clear(account_id, ("ecs",))
        queue_post_action_sync(account, "ecs", "renew", {"resource_id": body.resource_ids[0] if body.resource_ids else ""})
        return result
    except CtyunConsoleApiError as exc:
        record_operation(account_id, "ecs", ",".join(body.resource_ids or []), "renew", "failed", str(exc))
        raise HTTPException(status_code=501, detail=str(exc))


@app.post("/api/accounts/{account_id}/ecs/renew/order-status")
def query_ecs_renew_order_status(account_id: int, body: RenewOrderStatusBody, user: dict = Depends(require_user)) -> dict[str, Any]:
    account = get_account(account_id)
    try:
        return CtyunConsoleApi(account).renew_order_status(body.master_order_id)
    except CtyunConsoleApiError as exc:
        raise HTTPException(status_code=501, detail=str(exc))


@app.post("/api/accounts/{account_id}/actions")
async def run_action(account_id: int, body: ActionBody, user: dict = Depends(require_user)) -> dict[str, Any]:
    account = get_account(account_id)
    payload = dict(body.payload or {})
    if body.resource_id:
        with connect() as conn:
            row = conn.execute(
                "select * from resources where account_id=? and resource_type=? and provider_id=?",
                (account_id, body.resource_type, body.resource_id),
            ).fetchone()
        if row:
            cached = dict(row)
            payload.setdefault("resource_id", cached.get("provider_id"))
            cached_payload = json.loads(cached.get("payload_json") or "{}")
            cached_region = (
                cached_payload.get("regionID")
                or cached_payload.get("regionId")
                or cached_payload.get("region_id")
                or cached_payload.get("regionUUID")
                or cached_payload.get("regionUuid")
                or cached.get("region")
            )
            payload.setdefault("regionID", cached_region)
            if body.resource_type == "ecs":
                instance_id = (
                    cached_payload.get("instanceID")
                    or cached_payload.get("instanceId")
                    or cached_payload.get("instance_id")
                    or cached_payload.get("deviceUUID")
                    or cached_payload.get("deviceUuid")
                )
                if instance_id:
                    payload.setdefault("instanceID", instance_id)
            if body.resource_type == "image":
                payload.setdefault("imageID", cached.get("provider_id"))
            if body.resource_type == "vip":
                payload.setdefault("haVipID", cached.get("provider_id"))
            payload.setdefault("_cached", cached_payload)
    try:
        if body.resource_type == "ecs" and body.action == "change_private_ip":
            payload["resource_id"] = body.resource_id or payload.get("resource_id") or payload.get("instanceID") or payload.get("instanceId")
            result = await change_console_ecs_private_ip(account, payload)
            confirmed_ip = str(result.get("privateIP") or payload.get("privateIP") or payload.get("privateIp") or payload.get("ipAddress") or "").strip()
            if confirmed_ip:
                payload["_confirmed_private_ip"] = confirmed_ip
                apply_confirmed_ecs_private_ip(
                    account_id,
                    str(payload.get("resource_id") or ""),
                    confirmed_ip,
                    str(result.get("subnetID") or payload.get("subnetID") or payload.get("subnetId") or ""),
                    str(result.get("networkInterfaceID") or payload.get("networkInterfaceID") or payload.get("networkCardID") or payload.get("portID") or ""),
                )
        else:
            client = build_client(account, settings.ctyun_mode)
            result = await asyncio.to_thread(client.action, body.resource_type, body.action, payload)
        submit_summary = action_submit_summary(body.resource_type, body.action, payload)
        if submit_summary:
            record_operation(
                account_id,
                body.resource_type,
                body.resource_id,
                body.action,
                "submitted",
                json.dumps({"提交参数": submit_summary, "接口返回": result}, ensure_ascii=False),
            )
        else:
            record_operation(account_id, body.resource_type, body.resource_id, body.action, "success", json.dumps(result, ensure_ascii=False))
        option_cache_clear(account_id, option_kinds_after_action(body.resource_type, body.action))
        sync_result = queue_post_action_sync(account, body.resource_type, body.action, {**payload, "resource_id": body.resource_id})
        if isinstance(result, dict):
            return {**result, "post_sync": sync_result}
        return {"result": result, "post_sync": sync_result}
    except (CtyunClientError, RuntimeError) as exc:
        if body.resource_type == "ecs" and body.action == "create" and ecs_flavor_sold_out_error(str(exc)):
            remember_flavor_sold_out(payload, str(exc))
        record_operation(account_id, body.resource_type, body.resource_id, body.action, "failed", str(exc))
        raise HTTPException(status_code=501, detail=str(exc))


def compact_remote_login_error(message: str) -> str:
    text = str(message or "")
    if "official_console_missing_saved_cookie" in text or "official_cookie_missing" in text:
        return "账号没有保存网页登录态，请先打开官方控制台完成一次登录。"
    if "official_console_unauthorized" in text or "official_cookie_unauthorized" in text:
        return "官方网页登录态已失效，请重新登录天翼云账号。"
    if "Invalid signature" in text or "验证合法性失败" in text:
        return "官方控制台接口签名校验失败，请稍后重试。"
    if "StatusNotValid" in text or "云主机状态无效" in text:
        return "官方远程登录要求云主机处于运行中，请先确认云主机状态。"
    if "RegionVersion.NotSupported" in text or "当前API不支持该资源池" in text:
        return "当前资源池不支持旧版 OpenAPI 远程登录，已优先尝试官方控制台接口。"
    if "Instance.NotFound" in text or "云主机不存在" in text:
        return "官方接口没有找到这台云主机，可能是资源缓存过旧，请先同步当前页。"
    if "官方接口没有返回远程登录地址" in text or "未返回 VNC 地址" in text:
        return "官方接口没有返回远程登录地址，请确认云主机是否可远程登录。"
    if '"errstatus": 3012' in text or "errstatus': 3012" in text:
        return "官方控制台拒绝生成远程登录地址，通常是云主机未运行、已过期或状态异常。"
    if text and len(text) <= 180:
        return text
    return "官方远程登录接口暂不可用，请刷新资源或在官方控制台确认云主机状态。"


def cached_remote_login_status_hint(cached: dict[str, Any], payload: dict[str, Any]) -> str:
    raw_status = str(
        payload.get("instanceStatus")
        or payload.get("status")
        or cached.get("status")
        or ""
    ).strip()
    normalized = raw_status.lower()
    if not normalized or normalized in {"running", "active", "started"}:
        return ""
    labels = {
        "expired": "已过期",
        "stopped": "已关机",
        "shutoff": "已关机",
        "error": "异常",
        "failed": "异常",
        "operation_failed": "操作失败",
    }
    label = labels.get(normalized, raw_status)
    return f"云主机当前状态为 {label}，官方远程登录通常要求云主机处于运行中。"


@app.post("/api/accounts/{account_id}/ecs/{resource_id}/remote-login")
async def ecs_remote_login(account_id: int, resource_id: str, user: dict = Depends(require_user)) -> dict[str, Any]:
    account = get_account(account_id)
    with connect() as conn:
        row = conn.execute(
            "select * from resources where account_id=? and resource_type='ecs' and provider_id=?",
            (account_id, resource_id),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="未找到已同步的云主机资源。")
    cached = dict(row)
    cached_payload = json.loads(cached.get("payload_json") or "{}")
    region_id = (
        cached_payload.get("regionID")
        or cached_payload.get("regionId")
        or cached_payload.get("region_id")
        or cached_payload.get("regionUUID")
        or cached_payload.get("regionUuid")
        or cached.get("region")
    )
    instance_id = (
        cached_payload.get("instanceID")
        or cached_payload.get("instanceId")
        or cached_payload.get("instance_id")
        or cached_payload.get("deviceUUID")
        or cached_payload.get("deviceUuid")
        or resource_id
    )
    payload = {
        **cached_payload,
        "_cached": cached_payload,
        "resource_id": resource_id,
        "regionID": region_id,
        "instanceID": instance_id,
    }
    status_hint = cached_remote_login_status_hint(cached, cached_payload)
    console_error = ""
    try:
        result = await get_console_ecs_vnc_url(account, payload)
        record_operation(account_id, "ecs", resource_id, "remote_login", "success", "console_cookie")
        return result
    except Exception as exc:
        console_error = compact_remote_login_error(str(exc))
        detail = status_hint or console_error
        record_operation(account_id, "ecs", resource_id, "remote_login", "failed", detail)
        raise HTTPException(status_code=501, detail=detail)


@app.get("/api/accounts/{account_id}/totp")
def totp(account_id: int, user: dict = Depends(require_user)) -> dict[str, Any]:
    account = get_account(account_id)
    secret = decrypt_text(account.get("totp_secret_enc"))
    now = int(time.time())
    period = 30
    remaining = period - (now % period)
    return {
        "code": current_totp(secret),
        "has_totp": bool(secret),
        "period": period,
        "remaining": remaining,
        "server_time": now,
        "expires_at": now + remaining,
    }


@app.post("/api/accounts/{account_id}/recharge/open")
async def open_recharge(account_id: int, user: dict = Depends(require_user)) -> dict[str, Any]:
    account = get_account(account_id)
    result = await login_and_open_recharge(account)
    if result.get("status") == "ready":
        finance = await get_finance(account)
        persist_finance(account_id, finance)
        result["available"] = finance.get("available")
        result["owe"] = finance.get("owe")
        result["cookie_state_enc"] = finance.get("cookie_state_enc") or result.get("cookie_state_enc")
    if result.get("cookie_state_enc"):
        with connect() as conn:
            conn.execute("update ctyun_accounts set cookie_state_enc=?, updated_at=current_timestamp where id=?", (result["cookie_state_enc"], account_id))
    record_operation(account_id, "recharge", None, "open_recharge", result.get("status", "unknown"), result.get("message", ""))
    result.pop("cookie_state_enc", None)
    return result


@app.post("/api/accounts/{account_id}/console/open")
async def open_console(account_id: int, user: dict = Depends(require_user)) -> dict[str, Any]:
    account = get_account(account_id)
    storage_state = storage_state_for_console_bridge(account.get("cookie_state_enc"))
    cookie_count = len(storage_state.get("cookies") or [])
    if cookie_count <= 0:
        record_operation(account_id, "account", str(account_id), "open_console", "failed", "no_ctyun_cookie")
        raise HTTPException(status_code=409, detail="该账号没有可用的天翼云登录态，请先刷新余额或充值保存 cookie。")
    record_operation(account_id, "account", str(account_id), "open_console", "ready", f"{cookie_count} ctyun cookies")
    return {
        "status": "ready",
        "message": "官方控制台登录态已就绪，请使用控制台桥接扩展打开。",
        "target_url": "https://console.ctyun.cn/console/index/#/console",
        "cookie_count": cookie_count,
    }


@app.post("/api/accounts/{account_id}/console/bridge-state")
async def console_bridge_state(account_id: int, user: dict = Depends(require_user)) -> dict[str, Any]:
    account = get_account(account_id)
    cookie_state_enc = account.get("cookie_state_enc")
    storage_state = storage_state_for_console_bridge(cookie_state_enc)
    cookie_count = len(storage_state.get("cookies") or [])
    if cookie_count <= 0:
        record_operation(account_id, "account", str(account_id), "console_bridge", "failed", "no_ctyun_cookie")
        raise HTTPException(status_code=409, detail="该账号没有可导出的天翼云登录态，请先完成一次账号登录或刷新余额/充值以保存 cookie。")
    target_url = "https://console.ctyun.cn/console/index/#/console"
    keepalive_message = ""
    try:
        keepalive = await asyncio.wait_for(keepalive_saved_cookie(account), timeout=8)
        if keepalive.get("status") != "ready":
            keepalive_message = "；后台保活未确认，如果打开后是登录页，请刷新账号登录态"
    except Exception:
        keepalive_message = "；后台保活超时，如果打开后是登录页，请刷新账号登录态"
    record_operation(account_id, "account", str(account_id), "console_bridge", "success", f"{cookie_count} ctyun cookies")
    return {
        "status": "ready",
        "message": f"已生成本机浏览器控制台登录态{keepalive_message}",
        "account_id": account_id,
        "account_name": account.get("name") or f"account-{account_id}",
        "target_url": target_url,
        "storage_state": storage_state,
        "cookie_count": cookie_count,
        "origin_count": len(storage_state.get("origins") or []),
        "issued_at": int(time.time()),
    }


@app.post("/api/accounts/{account_id}/recharge/order")
async def recharge_order(
    account_id: int,
    body: RechargeBody,
    user: dict = Depends(require_user),
) -> dict[str, Any]:
    if body.payment_method not in PAYMENT_METHODS:
        raise HTTPException(status_code=422, detail="不支持的支付方式")
    try:
        amount = Decimal(body.amount.strip())
    except (InvalidOperation, AttributeError):
        raise HTTPException(status_code=422, detail="充值金额格式无效")
    if amount <= 0 or amount >= Decimal("100000000"):
        raise HTTPException(status_code=422, detail="充值金额必须大于 0 且小于 1 亿元")
    if amount.as_tuple().exponent < -2:
        raise HTTPException(status_code=422, detail="充值金额最多保留两位小数")

    amount_text = format(amount.quantize(Decimal("0.01")), "f")
    account = get_account(account_id)
    started = time.monotonic()
    result = await create_recharge_order(account, amount_text)
    persist_account_session(account_id, result)
    record_operation(
        account_id,
        "recharge",
        result.get("order_no"),
        "create_recharge_order",
        result.get("status", "unknown"),
        result.get("message", ""),
    )
    result.pop("cookie_state_enc", None)
    if result.get("status") != "ready":
        raise HTTPException(status_code=502, detail=result.get("message", "创建充值订单失败"))
    payment = await activate_payment_method(account, body.payment_method)
    persist_account_session(account_id, payment)
    record_operation(
        account_id,
        "recharge",
        result.get("order_no"),
        "activate_payment",
        payment.get("status", "unknown"),
        payment.get("message", ""),
    )
    payment.pop("cookie_state_enc", None)
    result["payment_status"] = payment.get("status")
    result["payment_message"] = payment.get("message")
    result["payment_method"] = payment.get("payment_method", body.payment_method)
    result["qr_available"] = bool(payment.get("qr_available"))
    result["qr_cached"] = bool(payment.get("qr_cached"))
    result["qr_remaining_seconds"] = payment.get("qr_remaining_seconds")
    result["qr_expires_in"] = payment.get("qr_expires_in")
    result["qr_server_time"] = payment.get("qr_server_time")
    result["qr_expires_at_epoch"] = payment.get("qr_expires_at_epoch")
    result["fast_path"] = bool(result.get("fast_path"))
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    return result


@app.post("/api/accounts/{account_id}/recharge/payment")
async def recharge_payment(
    account_id: int,
    body: PaymentBody,
    user: dict = Depends(require_user),
) -> dict[str, Any]:
    if body.payment_method not in PAYMENT_METHODS:
        raise HTTPException(status_code=422, detail="不支持的支付方式")
    account = get_account(account_id)
    result = await activate_payment_method(account, body.payment_method)
    persist_account_session(account_id, result)
    record_operation(
        account_id,
        "recharge",
        None,
        "activate_payment",
        result.get("status", "unknown"),
        result.get("message", ""),
    )
    result.pop("cookie_state_enc", None)
    if result.get("status") != "ready":
        raise HTTPException(status_code=502, detail=result.get("message", "加载支付方式失败"))
    return result


@app.post("/api/accounts/{account_id}/recharge/close")
async def recharge_close(account_id: int, user: dict = Depends(require_user)) -> dict[str, Any]:
    get_account(account_id)
    result = await close_recharge_session(account_id)
    persist_account_session(account_id, result)
    record_operation(
        account_id,
        "recharge",
        None,
        "close_recharge",
        result.get("status", "unknown"),
        result.get("message", ""),
    )
    result.pop("cookie_state_enc", None)
    return result


@app.get("/api/accounts/{account_id}/recharge/qr")
async def recharge_qr(account_id: int, user: dict = Depends(require_user)) -> Response:
    account = get_account(account_id)
    result = await get_payment_qr(account)
    if result.get("status") != "ready" or not result.get("png"):
        raise HTTPException(status_code=404, detail=result.get("message", "收款码尚未生成"))
    return Response(
        content=result["png"],
        media_type="image/png",
        headers=NO_CACHE_HEADERS,
    )


@app.post("/api/accounts/{account_id}/recharge/qr/refresh")
async def recharge_qr_refresh(account_id: int, user: dict = Depends(require_user)) -> dict[str, Any]:
    account = get_account(account_id)
    result = await refresh_payment_qr(account)
    record_operation(
        account_id,
        "recharge",
        None,
        "refresh_payment_qr",
        result.get("status", "unknown"),
        result.get("message", ""),
    )
    if result.get("status") != "ready":
        raise HTTPException(status_code=409, detail=result.get("message", "二维码暂时无法刷新"))
    return result


@app.get("/api/accounts/{account_id}/recharge/status")
async def recharge_status(account_id: int, user: dict = Depends(require_user)) -> dict[str, Any]:
    account = get_account(account_id)
    result = await check_recharge_payment_status(account)
    persist_account_session(account_id, result)
    if result.get("status") in {"paid", "success", "completed"}:
        result["message"] = result.get("message") or "官方支付状态接口显示支付成功"
        asyncio.create_task(refresh_account_finance_once(account_id, delay=0, force_api=True))
        asyncio.create_task(refresh_account_finance_once(account_id, delay=3, force_api=True))
    result.pop("cookie_state_enc", None)
    return result


@app.websocket("/websockify")
async def websockify(websocket: WebSocket) -> None:
    if not verify_session(websocket.cookies.get("ctyun_manager_session")):
        await websocket.close(code=4401)
        return
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", 5900)
    except OSError:
        await websocket.close(code=1011)
        return

    requested_protocols = websocket.headers.get("sec-websocket-protocol", "")
    subprotocol = "binary" if "binary" in requested_protocols.lower() else None
    await websocket.accept(subprotocol=subprotocol)

    async def browser_to_vnc() -> None:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            data = message.get("bytes")
            if data is None and message.get("text") is not None:
                data = message["text"].encode("latin-1")
            if data:
                writer.write(data)
                await writer.drain()

    async def vnc_to_browser() -> None:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            await websocket.send_bytes(data)

    tasks = [
        asyncio.create_task(browser_to_vnc()),
        asyncio.create_task(vnc_to_browser()),
    ]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        for task in tasks:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        writer.close()
        with suppress(Exception):
            await writer.wait_closed()


@app.get("/api/vnc/health")
async def vnc_health(user: dict = Depends(require_user)) -> dict[str, Any]:
    writer = None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", 5900),
            timeout=2,
        )
        banner = await asyncio.wait_for(reader.readexactly(12), timeout=2)
        ready = banner.startswith(b"RFB ")
        return {
            "ready": ready,
            "message": "VNC 服务正常" if ready else "VNC 服务返回了无效握手",
        }
    except Exception as exc:
        return {"ready": False, "message": f"VNC 服务未就绪：{exc}"}
    finally:
        if writer:
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()


@app.get("/api/operations")
def operations(user: dict = Depends(require_user)) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("select * from operations order by id desc limit 200").fetchall()
    return rows_to_dicts(rows)

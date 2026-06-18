import asyncio
import copy
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import suppress
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import settings
from .db import connect, migrate, rows_to_dicts
from .security import decrypt_text, encrypt_text, encryption_key_status, mask, sign_session, verify_password, verify_session
from .services.browser_automation import (
    activate_payment_method,
    check_recharge_payment_status,
    close_browser_sessions,
    close_recharge_session,
    create_recharge_order,
    current_totp,
    get_finance,
    get_console_eip_create_options,
    get_console_resource_stock,
    get_payment_qr,
    login_and_open_console,
    login_and_open_recharge,
    normalize_totp_secret,
    refresh_payment_qr,
    reset_browser_session,
)
from .services.ctyun_client import CtyunClientError, CtyunClientSkipped, build_client, build_region_client
from .services.ikuai_client import IkuaiClient, IkuaiClientError, IKUAI_MENU_GROUPS, IKUAI_SECTION_ACTIONS, SECTION_CALLS, gateway_summary, normalize_base_url

APP_VERSION = "2026.06.19.0206"
NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


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


class SyncBody(BaseModel):
    types: list[str]


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


class IkuaiSectionActionBody(BaseModel):
    action: str
    payload: dict[str, Any] = {}


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
account_sync_locks: dict[int, threading.Lock] = {}
account_sync_locks_guard = threading.Lock()
resource_sync_locks: dict[tuple[int, str], threading.Lock] = {}
resource_sync_locks_guard = threading.Lock()
account_id_verified: set[int] = set()
recharge_prewarm_lock = asyncio.Lock()
option_cache: dict[tuple[Any, ...], tuple[float, list[dict[str, Any]]]] = {}
option_cache_lock = threading.Lock()
option_prewarm_keys: set[tuple[int, str]] = set()
option_prewarm_keys_lock = threading.Lock()
PERSISTENT_OPTION_KINDS = {"flavors", "disk_types", "images_public"}
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
    worker = threading.Thread(target=background_sync_loop, name="ctyun-background-sync", daemon=True)
    worker.start()


@app.on_event("startup")
async def startup_finance_refresh() -> None:
    app.state.finance_task = asyncio.create_task(finance_refresh_loop())
    app.state.inventory_task = asyncio.create_task(option_inventory_refresh_loop())
    app.state.recharge_prewarm_task = asyncio.create_task(recharge_prewarm_loop())


@app.on_event("shutdown")
async def shutdown() -> None:
    task = getattr(app.state, "finance_task", None)
    if task:
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


def option_cache_set(kind: str, key: tuple[Any, ...], value: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ttl = OPTION_CACHE_TTLS.get(kind, 0)
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


def get_account(account_id: int) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("select * from ctyun_accounts where id = ?", (account_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="account_not_found")
    return dict(row)


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
            accounts = [
                dict(row)
                for row in conn.execute(
                    "select * from ctyun_accounts where status='enabled' order by id"
                ).fetchall()
            ]
        semaphore = asyncio.Semaphore(3)

        async def refresh_one(account: dict[str, Any]) -> None:
            async with semaphore:
                try:
                    result = await get_finance(account)
                    if result.get("status") == "interactive":
                        return
                    persist_finance(account["id"], result)
                    if result.get("status") != "ready":
                        record_operation(
                            account["id"],
                            "finance",
                            None,
                            "keepalive",
                            result.get("status", "unknown"),
                            result.get("message", ""),
                        )
                    await discover_provider_account_id(account)
                except Exception as exc:
                    record_operation(account["id"], "finance", None, "keepalive", "failed", str(exc))

        await asyncio.gather(*(refresh_one(account) for account in accounts))
        await asyncio.sleep(settings.finance_refresh_seconds)


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


@app.get("/api/version")
def version() -> dict[str, Any]:
    return {
        "version": APP_VERSION,
        "build_time": "2026-06-19 02:06 Asia/Shanghai",
        "ctyun_mode": settings.ctyun_mode,
        "encryption_key_status": encryption_key_status(),
    }


@app.post("/api/login")
def login(body: LoginBody, response: Response) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("select * from users where username = ?", (body.username,)).fetchone()
    if not row or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="bad_credentials")
    token = sign_session({"sub": body.username})
    response.set_cookie("ctyun_manager_session", token, httponly=True, samesite="lax", max_age=86400)
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
        with connect() as conn:
            conn.execute("delete from resources where account_id=? and resource_type=?", (account["id"], resource_type))
            for item in items:
                if not item.get("id"):
                    continue
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


def account_for_fast_sync(account: dict[str, Any], kinds: list[str]) -> dict[str, Any]:
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
    account_lock = account_sync_lock(account_id)
    if not account_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="该账号已有同步任务正在运行，请稍后再试。")
    try:
        result = sync_account_resources(account_for_fast_sync(account, kinds), kinds)
        status_value = "success" if not result["errors"] else "partial"
        record_operation(account_id, "account", str(account_id), "auto_sync", status_value, json.dumps(result, ensure_ascii=False))
        option_cache_clear(account_id, option_kinds_for_resource_types(kinds))
        return result
    finally:
        account_lock.release()


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

    def cache_options(value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return option_cache_set(kind, cache_key, value)

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
            try:
                stock = await asyncio.wait_for(asyncio.shield(stock_task), timeout=1.9)
            except asyncio.TimeoutError:
                track_background_task(stock_task)
                cache_result = False
                stock = {"status": "stock_pending", "message": "官方实时库存仍在后台刷新，已先返回 OpenAPI 快速规格"}
            except Exception as exc:
                cache_result = False
                stock = {"status": "stock_error", "message": str(exc)}
            official_stock = official_flavor_stock_map(stock)
            official_specs = [item for item in (stock.get("specs") or []) if isinstance(item, dict)]
            if available_only and stock.get("status") == "ready" and (not official_specs or not official_stock):
                cache_result = False
                stock = {"status": "stock_empty", "message": "官方实时库存规格表为空，已先返回 OpenAPI 快速规格"}
                official_stock = {}
                official_specs = []
            if official_specs:
                track_background_task(openapi_task)
                items = official_specs
            else:
                remaining_timeout = max(0.2, 2.8 - (time.monotonic() - started))
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
                if stock_item:
                    meta["officialStock"] = stock_item
                if sold_out:
                    meta["soldOut"] = True
                    meta["disabled"] = True
                result.append({
                    "value": str(flavor_id),
                    "label": f"{spec} · {detail}" if detail else str(spec),
                    "meta": meta,
                    "disabled": sold_out,
                })
            return cache_options(result) if cache_result and stock.get("status") == "ready" else result
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
        raise HTTPException(status_code=501, detail=str(exc))
    raise HTTPException(status_code=404, detail="不支持的询价类型。")


@app.post("/api/accounts/{account_id}/actions")
def run_action(account_id: int, body: ActionBody, user: dict = Depends(require_user)) -> dict[str, Any]:
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
        result = build_client(account, settings.ctyun_mode).action(body.resource_type, body.action, payload)
        record_operation(account_id, body.resource_type, body.resource_id, body.action, "success", json.dumps(result, ensure_ascii=False))
        option_cache_clear(account_id, option_kinds_after_action(body.resource_type, body.action))
        return result
    except CtyunClientError as exc:
        record_operation(account_id, body.resource_type, body.resource_id, body.action, "failed", str(exc))
        raise HTTPException(status_code=501, detail=str(exc))


@app.post("/api/accounts/{account_id}/ecs/{resource_id}/remote-login")
def ecs_remote_login(account_id: int, resource_id: str, user: dict = Depends(require_user)) -> dict[str, Any]:
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
    try:
        vnc = build_client(account, settings.ctyun_mode).query_ecs_vnc_details(payload)
        url = str(vnc.get("url") or "")
        record_operation(account_id, "ecs", resource_id, "remote_login", "success", f"OpenAPI VNC {vnc.get('path', '')}")
        return {
            "status": "ready",
            "message": "已通过天翼云官方 OpenAPI 获取远程登录地址。",
            "url": url,
            "viewer_url": url,
            "source": "openapi",
        }
    except CtyunClientError as exc:
        record_operation(account_id, "ecs", resource_id, "remote_login", "failed", str(exc))
        raise HTTPException(status_code=501, detail=str(exc))


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
    result = await login_and_open_console(account)
    if result.get("cookie_state_enc"):
        with connect() as conn:
            conn.execute("update ctyun_accounts set cookie_state_enc=?, updated_at=current_timestamp where id=?", (result["cookie_state_enc"], account_id))
    record_operation(account_id, "account", str(account_id), "open_console", result.get("status", "unknown"), result.get("message", ""))
    result.pop("cookie_state_enc", None)
    return result


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
        finance = await get_finance(account, force_api=True)
        if finance.get("status") != "interactive":
            persist_finance(account_id, finance)
            result["available"] = finance.get("available")
            result["owe"] = finance.get("owe")
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

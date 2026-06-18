import asyncio
import base64
import json
import os
import re
import struct
import time
import zlib
from contextlib import suppress
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

import pyotp

from ..config import settings
from ..security import decrypt_text, encrypt_text


RECHARGE_URL = "https://www.ctyun.cn/console/expense/fund/recharge"
RECHARGE_CREATE_URL = "https://www.ctyun.cn/gw/account/cash/Recharge"
RECHARGE_FRONT_URL = "https://www.ctyun.cn/virtual/redirect/funddetail"
BALANCE_URL = "https://www.ctyun.cn/gw/account/giftcard/QueryBookSumm"
OWE_URL = "https://www.ctyun.cn/v1/bcc/bill/QueryOwe"
ACCOUNT_INFO_URL = "https://www.ctyun.cn/v2/bcc/basicData/getCurrentInfo"
CONSOLE_ORIGIN = "https://console.ctyun.cn"
CONSOLE_HOME_URL = "https://console.ctyun.cn/"
CONSOLE_ECS_CREATE_URL = "https://console.ctyun.cn/compute/index/#/ecm/ecmCreate"
CONSOLE_NETWORK_URL = "https://console.ctyun.cn/network/index/"
TRANSIENT_PAGE_ERRORS = (
    "execution context was destroyed",
    "cannot find context with specified id",
    "most likely because of a navigation",
    "target page, context or browser has been closed",
)


def normalize_totp_secret(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if value.startswith("otpauth://"):
        parsed = urlparse(value)
        secret = parse_qs(parsed.query).get("secret", [None])[0]
        if not secret:
            raise ValueError("otpauth URI 中缺少 secret")
        value = secret
    normalized = value.replace(" ", "").upper()
    pyotp.TOTP(normalized).at(0)
    return normalized


def current_totp(secret: str | None) -> str | None:
    if not secret:
        return None
    return pyotp.TOTP(normalize_totp_secret(secret)).now()


@dataclass
class BrowserSession:
    context: Any
    page: Any
    last_page_refresh: float = 0
    interactive_until: float = 0
    last_payment: dict[str, Any] | None = None
    fast_recharge_disabled_until: float = 0


_playwright: Any = None
_browser: Any = None
_sessions: dict[int, BrowserSession] = {}
_browser_lock = asyncio.Lock()
_account_operation_locks: dict[int, asyncio.Lock] = {}
_console_stock_cache: dict[tuple[int, str], tuple[float, dict[str, Any]]] = {}
_console_shared_stock_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_console_region_stock_locks: dict[str, asyncio.Lock] = {}
_console_region_platform_cache: dict[tuple[int, str], tuple[float, str]] = {}
_console_eip_options_cache: dict[tuple[int, str], tuple[float, dict[str, Any]]] = {}
PAYMENT_METHODS = {
    "alipay": "支付宝",
    "bestpay": "翼支付",
    "wechat": "微信支付",
}
PAYMENT_CHANNEL_CODES = {
    "alipay": "7",
    "wechat": "8",
    "bestpay": "9",
}


def _account_operation_lock(account_id: int) -> asyncio.Lock:
    lock = _account_operation_locks.get(account_id)
    if lock is None:
        lock = asyncio.Lock()
        _account_operation_locks[account_id] = lock
    return lock


def _console_region_stock_lock(region_id: str) -> asyncio.Lock:
    key = str(region_id)
    lock = _console_region_stock_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _console_region_stock_locks[key] = lock
    return lock


def _viewer_url() -> str:
    return "/static/vnc.html"


async def _start_browser() -> Any:
    global _playwright, _browser
    if _browser:
        return _browser
    from playwright.async_api import async_playwright

    _playwright = await async_playwright().start()
    headful = settings.browser_headful and bool(os.getenv("DISPLAY"))
    launch_kwargs: dict[str, Any] = {
        "headless": not headful,
        "args": [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--window-position=0,0",
            "--window-size=1440,900",
            "--start-maximized",
            "--kiosk",
        ],
    }
    executable_path = settings.browser_executable_path.strip()
    if not executable_path:
        for candidate in (
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ):
            if os.path.exists(candidate):
                executable_path = candidate
                break
    if executable_path:
        launch_kwargs["executable_path"] = executable_path
    _browser = await _playwright.chromium.launch(
        **launch_kwargs,
    )
    return _browser


async def _get_session(account: dict[str, Any]) -> BrowserSession:
    account_id = int(account["id"])
    existing = _sessions.get(account_id)
    if existing:
        return existing

    async with _browser_lock:
        existing = _sessions.get(account_id)
        if existing:
            return existing
        browser = await _start_browser()
        state = None
        cookie_state = decrypt_text(account.get("cookie_state_enc"))
        if cookie_state:
            try:
                state = json.loads(cookie_state)
            except json.JSONDecodeError:
                state = None
        context = await browser.new_context(
            storage_state=state,
            locale="zh-CN",
            viewport={"width": 1440, "height": 900},
        )
        page = await context.new_page()
        session = BrowserSession(context=context, page=page)
        _sessions[account_id] = session
        return session


async def _visible_text(page: Any) -> str:
    for _ in range(3):
        try:
            return (await page.locator("body").inner_text(timeout=3000))[:10000]
        except Exception as exc:
            if not _is_transient_page_error(exc):
                return ""
            await _wait_for_page_ready(page)
    return ""


async def _page_diagnostic(page: Any) -> str:
    try:
        title = await page.title()
    except Exception:
        title = ""
    text = re.sub(r"\s+", " ", await _visible_text(page)).strip()[:300]
    return f"url={page.url}; title={title or '-'}; text={text or '-'}"


def _is_transient_page_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(fragment in message for fragment in TRANSIENT_PAGE_ERRORS)


def _is_login_url(url: str) -> bool:
    return "/h5/auth/login" in url or "/auth/login" in url


def _is_console_url(url: str) -> bool:
    return "ctyun.cn/console/" in url and not _is_login_url(url)


def _is_payment_url(url: str) -> bool:
    return "ctyun.cn/checkstand/" in url


def _is_recharge_url(url: str) -> bool:
    return "/console/expense/fund/recharge" in url


def _amount_to_cents(amount: str) -> str:
    try:
        value = Decimal(str(amount).strip())
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError("充值金额格式无效") from exc
    if value <= 0:
        raise ValueError("充值金额必须大于 0")
    return str(int((value * Decimal("100")).quantize(Decimal("1"))))


def _normalize_payment_url(url: str) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    if text.startswith("//"):
        text = f"https:{text}"
    if not urlparse(text).scheme:
        text = urljoin("https://www.ctyun.cn", text)
    return text


def _extract_payment_url(data: Any) -> str:
    if isinstance(data, str):
        normalized = _normalize_payment_url(data)
        return normalized if _is_payment_url(normalized) or "checkstand" in normalized else ""
    candidate = _find_first_value(
        data,
        ("nextUrl", "next_url", "payUrl", "pay_url", "redirectUrl", "redirect_url", "url"),
    )
    normalized = _normalize_payment_url(str(candidate or ""))
    return normalized if _is_payment_url(normalized) or "checkstand" in normalized else ""


def _payment_query_values(url: str) -> tuple[str, str]:
    query = parse_qs(urlparse(url).query)
    return query.get("account_id", [""])[0], query.get("out_trade_no", [""])[0]


async def _wait_for_payment_checkout_ready(page: Any, timeout_ms: int = 8000) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        try:
            if not _is_payment_url(str(page.url or "")):
                return False
            if await _visible_locator_count(page.locator(".channel-item")):
                return True
            if await _visible_locator_count(page.get_by_role("button", name="确认支付", exact=True)):
                return True
            if await _visible_locator_count(page.locator("#qrcode-canvas")):
                return True
        except Exception as exc:
            if not _is_transient_page_error(exc):
                return False
        await page.wait_for_timeout(250)
    try:
        return _is_payment_url(str(page.url or ""))
    except Exception:
        return False


async def _wait_for_page_ready(page: Any, timeout: int = 12000) -> None:
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout)
    except Exception:
        pass
    last_url = ""
    stable_checks = 0
    for _ in range(8):
        try:
            current_url = page.url
        except Exception:
            current_url = ""
        if current_url and current_url == last_url:
            stable_checks += 1
            if stable_checks >= 2:
                return
        else:
            stable_checks = 0
            last_url = current_url
        await page.wait_for_timeout(150)


async def _click_text(page: Any, texts: list[str]) -> bool:
    for attempt in range(3):
        try:
            for text in texts:
                locator = page.get_by_text(text, exact=True)
                count = await locator.count()
                for index in range(count - 1, -1, -1):
                    candidate = locator.nth(index)
                    if await candidate.is_visible():
                        before_url = page.url
                        try:
                            await candidate.click()
                            return True
                        except Exception as exc:
                            if _is_transient_page_error(exc):
                                await _wait_for_page_ready(page)
                                if page.url != before_url:
                                    return True
                                break
                            continue
        except Exception as exc:
            if not _is_transient_page_error(exc):
                raise
        if attempt < 2:
            await _wait_for_page_ready(page)
    return False


async def _click_button(page: Any, texts: list[str]) -> bool:
    for attempt in range(3):
        try:
            for text in texts:
                locator = page.get_by_role("button", name=text, exact=True)
                count = await locator.count()
                for index in range(count - 1, -1, -1):
                    candidate = locator.nth(index)
                    if await candidate.is_visible() and await candidate.is_enabled():
                        before_url = page.url
                        try:
                            await candidate.click()
                            return True
                        except Exception as exc:
                            if _is_transient_page_error(exc):
                                await _wait_for_page_ready(page)
                                if page.url != before_url:
                                    return True
                                break
                            continue
        except Exception as exc:
            if not _is_transient_page_error(exc):
                raise
        if attempt < 2:
            await _wait_for_page_ready(page)
    return False


async def _fill_placeholder(page: Any, placeholders: list[str], value: str) -> bool:
    for attempt in range(3):
        try:
            for placeholder in placeholders:
                locator = page.get_by_placeholder(placeholder, exact=True)
                if await locator.count() == 1 and await locator.is_visible():
                    await locator.fill(value)
                    return True
        except Exception as exc:
            if not _is_transient_page_error(exc):
                raise
        if attempt < 2:
            await _wait_for_page_ready(page)
    return False


async def _check_login_agreement(page: Any) -> None:
    for attempt in range(3):
        try:
            checkboxes = page.locator('input[type="checkbox"]')
            count = await checkboxes.count()
            for index in range(count):
                if await checkboxes.nth(index).is_checked():
                    return
            agreement_text = page.get_by_text("我已阅读并同意", exact=True)
            if await agreement_text.count() == 1 and await agreement_text.is_visible():
                await agreement_text.click()
                for index in range(count):
                    if await checkboxes.nth(index).is_checked():
                        return
            for index in range(await checkboxes.count()):
                checkbox = checkboxes.nth(index)
                if not await checkbox.is_checked():
                    try:
                        await checkbox.check(force=True)
                    except Exception:
                        label = page.locator("label").filter(has=checkbox)
                        if await label.count() == 1 and await label.is_visible():
                            await label.click()
                    return
            return
        except Exception as exc:
            if not _is_transient_page_error(exc):
                return
        if attempt < 2:
            await _wait_for_page_ready(page)


async def _save_state(session: BrowserSession) -> str:
    try:
        state = await session.context.storage_state()
        return encrypt_text(json.dumps(state, ensure_ascii=False)) or ""
    except Exception:
        return ""


async def _request_json(session: BrowserSession, url: str) -> dict[str, Any]:
    await _wait_for_page_ready(session.page)
    request_headers = {
        "Accept": "application/json, text/plain, */*",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    for method in ("GET", "POST"):
        for use_page in (True, False):
            try:
                if use_page:
                    result = await session.page.evaluate(
                        """
                        async ({url, method, headers}) => {
                          const response = await fetch(url, {
                            method,
                            credentials: "include",
                            cache: "no-store",
                            headers,
                          });
                          return {status: response.status, text: await response.text()};
                        }
                        """,
                        {"url": url, "method": method, "headers": request_headers},
                    )
                    status = int(result.get("status", 0))
                    if status in {401, 403, 405}:
                        continue
                    data = json.loads(result.get("text") or "{}")
                else:
                    response = await session.context.request.fetch(
                        url,
                        method=method,
                        headers=request_headers,
                        timeout=30000,
                    )
                    if response.status in {401, 403, 405}:
                        continue
                    data = await response.json()
                if isinstance(data, dict) and data:
                    return data
            except Exception as exc:
                if use_page and _is_transient_page_error(exc):
                    await _wait_for_page_ready(session.page)
                continue
    return {}


def _cache_busted_url(url: str) -> str:
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}_t={int(time.time() * 1000)}"


async def _session_storage_value(page: Any, key: str) -> str:
    try:
        return str(await page.evaluate("(key) => window.sessionStorage.getItem(key) || ''", key) or "")
    except Exception:
        return ""


async def _context_cookie_value(session: BrowserSession, name: str, url: str = CONSOLE_ORIGIN) -> str:
    try:
        for cookie in await session.context.cookies(url):
            if cookie.get("name") == name:
                return str(cookie.get("value") or "")
    except Exception:
        return ""
    return ""


async def _console_json(
    session: BrowserSession,
    path: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    via_page: bool = False,
    region_type: str = "",
) -> dict[str, Any]:
    params = {key: value for key, value in (params or {}).items() if value not in (None, "")}
    ctyunid = str((headers or {}).get("regionCtyunId") or (headers or {}).get("platformId") or "")
    params.setdefault("ctyunid", ctyunid)
    params.setdefault("timestamp", int(time.time() * 1000))
    if not region_type and via_page:
        region_type = await _session_storage_value(session.page, "regionType")
    if region_type:
        params.setdefault("type", region_type)
    query = urlencode(params)
    url = f"{CONSOLE_ORIGIN}{path}"
    if query:
        url = f"{url}?{query}"
    csrf = await _context_cookie_value(session, "csrftoken")
    request_headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN",
        "X-Requested-With": "XMLHttpRequest",
        "regionCtyunId": ctyunid,
        "X-CSRFToken": csrf,
        **(headers or {}),
    }
    request_headers = {key: str(value) for key, value in request_headers.items() if value not in (None, "")}
    if via_page and str(session.page.url).startswith(CONSOLE_ORIGIN):
        result = await session.page.evaluate(
            """
            async ({url, headers}) => {
              const response = await fetch(url, {
                method: "GET",
                credentials: "include",
                headers,
              });
              return {status: response.status, text: await response.text()};
            }
            """,
            {"url": url, "headers": request_headers},
        )
        status = int(result.get("status", 0))
        text = str(result.get("text") or "")
    else:
        response = await session.context.request.fetch(
            url,
            method="GET",
            headers={
                **request_headers,
                "Origin": CONSOLE_ORIGIN,
                "Referer": CONSOLE_ECS_CREATE_URL,
            },
            timeout=30000,
        )
        status = int(response.status)
        text = await response.text()
    if status in {401, 403}:
        raise RuntimeError(f"official_console_unauthorized:{status}")
    try:
        data = json.loads(text or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"official_console_not_json:{text[:200]}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("official_console_bad_json")
    return data


async def _ensure_console_context(account: dict[str, Any], session: BrowserSession) -> tuple[bool, str]:
    page = session.page
    try:
        if not str(page.url).startswith(CONSOLE_ORIGIN) or _is_login_url(page.url):
            await page.goto(CONSOLE_ECS_CREATE_URL, wait_until="domcontentloaded", timeout=60000)
            await _wait_for_page_ready(page, timeout=20000)
            session.last_page_refresh = time.monotonic()
        if _is_login_url(page.url):
            status, message = await _complete_login(account, session)
            if status != "ready":
                return False, message
            await page.goto(CONSOLE_ECS_CREATE_URL, wait_until="domcontentloaded", timeout=60000)
            await _wait_for_page_ready(page, timeout=20000)
            session.last_page_refresh = time.monotonic()
        if _is_login_url(page.url):
            return False, "官方控制台仍要求登录，请在浏览器窗口手动处理"
        return True, ""
    except Exception as exc:
        return False, f"官方控制台授权初始化失败：{exc}"


def _find_region_platform_id(data: Any, region_id: str) -> str:
    if isinstance(data, dict):
        item_id = str(
            data.get("uuid")
            or data.get("regionID")
            or data.get("regionId")
            or data.get("regionid")
            or data.get("id")
            or ""
        )
        if item_id == str(region_id):
            details = data.get("details") if isinstance(data.get("details"), dict) else {}
            value = (
                details.get("ctyun_id")
                or details.get("ctyunId")
                or details.get("ctyunid")
                or data.get("ctyun_id")
                or data.get("ctyunId")
                or data.get("ctyunid")
            )
            if value:
                return str(value)
        for value in data.values():
            found = _find_region_platform_id(value, region_id)
            if found:
                return found
    elif isinstance(data, list):
        for value in data:
            found = _find_region_platform_id(value, region_id)
            if found:
                return found
    return ""


def _saved_storage_state(account: dict[str, Any]) -> dict[str, Any]:
    cookie_state = decrypt_text(account.get("cookie_state_enc"))
    if not cookie_state:
        return {}
    try:
        state = json.loads(cookie_state)
    except json.JSONDecodeError:
        return {}
    return state if isinstance(state, dict) else {}


def _storage_cookies(account: dict[str, Any], domain: str = "ctyun.cn") -> list[dict[str, Any]]:
    cookies = _saved_storage_state(account).get("cookies") or []
    if not isinstance(cookies, list):
        return []
    domain = domain.lower()
    result = []
    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        cookie_domain = str(cookie.get("domain") or "").lower()
        cookie_url = str(cookie.get("url") or "").lower()
        if domain in cookie_domain or domain in cookie_url:
            result.append(cookie)
    return result


def _storage_cookie_value(account: dict[str, Any], name: str) -> str:
    for cookie in _storage_cookies(account):
        if str(cookie.get("name") or "") == name:
            return str(cookie.get("value") or "")
    return ""


def _storage_cookie_header(account: dict[str, Any]) -> str:
    pairs: list[str] = []
    seen: set[str] = set()
    for cookie in _storage_cookies(account):
        name = str(cookie.get("name") or "")
        value = str(cookie.get("value") or "")
        if not name or name in seen:
            continue
        seen.add(name)
        pairs.append(f"{name}={value}")
    return "; ".join(pairs)


def _console_json_cookie_sync(
    account: dict[str, Any],
    path: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, Any] | None = None,
    *,
    referer: str = CONSOLE_ECS_CREATE_URL,
    region_type: str = "",
    timeout: int = 12,
) -> dict[str, Any]:
    cookie_header = _storage_cookie_header(account)
    if not cookie_header:
        raise RuntimeError("official_console_missing_saved_cookie")
    params = {key: value for key, value in (params or {}).items() if value not in (None, "")}
    ctyunid = str((headers or {}).get("regionCtyunId") or (headers or {}).get("platformId") or "")
    if ctyunid:
        params.setdefault("ctyunid", ctyunid)
    params.setdefault("timestamp", int(time.time() * 1000))
    if region_type:
        params.setdefault("type", region_type)
    query = urlencode(params)
    url = f"{CONSOLE_ORIGIN}{path}"
    if query:
        url = f"{url}?{query}"
    request_headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN",
        "Cookie": cookie_header,
        "Origin": CONSOLE_ORIGIN,
        "Referer": referer,
        "User-Agent": "Mozilla/5.0",
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRFToken": _storage_cookie_value(account, "csrftoken"),
        **(headers or {}),
    }
    request_headers = {key: str(value) for key, value in request_headers.items() if value not in (None, "")}
    request = Request(url, headers=request_headers, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200))
            text = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise RuntimeError(f"official_console_unauthorized:{exc.code}") from exc
        body = exc.read().decode("utf-8", errors="replace")[:200]
        raise RuntimeError(f"official_console_http_{exc.code}:{body}") from exc
    except URLError as exc:
        raise RuntimeError(f"official_console_network_error:{exc}") from exc
    if status in {401, 403}:
        raise RuntimeError(f"official_console_unauthorized:{status}")
    try:
        data = json.loads(text or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"official_console_not_json:{text[:200]}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("official_console_bad_json")
    return data


async def _console_json_cookie(
    account: dict[str, Any],
    path: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, Any] | None = None,
    *,
    referer: str = CONSOLE_ECS_CREATE_URL,
    region_type: str = "",
    timeout: int = 12,
) -> dict[str, Any]:
    return await asyncio.to_thread(
        _console_json_cookie_sync,
        account,
        path,
        params,
        headers,
        referer=referer,
        region_type=region_type,
        timeout=timeout,
    )


async def _console_region_platform_id_from_cookie(account: dict[str, Any], account_id: int, region_id: str) -> str:
    cache_key = (account_id, str(region_id))
    cached = _console_region_platform_cache.get(cache_key)
    if cached and cached[0] > time.monotonic():
        return cached[1]

    def remember(value: str) -> str:
        if value:
            _console_region_platform_cache[cache_key] = (time.monotonic() + 3600, value)
        return value

    if _storage_cookie_value(account, "regionid") == str(region_id):
        ctyunid = _storage_cookie_value(account, "ctyunid")
        if ctyunid:
            return remember(ctyunid)
    for path in (
        "/console/common/index/platform/list/create/",
        "/console/common/index/platform/list/",
    ):
        with suppress(Exception):
            data = await _console_json_cookie(account, path, {"regionid": "all"}, {}, referer=CONSOLE_HOME_URL)
            found = _find_region_platform_id(data, region_id)
            if found:
                return remember(found)
    return ""


async def _read_console_stock_with_cookie(account: dict[str, Any], account_id: int, region_id: str) -> dict[str, Any]:
    ctyunid = await _console_region_platform_id_from_cookie(account, account_id, region_id)
    if not ctyunid:
        raise RuntimeError(f"未从已保存网页登录态获取到 {region_id} 的 platformId/ctyunid")
    headers = {
        "regionid": region_id,
        "regionId": region_id,
        "isQueryAll": "true",
        "platformId": ctyunid,
        "regionCtyunId": ctyunid,
    }
    params = {"regionid": region_id}
    spec_data, flavor_data, storage_data = await asyncio.gather(
        _console_json_cookie(
            account,
            "/console/compute/ecm/ecs/serverextenddata/",
            params,
            headers,
        ),
        _console_json_cookie(
            account,
            "/console/compute/opsapi/report/remain_flavor_ecs_inner/",
            params,
            headers,
        ),
        _console_json_cookie(
            account,
            "/console/storage/opsapi/report/remain_region_inner/",
            {"regionid": region_id, "platformId": ctyunid},
            headers,
        ),
    )
    return {
        "status": "ready",
        "message": "",
        "specs": _console_items(spec_data, ("spec_list", "specList", "flavors")),
        "flavors": _console_items(flavor_data, ("spec_list", "specList", "flavor_list", "flavorList", "flavors")),
        "storage": _console_items(storage_data, ("resource_list", "resourceList", "storage_list", "storageList", "volumes")),
        "source": "console_cookie",
    }


async def _console_region_platform_id(session: BrowserSession, account_id: int, region_id: str) -> str:
    cache_key = (account_id, str(region_id))
    cached = _console_region_platform_cache.get(cache_key)
    if cached and cached[0] > time.monotonic():
        return cached[1]

    def remember(value: str) -> str:
        if value:
            _console_region_platform_cache[cache_key] = (time.monotonic() + 3600, value)
        return value

    page_region = await _session_storage_value(session.page, "regionid")
    page_ctyunid = await _session_storage_value(session.page, "ctyunid")
    if page_region == str(region_id) and page_ctyunid:
        return remember(page_ctyunid)
    cookie_region = await _context_cookie_value(session, "regionid")
    cookie_ctyunid = await _context_cookie_value(session, "ctyunid")
    if cookie_region == str(region_id) and cookie_ctyunid:
        return remember(cookie_ctyunid)
    for path in (
        "/console/common/index/platform/list/create/",
        "/console/common/index/platform/list/",
    ):
        try:
            data = await _console_json(session, path, {"regionid": "all"}, {}, via_page=False)
        except Exception:
            continue
        found = _find_region_platform_id(data, region_id)
        if found:
            return remember(found)
    return ""


async def _set_console_region(session: BrowserSession, region_id: str, platform_id: str, update_page: bool = False) -> None:
    cookies = [
        {"name": "regionid", "value": region_id, "url": CONSOLE_ORIGIN},
    ]
    if platform_id:
        cookies.append({"name": "ctyunid", "value": platform_id, "url": CONSOLE_ORIGIN})
    with suppress(Exception):
        await session.context.add_cookies(cookies)
    if update_page and str(session.page.url).startswith(CONSOLE_ORIGIN):
        with suppress(Exception):
            await session.page.evaluate(
                """
                ({regionid, ctyunid}) => {
                  window.sessionStorage.setItem("regionid", regionid || "");
                  if (ctyunid) window.sessionStorage.setItem("ctyunid", ctyunid);
                }
                """,
                {"regionid": region_id, "ctyunid": platform_id},
            )


def _console_items(data: dict[str, Any], preferred_keys: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    def find_preferred(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, dict):
            for key in preferred_keys:
                items = value.get(key)
                if isinstance(items, list):
                    return [item for item in items if isinstance(item, dict)]
            for item in value.values():
                found = find_preferred(item)
                if found:
                    return found
        elif isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        return []

    if preferred_keys:
        preferred = find_preferred(data)
        if preferred:
            return preferred
    obj = data.get("returnObj", data.get("data", data))
    if isinstance(obj, list):
        return [item for item in obj if isinstance(item, dict)]
    if isinstance(obj, dict):
        for key in ("spec_list", "specList", "resource_list", "resourceList", "returnObj", "results", "items", "list", "records", "data"):
            value = obj.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


async def get_console_resource_stock(account: dict[str, Any], region_id: str) -> dict[str, Any]:
    """Read official console stock status through the saved web login session."""
    account_id = int(account["id"])
    cache_key = (account_id, region_id)
    now = time.monotonic()
    cached = _console_stock_cache.get(cache_key)
    if cached and cached[0] > now:
        return cached[1]

    async with _account_operation_lock(account_id):
        cached = _console_stock_cache.get(cache_key)
        if cached and cached[0] > time.monotonic():
            return cached[1]

        session_result = await ensure_ctyun_session(account)
        if session_result.get("status") != "ready":
            result = {
                "status": session_result.get("status", "login_required"),
                "message": session_result.get("message", "official console session is not ready"),
                "specs": [],
                "flavors": [],
                "storage": [],
            }
            _console_stock_cache[cache_key] = (time.monotonic() + 30, result)
            return result

        session = _sessions[account_id]

        async def read_stock(via_page: bool = False) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
            ctyunid = await _console_region_platform_id(session, account_id, region_id)
            if not ctyunid:
                raise RuntimeError(f"未从官方控制台资源池列表获取到 {region_id} 的 platformId/ctyunid")
            await _set_console_region(session, region_id, ctyunid)
            headers = {
                "regionid": region_id,
                "regionId": region_id,
                "isQueryAll": "true",
                "platformId": ctyunid,
                "regionCtyunId": ctyunid,
            }
            params = {"regionid": region_id}
            specs, flavor, storage = await asyncio.gather(
                _console_json(
                    session,
                    "/console/compute/ecm/ecs/serverextenddata/",
                    params,
                    headers,
                    via_page=via_page,
                ),
                _console_json(
                    session,
                    "/console/compute/opsapi/report/remain_flavor_ecs_inner/",
                    params,
                    headers,
                    via_page=via_page,
                ),
                _console_json(
                    session,
                    "/console/storage/opsapi/report/remain_region_inner/",
                    {"regionid": region_id, "platformId": ctyunid},
                    headers,
                    via_page=via_page,
                ),
            )
            return specs, flavor, storage

        try:
            try:
                spec_data, flavor_data, storage_data = await read_stock(via_page=str(session.page.url).startswith(CONSOLE_ORIGIN))
            except Exception as exc:
                retryable = any(fragment in str(exc) for fragment in (
                    "official_console_unauthorized",
                    "official_console_not_json",
                    "未从官方控制台资源池列表获取到",
                ))
                if not retryable:
                    raise
                ready, message = await _ensure_console_context(account, session)
                if not ready:
                    raise RuntimeError(message) from exc
                spec_data, flavor_data, storage_data = await read_stock(via_page=True)
            result = {
                "status": "ready",
                "message": "",
                "specs": _console_items(spec_data, ("spec_list", "specList", "flavors")),
                "flavors": _console_items(flavor_data, ("spec_list", "specList", "flavor_list", "flavorList", "flavors")),
                "storage": _console_items(storage_data, ("resource_list", "resourceList", "storage_list", "storageList", "volumes")),
            }
            _console_stock_cache[cache_key] = (time.monotonic() + 600, result)
            return result
        except Exception as exc:
            message = str(exc)
            if "official_console_unauthorized" in message:
                message = "官方控制台接口仍返回 401，后台浏览器未能取得 console.ctyun.cn 授权；请重新刷新余额或手动完成网页登录后重试"
            elif "official_console_not_json" in message and ("auth/login" in message or "登录" in message):
                message = "官方控制台返回登录页面，网页登录态已失效；请重新刷新余额或手动完成登录后重试"
            result = {
                "status": "console_stock_error",
                "message": f"官方实时库存查询失败：{message}",
                "specs": [],
                "flavors": [],
                "storage": [],
            }
            _console_stock_cache[cache_key] = (time.monotonic() + 30, result)
            return result


async def get_console_resource_stock(account: dict[str, Any], region_id: str) -> dict[str, Any]:
    """Read official console stock status through the saved web login session."""
    account_id = int(account["id"])
    cache_key = (account_id, str(region_id))
    shared_key = str(region_id)
    now = time.monotonic()
    shared_cached = _console_shared_stock_cache.get(shared_key)
    if shared_cached and shared_cached[0] > now:
        return shared_cached[1]
    cached = _console_stock_cache.get(cache_key)
    if cached and cached[0] > now:
        return cached[1]

    async with _console_region_stock_lock(region_id):
        shared_cached = _console_shared_stock_cache.get(shared_key)
        if shared_cached and shared_cached[0] > time.monotonic():
            return shared_cached[1]
        cached = _console_stock_cache.get(cache_key)
        if cached and cached[0] > time.monotonic():
            return cached[1]

        cookie_error = ""
        try:
            result = await _read_console_stock_with_cookie(account, account_id, region_id)
            _console_stock_cache[cache_key] = (time.monotonic() + 86400, result)
            _console_shared_stock_cache[shared_key] = (time.monotonic() + 86400, result)
            return result
        except Exception as exc:
            cookie_error = str(exc)

        async with _account_operation_lock(account_id):
            session_result = await ensure_ctyun_session(account)
            if session_result.get("status") != "ready":
                result = {
                    "status": session_result.get("status", "login_required"),
                    "message": session_result.get("message", "official console session is not ready") or cookie_error,
                    "specs": [],
                    "flavors": [],
                    "storage": [],
                }
                _console_stock_cache[cache_key] = (time.monotonic() + 30, result)
                return result
            session = _sessions[account_id]

        async def read_stock(via_page: bool = False) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
            ctyunid = await _console_region_platform_id(session, account_id, region_id)
            if not ctyunid:
                raise RuntimeError(f"未从官方控制台资源池列表获取到 {region_id} 的 platformId/ctyunid")
            await _set_console_region(session, region_id, ctyunid, update_page=via_page)
            headers = {
                "regionid": region_id,
                "regionId": region_id,
                "isQueryAll": "true",
                "platformId": ctyunid,
                "regionCtyunId": ctyunid,
            }
            params = {"regionid": region_id}
            region_type = await _session_storage_value(session.page, "regionType") if via_page else ""
            specs, flavor, storage = await asyncio.gather(
                _console_json(
                    session,
                    "/console/compute/ecm/ecs/serverextenddata/",
                    params,
                    headers,
                    via_page=via_page,
                    region_type=region_type,
                ),
                _console_json(
                    session,
                    "/console/compute/opsapi/report/remain_flavor_ecs_inner/",
                    params,
                    headers,
                    via_page=via_page,
                    region_type=region_type,
                ),
                _console_json(
                    session,
                    "/console/storage/opsapi/report/remain_region_inner/",
                    {"regionid": region_id, "platformId": ctyunid},
                    headers,
                    via_page=via_page,
                    region_type=region_type,
                ),
            )
            return specs, flavor, storage

        try:
            try:
                spec_data, flavor_data, storage_data = await read_stock(via_page=False)
            except Exception as exc:
                retryable = any(fragment in str(exc) for fragment in (
                    "official_console_unauthorized",
                    "official_console_not_json",
                    "未从官方控制台资源池列表获取到",
                ))
                if not retryable:
                    raise
                async with _account_operation_lock(account_id):
                    ready, message = await _ensure_console_context(account, session)
                    if not ready:
                        raise RuntimeError(message) from exc
                    spec_data, flavor_data, storage_data = await read_stock(via_page=True)
            result = {
                "status": "ready",
                "message": "",
                "specs": _console_items(spec_data, ("spec_list", "specList", "flavors")),
                "flavors": _console_items(flavor_data, ("spec_list", "specList", "flavor_list", "flavorList", "flavors")),
                "storage": _console_items(storage_data, ("resource_list", "resourceList", "storage_list", "storageList", "volumes")),
            }
            _console_stock_cache[cache_key] = (time.monotonic() + 86400, result)
            _console_shared_stock_cache[shared_key] = (time.monotonic() + 86400, result)
            return result
        except Exception as exc:
            message = str(exc)
            if "official_console_unauthorized" in message:
                message = "官方控制台接口仍返回 401，后台浏览器未能取得 console.ctyun.cn 授权；请刷新余额或手动完成网页登录后重试"
            elif "official_console_not_json" in message and ("auth/login" in message or "登录" in message):
                message = "官方控制台返回登录页面，网页登录状态已失效；请刷新余额或手动完成登录后重试"
            result = {
                "status": "console_stock_error",
                "message": f"官方实时库存查询失败：{message}",
                "specs": [],
                "flavors": [],
                "storage": [],
            }
            _console_stock_cache[cache_key] = (time.monotonic() + 30, result)
            return result


async def get_console_eip_create_options(account: dict[str, Any], region_id: str) -> dict[str, Any]:
    """Best-effort read of the official EIP purchase form config for one resource pool."""
    account_id = int(account["id"])
    cache_key = (account_id, str(region_id))
    cached = _console_eip_options_cache.get(cache_key)
    if cached and cached[0] > time.monotonic():
        return cached[1]

    async def read_with_cookie() -> dict[str, Any]:
        ctyunid = await _console_region_platform_id_from_cookie(account, account_id, region_id)
        headers = {
            "regionid": region_id,
            "regionId": region_id,
            "regionID": region_id,
            "platformId": ctyunid,
            "regionCtyunId": ctyunid,
        }
        customer_id = str(account.get("provider_account_id") or "")
        sell, ipv4_count, floating_count, talk_order = await asyncio.gather(
            _console_json_cookie(
                account,
                "/console/sgt/api/sell/eipSell",
                {"regionId": region_id, "customerId": customer_id},
                headers,
                referer=CONSOLE_NETWORK_URL,
            ),
            _console_json_cookie(
                account,
                "/console/network/ctapi/v4/eip/get-ipv4-available-count/",
                {"regionID": region_id},
                headers,
                referer=CONSOLE_NETWORK_URL,
            ),
            _console_json_cookie(
                account,
                "/console/network/ctapi/v4/eip/get-floating-count/",
                {"regionID": region_id},
                headers,
                referer=CONSOLE_NETWORK_URL,
            ),
            _console_json_cookie(
                account,
                "/console/network/ctapi/v4/eip/get-eip-talkorder-info/",
                {"regionID": region_id},
                headers,
                referer=CONSOLE_NETWORK_URL,
            ),
            return_exceptions=True,
        )
        errors = [str(item) for item in (sell, ipv4_count, floating_count, talk_order) if isinstance(item, Exception)]
        if isinstance(sell, Exception) and len(errors) == 4:
            raise RuntimeError("；".join(errors[:2]))
        return {
            "status": "ready",
            "message": "；".join(errors[:2]),
            "sell": {} if isinstance(sell, Exception) else sell,
            "available_count": {} if isinstance(ipv4_count, Exception) else ipv4_count,
            "floating_count": {} if isinstance(floating_count, Exception) else floating_count,
            "talk_order": {} if isinstance(talk_order, Exception) else talk_order,
            "source": "console_cookie",
        }

    async def read_with_browser() -> dict[str, Any]:
        async with _account_operation_lock(account_id):
            session_result = await ensure_ctyun_session(account)
            if session_result.get("status") != "ready":
                raise RuntimeError(session_result.get("message", "official console session is not ready"))
            session = _sessions[account_id]
            ready, message = await _ensure_console_context(account, session)
            if not ready:
                raise RuntimeError(message)
            ctyunid = await _console_region_platform_id(session, account_id, region_id)
            await _set_console_region(session, region_id, ctyunid, update_page=True)
            region_type = await _session_storage_value(session.page, "regionType")
            headers = {
                "regionid": region_id,
                "regionId": region_id,
                "regionID": region_id,
                "platformId": ctyunid,
                "regionCtyunId": ctyunid,
            }
            customer_id = str(account.get("provider_account_id") or "")
            sell, ipv4_count, floating_count, talk_order = await asyncio.gather(
                _console_json(
                    session,
                    "/console/sgt/api/sell/eipSell",
                    {"regionId": region_id, "customerId": customer_id},
                    headers,
                    via_page=True,
                    region_type=region_type,
                ),
                _console_json(
                    session,
                    "/console/network/ctapi/v4/eip/get-ipv4-available-count/",
                    {"regionID": region_id},
                    headers,
                    via_page=True,
                    region_type=region_type,
                ),
                _console_json(
                    session,
                    "/console/network/ctapi/v4/eip/get-floating-count/",
                    {"regionID": region_id},
                    headers,
                    via_page=True,
                    region_type=region_type,
                ),
                _console_json(
                    session,
                    "/console/network/ctapi/v4/eip/get-eip-talkorder-info/",
                    {"regionID": region_id},
                    headers,
                    via_page=True,
                    region_type=region_type,
                ),
                return_exceptions=True,
            )
        errors = [str(item) for item in (sell, ipv4_count, floating_count, talk_order) if isinstance(item, Exception)]
        if isinstance(sell, Exception) and len(errors) == 4:
            raise RuntimeError("；".join(errors[:2]))
        return {
            "status": "ready",
            "message": "；".join(errors[:2]),
            "sell": {} if isinstance(sell, Exception) else sell,
            "available_count": {} if isinstance(ipv4_count, Exception) else ipv4_count,
            "floating_count": {} if isinstance(floating_count, Exception) else floating_count,
            "talk_order": {} if isinstance(talk_order, Exception) else talk_order,
            "source": "console_browser",
        }

    try:
        try:
            result = await read_with_cookie()
        except Exception:
            result = await read_with_browser()
        _console_eip_options_cache[cache_key] = (time.monotonic() + 600, result)
        return result
    except Exception as exc:
        result = {
            "status": "console_eip_options_error",
            "message": f"官方弹性IP销售配置读取失败：{exc}",
            "sell": {},
            "available_count": {},
            "floating_count": {},
            "talk_order": {},
        }
        _console_eip_options_cache[cache_key] = (time.monotonic() + 30, result)
        return result


async def _recharge_content_present(page: Any) -> bool:
    if _is_login_url(page.url):
        return False
    if _is_payment_url(page.url):
        return True
    if "/console/expense/fund/recharge" not in page.url:
        return False
    try:
        amount_input = page.get_by_placeholder("请输入充值金额", exact=True)
        if await amount_input.count() == 1 and await amount_input.is_visible():
            return True
        balance_panels = page.locator(".wrap-amount")
        if await balance_panels.count() > 0:
            return True
        recharge_button = page.get_by_role("button", name="立即充值", exact=True)
        if await recharge_button.count() == 1 and await recharge_button.is_visible():
            return True
        text = await _visible_text(page)
        if "账户余额" in text and "欠费金额" in text:
            return True
    except Exception as exc:
        if not _is_transient_page_error(exc):
            return False
    return False


async def _wait_for_recharge_content(page: Any, timeout_ms: int = 30000) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if await _recharge_content_present(page):
            return True
        if _is_login_url(page.url):
            return False
        await page.wait_for_timeout(500)
    return False


async def _has_finance_access(session: BrowserSession) -> bool:
    return await _recharge_content_present(session.page)


async def _complete_login(account: dict[str, Any], session: BrowserSession) -> tuple[str, str]:
    page = session.page
    username = decrypt_text(account.get("username_enc"))
    password = decrypt_text(account.get("password_enc"))
    secret = decrypt_text(account.get("totp_secret_enc"))
    if not username or not password:
        return "missing_credentials", "该账号没有保存网页登录账号或密码"

    await _wait_for_page_ready(page)
    text = await _visible_text(page)
    if any(word in text for word in ["滑块验证", "人机验证", "图形验证码"]):
        return "manual_required", "官方页面要求人机验证，请在浏览器窗口中手动完成"

    if await _click_text(page, ["账号登录"]):
        await _wait_for_page_ready(page)
    username_filled = await _fill_placeholder(page, ["登录名/邮箱", "请输入登录名", "请输入账号"], username)
    password_filled = await _fill_placeholder(page, ["请输入密码", "登录密码"], password)
    if username_filled and password_filled:
        await _check_login_agreement(page)
        if not await _click_button(page, ["登录"]):
            return "manual_required", "登录按钮无法自动识别，请在浏览器窗口中手动登录"
        await _wait_for_page_ready(page)

    for _ in range(24):
        if await _has_finance_access(session):
            return "ready", "天翼云登录状态正常"
        text = await _visible_text(page)
        if any(word in text for word in ["滑块验证", "人机验证", "图形验证码", "短信验证码"]):
            return "manual_required", "官方页面要求人工安全验证，请在浏览器窗口中手动完成"

        code = current_totp(secret)
        if code:
            remaining = 30 - (int(time.time()) % 30)
            if remaining <= 4:
                await page.wait_for_timeout((remaining + 1) * 1000)
                code = current_totp(secret)
            filled = await _fill_placeholder(
                page,
                [
                    "请输入6位动态验证码",
                    "请输入动态验证码",
                    "请输入谷歌验证码",
                    "请输入Google验证码",
                    "请输入MFA验证码",
                ],
                code,
            )
            if not filled:
                try:
                    candidates = page.locator(
                        'input[maxlength="6"], input[placeholder*="动态"], input[placeholder*="Google"], input[placeholder*="谷歌"]'
                    )
                    if await candidates.count() == 1 and await candidates.is_visible():
                        await candidates.fill(code)
                        filled = True
                except Exception as exc:
                    if _is_transient_page_error(exc):
                        await _wait_for_page_ready(page)
            if filled:
                await _click_button(page, ["登录", "确认", "验证", "下一步"])
                await _wait_for_page_ready(page)
                text = await _visible_text(page)
                if any(word in text for word in ["动态验证码错误", "验证码不正确", "验证码已失效"]):
                    return "totp_failed", "MFA 动态验证码未通过，请检查保存的 TOTP 密钥和服务器 NTP 时间"
                continue
        if any(word in text for word in ["账号或密码错误", "密码错误", "账号不存在", "登录失败"]):
            return "login_failed", "天翼云拒绝了账号密码，请检查平台内保存的登录资料"
        await page.wait_for_timeout(500)

    if await _wait_for_recharge_content(page, timeout_ms=5000):
        return "ready", "天翼云登录状态正常"
    text = await _visible_text(page)
    if "您已经开启MFA验证" in text or "请输入6位动态验证码" in text:
        return "totp_failed", "仍停留在 MFA 验证页面，请检查保存的 TOTP 密钥和服务器 NTP 时间"
    if _is_login_url(page.url):
        return "login_failed", "仍停留在天翼云登录页，请检查保存的登录账号和密码"
    return "manual_required", f"自动登录未完成，请在浏览器窗口中按官方页面提示手动处理；{await _page_diagnostic(page)}"


async def ensure_ctyun_session(account: dict[str, Any], open_recharge: bool = False) -> dict[str, Any]:
    session = await _get_session(account)
    page = session.page
    target = RECHARGE_URL
    try:
        now = time.monotonic()
        if open_recharge:
            session.interactive_until = now + 1800
        interactive_active = now < session.interactive_until and _is_payment_url(page.url)
        recharge_ready = await _recharge_content_present(page)
        authenticated = interactive_active or recharge_ready
        refresh_due = now - session.last_page_refresh >= settings.browser_page_refresh_seconds
        can_background_refresh = now >= session.interactive_until
        should_navigate = not authenticated
        if open_recharge and interactive_active:
            should_navigate = True
        elif recharge_ready:
            should_navigate = not open_recharge and refresh_due and can_background_refresh
        if should_navigate:
            await page.goto(target, wait_until="domcontentloaded", timeout=45000)
            await _wait_for_page_ready(page)
            session.last_page_refresh = now
        if not authenticated and not _is_login_url(page.url):
            authenticated = await _wait_for_recharge_content(page, timeout_ms=10000)
        status = "ready" if authenticated else ""
        message = "天翼云登录状态正常" if status else ""
        if not status:
            status, message = await _complete_login(account, session)
            if status == "ready" and "/console/expense/fund/recharge" not in page.url:
                await page.goto(target, wait_until="domcontentloaded", timeout=45000)
                await _wait_for_page_ready(page)
                if not await _wait_for_recharge_content(page, timeout_ms=20000):
                    status = "recharge_page_error"
                    message = f"登录成功，但官方充值页未加载完成；{await _page_diagnostic(page)}"
        await page.bring_to_front()
        return {
            "status": status,
            "message": message,
            "url": page.url,
            "viewer_url": _viewer_url(),
            "cookie_state_enc": await _save_state(session),
        }
    except Exception as exc:
        return {
            "status": "browser_error",
            "message": f"浏览器会话失败：{exc}",
            "url": page.url,
            "viewer_url": _viewer_url(),
            "cookie_state_enc": await _save_state(session),
        }


def _find_value(data: Any, key: str) -> Any:
    if isinstance(data, dict):
        if key in data:
            return data[key]
        for value in data.values():
            found = _find_value(value, key)
            if found is not None:
                return found
    elif isinstance(data, list):
        for value in data:
            found = _find_value(value, key)
            if found is not None:
                return found
    return None


def _find_first_value(data: Any, keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = _find_value(data, key)
        if value is not None:
            return value
    return None


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").replace("¥", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


async def _read_finance_from_page(page: Any) -> tuple[float | None, float | None]:
    for _ in range(20):
        try:
            panels = page.locator(".wrap-amount")
            values: dict[str, float] = {}
            for index in range(await panels.count()):
                text = await panels.nth(index).inner_text(timeout=3000)
                match = re.search(r"([+-]?\d[\d,]*(?:\.\d+)?)", text)
                if not match:
                    continue
                amount = _to_float(match.group(1))
                if amount is None:
                    continue
                if "账户余额" in text:
                    values["available"] = amount
                elif "欠费金额" in text:
                    values["owe"] = amount
            if "available" in values:
                return values["available"], values.get("owe")
        except Exception as exc:
            if _is_transient_page_error(exc):
                await _wait_for_page_ready(page)
        await page.wait_for_timeout(250)

    text = await _visible_text(page)
    balance_match = re.search(
        r"账户余额\s*[\(（]?元[\)）]?\s*([+-]?\d[\d,]*(?:\.\d+)?)",
        text,
    )
    owe_match = re.search(
        r"欠费金额\s*[\(（]?元[\)）]?\s*([+-]?\d[\d,]*(?:\.\d+)?)",
        text,
    )
    return (
        _to_float(balance_match.group(1)) if balance_match else None,
        _to_float(owe_match.group(1)) if owe_match else None,
    )


async def _read_finance_from_api(session: BrowserSession, session_result: dict[str, Any]) -> dict[str, Any]:
    balance_data = await _request_json(session, _cache_busted_url(BALANCE_URL))
    owe_data = await _request_json(session, _cache_busted_url(OWE_URL))
    account_data = await _request_json(session, _cache_busted_url(ACCOUNT_INFO_URL))
    available = _find_first_value(
        balance_data,
        ("cashPoints", "availableBalance", "availableAmount", "cashBalance", "availableCash"),
    )
    owe = _find_first_value(owe_data, ("realOwe", "oweAmount", "arrears", "outstandingAmount"))
    provider_account_id = _find_first_value(
        balance_data,
        ("accountId", "accountID", "account_id"),
    ) or _find_first_value(
        account_data,
        ("accountId", "accountID", "account_id", "tenantId", "tenantID"),
    ) or ""
    available_amount = _to_float(available)
    result = {
        **session_result,
        "available": available_amount,
        "owe": _to_float(owe),
        "provider_account_id": str(provider_account_id),
        "message": "余额已从天翼云官方接口读取",
    }
    if available_amount is None:
        result["status"] = "finance_error"
        result["message"] = f"官方余额接口没有返回可识别余额；{await _page_diagnostic(session.page)}"
    return result


async def _get_finance_unlocked(account: dict[str, Any], force_api: bool = False) -> dict[str, Any]:
    session_result = await ensure_ctyun_session(account)
    if session_result["status"] != "ready":
        return {
            **session_result,
            "available": None,
            "owe": None,
            "provider_account_id": "",
        }

    session = _sessions[int(account["id"])]
    try:
        if _is_payment_url(session.page.url) and not force_api:
            return {
                **session_result,
                "status": "interactive",
                "message": "官方支付页面正在使用，暂缓刷新余额",
                "available": None,
                "owe": None,
                "provider_account_id": "",
            }

        api_result = await _read_finance_from_api(session, session_result)
        if api_result.get("status") == "ready" and api_result.get("available") is not None:
            return api_result
        if force_api:
            return api_result

        page_available, page_owe = await _read_finance_from_page(session.page)
        if page_available is not None:
            return {
                **session_result,
                "available": page_available,
                "owe": page_owe,
                "provider_account_id": api_result.get("provider_account_id", ""),
                "message": "余额接口暂未返回，已从天翼云官方页面读取",
            }
        return api_result
    except Exception as exc:
        return {
            **session_result,
            "status": "finance_error",
            "message": f"已登录，但读取余额失败：{exc}",
            "available": None,
            "owe": None,
            "provider_account_id": "",
        }


async def get_finance(account: dict[str, Any], force_api: bool = False) -> dict[str, Any]:
    async with _account_operation_lock(int(account["id"])):
        return await _get_finance_unlocked(account, force_api=force_api)


async def login_and_open_recharge(account: dict[str, Any]) -> dict[str, Any]:
    async with _account_operation_lock(int(account["id"])):
        return await ensure_ctyun_session(account, open_recharge=True)


async def login_and_open_console(account: dict[str, Any]) -> dict[str, Any]:
    async with _account_operation_lock(int(account["id"])):
        session_result = await ensure_ctyun_session(account)
        session = await _get_session(account)
        page = session.page
        if session_result.get("status") != "ready":
            return {
                **session_result,
                "viewer_url": _viewer_url(),
                "cookie_state_enc": session_result.get("cookie_state_enc") or await _save_state(session),
            }
        try:
            if not str(page.url).startswith(CONSOLE_ORIGIN) or _is_login_url(page.url):
                await page.goto(CONSOLE_HOME_URL, wait_until="domcontentloaded", timeout=60000)
                await _wait_for_page_ready(page, timeout=20000)
                session.last_page_refresh = time.monotonic()
            if _is_login_url(page.url):
                status, message = await _complete_login(account, session)
                if status != "ready":
                    return {
                        "status": status,
                        "message": message,
                        "url": page.url,
                        "viewer_url": _viewer_url(),
                        "cookie_state_enc": await _save_state(session),
                    }
                await page.goto(CONSOLE_HOME_URL, wait_until="domcontentloaded", timeout=60000)
                await _wait_for_page_ready(page, timeout=20000)
            await page.bring_to_front()
            return {
                "status": "ready",
                "message": "官方控制台已打开",
                "url": page.url,
                "viewer_url": _viewer_url(),
                "cookie_state_enc": await _save_state(session),
            }
        except Exception as exc:
            return {
                "status": "browser_error",
                "message": f"打开官方控制台失败：{exc}",
                "url": page.url,
                "viewer_url": _viewer_url(),
                "cookie_state_enc": await _save_state(session),
            }


async def _ensure_recharge_agreement(page: Any) -> bool:
    checkbox = page.locator("input.ctda-checkbox__input")
    if await checkbox.count() != 1:
        checkbox = page.get_by_role(
            "checkbox",
            name="我已了解，我正在对天翼云官网账户进行充值，充值款项只可用于天翼云消费，账户余额提现时默认原路返还至原始充值账户，如无法返还至原充值账户时仅可以转至与天翼云账户实名认证信息同名的银行卡。",
            exact=True,
        )
    if await checkbox.count() != 1:
        return False
    if await checkbox.is_checked():
        return True

    label = page.locator("label.ctda-checkbox").filter(has_text="我已了解")
    if await label.count() == 1 and await label.is_visible():
        await label.click()
    else:
        indicator = page.locator(".ctda-checkbox__indicator")
        if await indicator.count() != 1 or not await indicator.is_visible():
            return False
        await indicator.click()

    for _ in range(10):
        if await checkbox.is_checked():
            return True
        await page.wait_for_timeout(100)
    return False


async def _wait_for_payment_page(session: BrowserSession, timeout_ms: int = 45000) -> Any | None:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        for candidate in reversed(list(session.context.pages)):
            try:
                if _is_payment_url(candidate.url):
                    await _wait_for_page_ready(candidate)
                    if await _wait_for_payment_checkout_ready(candidate, timeout_ms=6000):
                        session.page = candidate
                        return candidate
            except Exception:
                continue
        await session.page.wait_for_timeout(250)
    return None


async def _context_page_urls(session: BrowserSession) -> str:
    urls: list[str] = []
    for candidate in session.context.pages:
        try:
            urls.append(candidate.url)
        except Exception:
            continue
    return " | ".join(urls) or "-"


async def _create_recharge_order_fast_unlocked(
    account: dict[str, Any],
    session: BrowserSession,
    amount: str,
    session_result: dict[str, Any],
) -> dict[str, Any]:
    cents = _amount_to_cents(amount)
    page = session.page
    recharge_page = page
    if not _is_recharge_url(str(page.url)):
        await page.goto(RECHARGE_URL, wait_until="domcontentloaded", timeout=45000)
        await _wait_for_page_ready(page)
    elif not await _recharge_content_present(page):
        await _wait_for_page_ready(page, timeout=5000)

    result = await page.evaluate(
        """
        async ({url, amount, frontUrl, platform}) => {
          const query = new URLSearchParams({ amount, frontUrl, platform });
          const response = await fetch(`${url}?${query.toString()}`, {
            method: "GET",
            credentials: "include",
            headers: {
              Accept: "application/json, text/plain, */*",
              "X-Requested-With": "XMLHttpRequest"
            }
          });
          const text = await response.text();
          let data = null;
          try { data = JSON.parse(text || "{}"); } catch {}
          return { ok: response.ok, status: response.status, text, data };
        }
        """,
        {
            "url": RECHARGE_CREATE_URL,
            "amount": cents,
            "frontUrl": RECHARGE_FRONT_URL,
            "platform": "1",
        },
    )
    status = int(result.get("status") or 0)
    data = result.get("data")
    if not isinstance(data, dict):
        try:
            data = json.loads(result.get("text") or "{}")
        except json.JSONDecodeError:
            data = {}
    if status in {401, 403}:
        raise RuntimeError(f"official_fast_unauthorized:{status}")
    status_code = str(_find_first_value(data, ("statusCode", "code", "resultCode", "Result")) or "")
    payment_url = _extract_payment_url(data)
    if not payment_url:
        message = _find_first_value(data, ("message", "msg", "error", "reason")) or str(result.get("text") or "")[:160]
        raise RuntimeError(f"official_fast_no_next_url:{status_code or status}:{message}")

    payment_page = await session.context.new_page()
    try:
        await payment_page.goto(payment_url, wait_until="domcontentloaded", timeout=45000)
        await _wait_for_page_ready(payment_page)
        if not await _wait_for_payment_checkout_ready(payment_page, timeout_ms=9000):
            final_url = str(payment_page.url or "")
            with suppress(Exception):
                await payment_page.close(run_before_unload=False)
            session.page = recharge_page
            await recharge_page.bring_to_front()
            raise RuntimeError(f"official_fast_checkout_not_ready:{final_url or '-'}")
        await payment_page.bring_to_front()
    except Exception:
        if not payment_page.is_closed():
            with suppress(Exception):
                await payment_page.close(run_before_unload=False)
        session.page = recharge_page
        with suppress(Exception):
            await recharge_page.bring_to_front()
        raise
    session.page = payment_page
    provider_account_id, order_no = _payment_query_values(payment_page.url)
    session.last_payment = {
        "account_id": provider_account_id,
        "provider_account_id": provider_account_id,
        "out_trade_no": order_no,
        "order_no": order_no,
        "amount": amount,
        "url": payment_page.url,
        "fast_path": True,
    }
    return {
        **session_result,
        "status": "ready",
        "message": "官方快速下单成功，收银台已打开",
        "url": payment_page.url,
        "order_no": order_no,
        "provider_account_id": provider_account_id,
        "viewer_url": _viewer_url(),
        "fast_path": True,
        "cookie_state_enc": await _save_state(session),
    }


async def _create_recharge_order_unlocked(account: dict[str, Any], amount: str) -> dict[str, Any]:
    session_result = await ensure_ctyun_session(account, open_recharge=True)
    if session_result["status"] != "ready":
        return session_result

    session = _sessions[int(account["id"])]
    page = session.page
    fast_path_error = ""
    try:
        if "/console/expense/fund/recharge" not in page.url:
            await page.goto(RECHARGE_URL, wait_until="domcontentloaded", timeout=45000)
        await _wait_for_page_ready(page)
        if not await _wait_for_recharge_content(page, timeout_ms=30000):
            return {
                **session_result,
                "status": "recharge_page_error",
                "message": f"天翼云官方充值页未加载完成；{await _page_diagnostic(page)}",
            }

        if settings.recharge_fast_order_enabled and time.monotonic() >= session.fast_recharge_disabled_until:
            try:
                return await _create_recharge_order_fast_unlocked(account, session, amount, session_result)
            except Exception as exc:
                fast_path_error = str(exc)
                session.fast_recharge_disabled_until = time.monotonic() + 600
                page = session.page
                if not _is_recharge_url(str(page.url)):
                    recharge_pages = []
                    for candidate in session.context.pages:
                        try:
                            if _is_recharge_url(str(candidate.url or "")) and not candidate.is_closed():
                                recharge_pages.append(candidate)
                        except Exception:
                            continue
                    if recharge_pages:
                        page = recharge_pages[-1]
                        session.page = page
                        await page.bring_to_front()
                    else:
                        page = await session.context.new_page()
                        session.page = page
                        await page.goto(RECHARGE_URL, wait_until="domcontentloaded", timeout=45000)
                await _wait_for_page_ready(page)
                if not await _wait_for_recharge_content(page, timeout_ms=20000):
                    return {
                        **session_result,
                        "status": "recharge_page_error",
                        "message": f"快速下单回退后充值页未加载完成；fast={fast_path_error}; {await _page_diagnostic(page)}",
                    }

        amount_filled = await _fill_placeholder(page, ["请输入充值金额"], amount)
        if not amount_filled:
            return {
                **session_result,
                "status": "recharge_form_error",
                "message": f"未找到天翼云官方充值金额输入框；fast={fast_path_error or '-'}；{await _page_diagnostic(page)}",
            }

        if not await _ensure_recharge_agreement(page):
            return {
                **session_result,
                "status": "recharge_form_error",
                "message": f"天翼云官方充值协议未能勾选；{await _page_diagnostic(page)}",
            }

        if not await _click_button(page, ["立即充值"]):
            return {
                **session_result,
                "status": "recharge_form_error",
                "message": "未找到天翼云官方“立即充值”按钮",
            }

        confirm_button = page.get_by_role("button", name="确定", exact=True)
        try:
            await confirm_button.wait_for(state="visible", timeout=10000)
        except Exception:
            pass
        confirmation_clicked = False
        for index in range(await confirm_button.count() - 1, -1, -1):
            candidate = confirm_button.nth(index)
            if await candidate.is_visible() and await candidate.is_enabled():
                await candidate.click()
                confirmation_clicked = True
                break
        if not confirmation_clicked:
            return {
                **session_result,
                "status": "recharge_confirm_error",
                "message": f"未找到天翼云充值确认按钮；{await _page_diagnostic(page)}",
                "url": page.url,
                "cookie_state_enc": await _save_state(session),
            }

        payment_page = await _wait_for_payment_page(session)
        if payment_page is None:
            text = await _visible_text(page)
            return {
                **session_result,
                "status": "recharge_order_failed",
                "message": (
                    f"确认充值后未发现官方收银台页签；pages={await _context_page_urls(session)}；"
                    f"text={text[-300:] or '-'}"
                ),
                "url": page.url,
                "cookie_state_enc": await _save_state(session),
            }
        page = payment_page

        provider_account_id, order_no = _payment_query_values(page.url)
        session.last_payment = {
            "account_id": provider_account_id,
            "provider_account_id": provider_account_id,
            "out_trade_no": order_no,
            "order_no": order_no,
            "amount": amount,
            "url": page.url,
        }
        await page.bring_to_front()
        return {
            **session_result,
            "status": "ready",
            "message": f"充值订单已创建：¥{amount}",
            "url": page.url,
            "viewer_url": _viewer_url(),
            "provider_account_id": provider_account_id,
            "order_no": order_no,
            "amount": amount,
            "cookie_state_enc": await _save_state(session),
        }
    except Exception as exc:
        return {
            **session_result,
            "status": "recharge_order_failed",
            "message": f"创建充值订单失败：{exc}",
            "url": page.url,
            "viewer_url": _viewer_url(),
            "cookie_state_enc": await _save_state(session),
        }


async def create_recharge_order(account: dict[str, Any], amount: str) -> dict[str, Any]:
    async with _account_operation_lock(int(account["id"])):
        return await _create_recharge_order_unlocked(account, amount)


async def _find_payment_page_for_session(session: BrowserSession, timeout_ms: int = 4000) -> Any | None:
    payment = session.last_payment or {}
    preferred_url = str(payment.get("url") or "")
    preferred_order_no = str(payment.get("out_trade_no") or payment.get("order_no") or "")
    fallback = None
    candidates = list(reversed(list(session.context.pages)))
    for candidate in candidates:
        try:
            url = str(candidate.url or "")
        except Exception:
            continue
        if not _is_payment_url(url):
            continue
        if preferred_url and url == preferred_url:
            if await _wait_for_payment_checkout_ready(candidate, timeout_ms=timeout_ms):
                return candidate
            continue
        if preferred_order_no and preferred_order_no in url:
            if await _wait_for_payment_checkout_ready(candidate, timeout_ms=timeout_ms):
                return candidate
            continue
        if fallback is None:
            fallback = candidate
    if fallback and await _wait_for_payment_checkout_ready(fallback, timeout_ms=timeout_ms):
        return fallback
    return None


async def activate_payment_method(account: dict[str, Any], payment_method: str) -> dict[str, Any]:
    method = str(payment_method or "").lower()
    label = PAYMENT_METHODS.get(method)
    if not label:
        return {
            "status": "payment_method_error",
            "message": "不支持的支付方式",
            "payment_method": method,
        }

    async with _account_operation_lock(int(account["id"])):
        session = _sessions.get(int(account["id"]))
        if not session:
            return {
                "status": "payment_session_error",
                "message": "当前账号没有可用的官方收银台会话",
                "payment_method": method,
            }

        page = await _find_payment_page_for_session(session, timeout_ms=4000)
        if page is None:
            payment = session.last_payment or {}
            amount = str(payment.get("amount") or "").strip()
            recovery_message = ""
            if amount:
                session.fast_recharge_disabled_until = time.monotonic() + 600
                recovery = await _create_recharge_order_unlocked(account, amount)
                recovery_message = str(recovery.get("message") or "")
                if recovery.get("status") == "ready":
                    page = await _find_payment_page_for_session(session, timeout_ms=8000)
            if page is not None:
                session.page = page
            else:
                detail = f"；已尝试恢复：{recovery_message}" if recovery_message else ""
                return {
                    "status": "payment_session_error",
                    "message": f"没有找到官方收银台；pages={await _context_page_urls(session)}{detail}",
                    "payment_method": method,
                    "cookie_state_enc": await _save_state(session),
                }
        if page is None:
            return {
                "status": "payment_session_error",
                "message": f"没有找到官方收银台；pages={await _context_page_urls(session)}",
                "payment_method": method,
            }

        try:
            session.page = page
            await page.bring_to_front()
            _clear_payment_qr_cache(session)
            close_button = page.get_by_role("button", name="Close", exact=True)
            if await close_button.count() == 1 and await close_button.is_visible():
                await close_button.click()
                cancel_dialog = page.get_by_role("dialog", name="提示", exact=True)
                try:
                    await cancel_dialog.wait_for(state="visible", timeout=3000)
                except Exception:
                    pass
                if await cancel_dialog.count() == 1 and await cancel_dialog.is_visible():
                    cancel_confirm = cancel_dialog.get_by_role("button", name="确定", exact=True)
                    if await cancel_confirm.count() == 1 and await cancel_confirm.is_visible():
                        await cancel_confirm.click()
                        await page.wait_for_timeout(300)

            option = page.locator(".channel-item").filter(has_text=label)
            for _ in range(40):
                if await option.count() == 1 and await option.is_visible():
                    break
                await page.wait_for_timeout(250)
            if await option.count() != 1 or not await option.is_visible():
                return {
                    "status": "payment_method_error",
                    "message": f"官方收银台没有加载“{label}”",
                    "payment_method": method,
                    "cookie_state_enc": await _save_state(session),
                }
            await option.click()

            confirm = page.get_by_role("button", name="确认支付", exact=True)
            if await confirm.count() != 1 or not await confirm.is_enabled():
                return {
                    "status": "payment_confirm_error",
                    "message": "官方“确认支付”按钮不可用",
                    "payment_method": method,
                    "cookie_state_enc": await _save_state(session),
                }

            known_pages = set(session.context.pages)
            await confirm.click()
            qr_canvas = page.locator("#qrcode-canvas")
            for _ in range(40):
                new_pages = [candidate for candidate in session.context.pages if candidate not in known_pages]
                if new_pages:
                    session.page = new_pages[-1]
                    await _wait_for_page_ready(session.page)
                    await session.page.bring_to_front()
                    break
                if await qr_canvas.count() == 1 and await qr_canvas.is_visible():
                    break
                dialog = page.locator('[role="dialog"]').filter(has_text=label)
                if await dialog.count() == 1 and await dialog.is_visible():
                    break
                await page.wait_for_timeout(250)

            qr_canvas = session.page.locator("#qrcode-canvas")
            qr_available = await qr_canvas.count() == 1 and await qr_canvas.is_visible()
            state = await _wait_for_payment_qr_state(session.page, timeout_ms=10000)
            if not state:
                state = await _remember_payment_state(session, session.page, method)
            else:
                await _remember_payment_state(session, session.page, method)
            qr_available = qr_available and (_payment_qr_state_ready(state) or bool(state.get("qrCode")))
            qr_cached = False
            if qr_available:
                qr_cached = await _cache_current_payment_qr_png(session, session.page, method, state, timeout_ms=3500)
            return {
                "status": "ready",
                "message": f"{label}收款信息已加载",
                "payment_method": method,
                "qr_available": qr_available,
                "qr_cached": qr_cached,
                "url": session.page.url,
                "viewer_url": _viewer_url(),
                "cookie_state_enc": await _save_state(session),
            }
        except Exception as exc:
            return {
                "status": "payment_method_error",
                "message": f"加载{label}失败：{exc}",
                "payment_method": method,
                "url": page.url,
                "viewer_url": _viewer_url(),
                "cookie_state_enc": await _save_state(session),
            }


async def get_payment_qr(account: dict[str, Any]) -> dict[str, Any]:
    async with _account_operation_lock(int(account["id"])):
        session = _sessions.get(int(account["id"]))
        if not session:
            return {"status": "qr_unavailable", "message": "当前账号没有支付会话"}

        page = None
        for candidate in session.context.pages:
            try:
                canvas = candidate.locator("#qrcode-canvas")
                if await canvas.count() == 1:
                    page = candidate
                    break
            except Exception:
                continue
        if page is None:
            return {"status": "qr_unavailable", "message": "当前支付方式没有生成二维码"}

        try:
            canvas = page.locator("#qrcode-canvas")
            state = await _read_payment_page_state(page)
            cached_png = _cached_payment_qr_png(session, state)
            if cached_png and not await _payment_qr_busy(page, canvas):
                return {"status": "ready", "message": "收款码已从缓存提取", "png": cached_png}
            png = await _wait_for_payment_qr_png(page, canvas, timeout_ms=8000)
            if png:
                state = await _remember_payment_state(session, page)
                method = str((session.last_payment or {}).get("payment_method") or "")
                _store_payment_qr_png(session, page, png, method, state)
                return {"status": "ready", "message": "收款码已提取", "png": png}
            return {"status": "qr_unavailable", "message": "二维码仍在加载或已过期"}
        except Exception as exc:
            return {"status": "qr_unavailable", "message": f"提取二维码失败：{exc}"}


async def _visible_locator_count(locator: Any) -> int:
    try:
        count = await locator.count()
    except Exception:
        return 0
    visible = 0
    for index in range(count):
        try:
            if await locator.nth(index).is_visible():
                visible += 1
        except Exception:
            continue
    return visible


async def _payment_qr_busy(page: Any, canvas: Any | None = None) -> bool:
    selectors = (
        ".refresh-mask",
        ".ant-spin",
        ".ant-spin-blur",
        ".ant-spin-spinning",
        ".ant-spin-dot",
        ".el-loading-mask",
        ".loading",
        ".loading-mask",
        ".loader",
        ".qrcode-loading",
        ".qr-loading",
        ".van-loading",
    )
    for selector in selectors:
        if await _visible_locator_count(page.locator(selector)):
            return True
    if canvas is not None:
        try:
            return bool(await canvas.evaluate(
                """(el) => {
                    const rect = el.getBoundingClientRect();
                    const visible = (node) => {
                        if (!node || node === document.documentElement || node === document.body) return true;
                        const style = getComputedStyle(node);
                        return style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity || "1") > 0.01;
                    };
                    for (let node = el; node; node = node.parentElement) {
                        const style = getComputedStyle(node);
                        const className = String(node.className || "").toLowerCase();
                        if (style.filter && style.filter !== "none" && style.filter.includes("blur")) return true;
                        if (className.includes("spin-blur") || className.includes("loading")) return true;
                    }
                    const overlapRatio = (candidate) => {
                        if (!visible(candidate)) return 0;
                        const box = candidate.getBoundingClientRect();
                        const width = Math.max(0, Math.min(rect.right, box.right) - Math.max(rect.left, box.left));
                        const height = Math.max(0, Math.min(rect.bottom, box.bottom) - Math.max(rect.top, box.top));
                        const area = Math.max(1, rect.width * rect.height);
                        return (width * height) / area;
                    };
                    const candidates = document.querySelectorAll(
                        '[class*="spin"],[class*="Spin"],[class*="loading"],[class*="Loading"],[class*="mask"],[class*="Mask"],[class*="refresh"],[class*="Refresh"]'
                    );
                    for (const candidate of candidates) {
                        if (candidate === el) continue;
                        const className = String(candidate.className || "").toLowerCase();
                        if (!/(spin-blur|spinning|loading|mask|refresh)/.test(className)) continue;
                        if (overlapRatio(candidate) > 0.08) return true;
                    }
                    return false;
                }"""
            ))
        except Exception as exc:
            if not _is_transient_page_error(exc):
                raise
    return False


def _payment_marker(state: dict[str, Any] | None) -> str:
    if not state:
        return ""
    return "|".join(
        str(value)
        for value in (state.get("outTradeNo"), state.get("qrCode"))
        if value not in (None, "")
    )


def _payment_qr_state_ready(state: dict[str, Any] | None) -> bool:
    if not state:
        return False
    try:
        time_out = int(float(state.get("timeOut") or 0))
    except (TypeError, ValueError):
        time_out = 0
    return bool(state.get("qrCode")) and time_out > 0 and not bool(state.get("subLoading"))


def _payment_state_expired(state: dict[str, Any] | None) -> bool:
    if not state:
        return False
    try:
        time_out = int(float(state.get("timeOut") or 0))
    except (TypeError, ValueError):
        return False
    return time_out <= 0 and bool(state.get("qrCode") or state.get("outTradeNo"))


def _clear_payment_qr_cache(session: BrowserSession) -> None:
    if not session.last_payment:
        return
    payment = dict(session.last_payment)
    for key in (
        "qr_png",
        "qr_png_marker",
        "qr_png_method",
        "qr_png_at",
        "qr_png_expires_at",
        "qr_png_url",
    ):
        payment.pop(key, None)
    session.last_payment = payment


def _store_payment_qr_png(
    session: BrowserSession,
    page: Any,
    png: bytes,
    method: str = "",
    state: dict[str, Any] | None = None,
) -> bool:
    if not png:
        return False
    payment = dict(session.last_payment or {})
    marker = _payment_marker(state)
    payment["qr_png"] = bytes(png)
    payment["qr_png_at"] = time.monotonic()
    payment["qr_png_expires_at"] = time.monotonic() + 55
    payment["qr_png_url"] = page.url
    if marker:
        payment["qr_png_marker"] = marker
    if method:
        payment["qr_png_method"] = method
        payment["payment_method"] = method
    session.last_payment = payment
    return True


def _cached_payment_qr_png(
    session: BrowserSession,
    state: dict[str, Any] | None = None,
    method: str = "",
) -> bytes | None:
    payment = session.last_payment or {}
    png = payment.get("qr_png")
    if not isinstance(png, (bytes, bytearray)):
        return None
    if float(payment.get("qr_png_expires_at") or 0) <= time.monotonic():
        return None
    if method and payment.get("qr_png_method") and payment.get("qr_png_method") != method:
        return None
    marker = _payment_marker(state)
    cached_marker = str(payment.get("qr_png_marker") or "")
    if marker and cached_marker and marker != cached_marker:
        return None
    return bytes(png)


async def _cache_current_payment_qr_png(
    session: BrowserSession,
    page: Any,
    method: str,
    state: dict[str, Any] | None = None,
    timeout_ms: int = 3500,
) -> bool:
    if not settings.recharge_qr_cache_enabled:
        return False
    canvas = page.locator("#qrcode-canvas")
    if await canvas.count() != 1 or not await canvas.is_visible():
        return False
    png = await _wait_for_payment_qr_png(page, canvas, timeout_ms=timeout_ms)
    if not png:
        return False
    if state is None:
        state = await _read_payment_page_state(page)
    return _store_payment_qr_png(session, page, png, method, state)


async def _read_payment_page_state(page: Any) -> dict[str, Any]:
    try:
        state = await page.evaluate(
            """() => {
                const cloneObject = (value) => {
                    const result = {};
                    if (!value || typeof value !== "object") return result;
                    for (const key of Object.keys(value)) {
                        const item = value[key];
                        if (item == null || ["string", "number", "boolean"].includes(typeof item)) {
                            result[key] = item;
                        }
                    }
                    return result;
                };
                const readVm = (vm) => {
                    if (!vm) return null;
                    const data = vm.$data || vm;
                    const urlParams = data.urlParams || vm.urlParams || {};
                    const state = {
                        qrCode: data.qrCode || vm.qrCode || "",
                        outTradeNo: data.outTradeNo || vm.outTradeNo || "",
                        payStatus: data.payStatus || vm.payStatus || "",
                        timeOut: data.timeOut ?? vm.timeOut ?? null,
                        timeOut1: data.timeOut1 ?? vm.timeOut1 ?? null,
                        platform: data.platform ?? vm.platform ?? "",
                        payChannel: data.payChannel ?? vm.payChannel ?? "",
                        payChannelIndex: data.payChannelIndex ?? vm.payChannelIndex ?? "",
                        tabIndex: data.tabIndex ?? vm.tabIndex ?? null,
                        dialogVisible: data.dialogVisible ?? vm.dialogVisible ?? null,
                        subLoading: data.subLoading ?? vm.subLoading ?? false,
                        urlParams: cloneObject(urlParams),
                    };
                    if (
                        state.qrCode || state.outTradeNo || state.urlParams.out_trade_no ||
                        state.urlParams.account_id || Object.prototype.hasOwnProperty.call(data, "payStatus")
                    ) {
                        return state;
                    }
                    return null;
                };
                const seen = new Set();
                const candidates = [];
                const pushVm = (vm) => {
                    if (vm && !seen.has(vm)) {
                        seen.add(vm);
                        candidates.push(vm);
                    }
                };
                const canvas = document.querySelector("#qrcode-canvas");
                for (let node = canvas; node; node = node.parentElement) pushVm(node.__vue__);
                const nodes = Array.from(document.querySelectorAll("*"));
                for (const node of nodes) {
                    pushVm(node.__vue__);
                    if (candidates.length > 200) break;
                }
                for (const vm of candidates) {
                    const state = readVm(vm);
                    if (state) return state;
                }
                return {};
            }"""
        )
    except Exception as exc:
        if not _is_transient_page_error(exc):
            raise
        return {}
    return state if isinstance(state, dict) else {}


async def _wait_for_payment_qr_state(
    page: Any,
    previous_marker: str = "",
    timeout_ms: int = 10000,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_ms / 1000
    last_state: dict[str, Any] = {}
    while time.monotonic() < deadline:
        state = await _read_payment_page_state(page)
        if state:
            last_state = state
            marker = _payment_marker(state)
            if _payment_qr_state_ready(state) and (not previous_marker or marker != previous_marker):
                return state
        await page.wait_for_timeout(250)
    return last_state


def _payment_ids_from_state_or_session(
    state: dict[str, Any] | None,
    session: BrowserSession,
) -> tuple[str, str]:
    params = state.get("urlParams") if isinstance(state, dict) else {}
    if not isinstance(params, dict):
        params = {}
    payment = session.last_payment or {}
    state_account_id = state.get("account_id") if isinstance(state, dict) else ""
    state_out_trade_no = state.get("outTradeNo") if isinstance(state, dict) else ""
    account_id = str(params.get("account_id") or state_account_id or "")
    out_trade_no = str(params.get("out_trade_no") or state_out_trade_no or "")
    if not account_id:
        account_id = str(payment.get("account_id") or payment.get("provider_account_id") or "")
    if not out_trade_no:
        out_trade_no = str(payment.get("out_trade_no") or payment.get("order_no") or "")
    return account_id, out_trade_no


async def _remember_payment_state(
    session: BrowserSession,
    page: Any,
    method: str | None = None,
) -> dict[str, Any]:
    state = await _read_payment_page_state(page)
    params = state.get("urlParams") if isinstance(state.get("urlParams"), dict) else {}
    query = parse_qs(urlparse(page.url).query)
    account_id = str(params.get("account_id") or query.get("account_id", [""])[0] or "")
    out_trade_no = str(
        state.get("outTradeNo")
        or params.get("out_trade_no")
        or query.get("out_trade_no", [""])[0]
        or ""
    )
    payment = dict(session.last_payment or {})
    if account_id:
        payment["account_id"] = account_id
        payment["provider_account_id"] = account_id
    if out_trade_no:
        payment["out_trade_no"] = out_trade_no
        payment["order_no"] = out_trade_no
    if method:
        payment["payment_method"] = method
        payment["pay_channel"] = PAYMENT_CHANNEL_CODES.get(method, "")
    if state.get("qrCode"):
        payment["qr_code_marker"] = str(state.get("qrCode"))
    if state.get("timeOut") is not None:
        payment["time_out"] = state.get("timeOut")
    payment["url"] = page.url
    session.last_payment = payment
    return state


async def _trigger_official_payment_qr_refresh(page: Any) -> bool:
    try:
        return bool(await page.evaluate(
            """async () => {
                const findPaymentVm = () => {
                    const candidates = [];
                    const seen = new Set();
                    const pushVm = (vm) => {
                        if (vm && !seen.has(vm)) {
                            seen.add(vm);
                            candidates.push(vm);
                        }
                    };
                    const canvas = document.querySelector("#qrcode-canvas");
                    for (let node = canvas; node; node = node.parentElement) pushVm(node.__vue__);
                    for (const node of Array.from(document.querySelectorAll("*"))) {
                        pushVm(node.__vue__);
                        if (candidates.length > 200) break;
                    }
                    return candidates.find((vm) => typeof vm.init === "function" && typeof vm.tradeCreate === "function")
                        || candidates.find((vm) => typeof vm.init === "function");
                };
                const vm = findPaymentVm();
                if (!vm || typeof vm.init !== "function") return false;
                const result = vm.init();
                if (result && typeof result.then === "function") await result;
                return true;
            }"""
        ))
    except Exception as exc:
        if not _is_transient_page_error(exc):
            raise
    return False


async def _query_official_payment_status(
    page: Any,
    account_id: str,
    out_trade_no: str,
) -> dict[str, Any] | None:
    if not account_id or not out_trade_no:
        return None
    try:
        response = await page.evaluate(
            """async ({ accountId, outTradeNo }) => {
                const res = await fetch("/unifyapi/upayquery", {
                    method: "POST",
                    credentials: "include",
                    headers: {
                        "Content-Type": "application/json;charset=UTF-8",
                        "Accept": "application/json, text/plain, */*",
                    },
                    body: JSON.stringify({ accountId, outTradeNo }),
                });
                const text = await res.text();
                let data = null;
                try { data = JSON.parse(text); } catch {}
                return { ok: res.ok, status: res.status, data, text: text.slice(0, 300) };
            }""",
            {"accountId": account_id, "outTradeNo": out_trade_no},
        )
    except Exception as exc:
        if not _is_transient_page_error(exc):
            raise
        return None
    if not isinstance(response, dict):
        return None
    data = response.get("data")
    if not isinstance(data, dict):
        return {
            "status": "unknown",
            "message": f"官方支付状态接口返回异常：HTTP {response.get('status')}",
            "raw": response,
        }
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    trade_status = str(result.get("tradeStatus") or data.get("tradeStatus") or "").upper()
    if data.get("success") is True and trade_status == "SUCCESS":
        return {
            "status": "paid",
            "message": "官方支付状态接口显示支付成功",
            "trade_status": trade_status,
            "order_no": out_trade_no,
        }
    if data.get("success") is True and trade_status == "NOTPAY":
        return {
            "status": "pending",
            "message": "官方支付状态接口显示等待付款",
            "trade_status": trade_status,
            "order_no": out_trade_no,
        }
    if data.get("success") is True and trade_status == "FAIL":
        return {
            "status": "failed",
            "message": "官方支付状态接口显示支付失败",
            "trade_status": trade_status,
            "order_no": out_trade_no,
        }
    if data.get("success") is False:
        return {
            "status": "unknown",
            "message": str(data.get("errorMsg") or data.get("message") or "官方支付状态接口查询失败"),
            "trade_status": trade_status,
            "order_no": out_trade_no,
        }
    return {
        "status": "pending",
        "message": f"官方支付状态：{trade_status or '等待更新'}",
        "trade_status": trade_status,
        "order_no": out_trade_no,
    }


def _png_size(png: bytes) -> tuple[int, int]:
    if not png.startswith(b"\x89PNG\r\n\x1a\n") or len(png) < 33:
        return (0, 0)
    return struct.unpack(">II", png[16:24])


def _png_rows_rgb(png: bytes) -> tuple[int, int, list[list[tuple[int, int, int]]]]:
    width, height = _png_size(png)
    if width <= 0 or height <= 0:
        return (0, 0, [])
    pos = 8
    bit_depth = 0
    color_type = 0
    idat = bytearray()
    while pos + 8 <= len(png):
        length = struct.unpack(">I", png[pos:pos + 4])[0]
        chunk_type = png[pos + 4:pos + 8]
        data_start = pos + 8
        data_end = data_start + length
        if data_end + 4 > len(png):
            break
        data = png[data_start:data_end]
        if chunk_type == b"IHDR":
            bit_depth = data[8]
            color_type = data[9]
        elif chunk_type == b"IDAT":
            idat.extend(data)
        elif chunk_type == b"IEND":
            break
        pos = data_end + 4
    channels_by_type = {0: 1, 2: 3, 4: 2, 6: 4}
    channels = channels_by_type.get(color_type)
    if bit_depth != 8 or not channels or not idat:
        return (width, height, [])
    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    rows: list[bytes] = []
    offset = 0
    prev = bytearray(stride)
    for _ in range(height):
        if offset >= len(raw):
            return (width, height, [])
        filter_type = raw[offset]
        offset += 1
        current = bytearray(raw[offset:offset + stride])
        offset += stride
        for index in range(stride):
            left = current[index - channels] if index >= channels else 0
            up = prev[index]
            up_left = prev[index - channels] if index >= channels else 0
            if filter_type == 1:
                current[index] = (current[index] + left) & 0xFF
            elif filter_type == 2:
                current[index] = (current[index] + up) & 0xFF
            elif filter_type == 3:
                current[index] = (current[index] + ((left + up) >> 1)) & 0xFF
            elif filter_type == 4:
                pa = abs(up - up_left)
                pb = abs(left - up_left)
                pc = abs(left + up - 2 * up_left)
                predictor = left if pa <= pb and pa <= pc else (up if pb <= pc else up_left)
                current[index] = (current[index] + predictor) & 0xFF
            elif filter_type != 0:
                return (width, height, [])
        rows.append(bytes(current))
        prev = current
    rgb_rows: list[list[tuple[int, int, int]]] = []
    for row in rows:
        rgb_row: list[tuple[int, int, int]] = []
        for x in range(width):
            base = x * channels
            if color_type == 0:
                value = row[base]
                rgb_row.append((value, value, value))
            elif color_type == 4:
                value = row[base]
                rgb_row.append((value, value, value))
            else:
                rgb_row.append((row[base], row[base + 1], row[base + 2]))
        rgb_rows.append(rgb_row)
    return (width, height, rgb_rows)


def _payment_qr_png_has_loading_overlay(png: bytes) -> bool:
    try:
        width, height, rows = _png_rows_rgb(png)
    except Exception:
        return False
    if width < 80 or height < 80 or not rows:
        return False
    cx = width // 2
    cy = height // 2
    radius = max(18, min(width, height) // 5)
    total = 0
    very_light = 0
    mid_dark = 0
    outer_total = 0
    neutral_gray = 0
    for y in range(max(0, cy - radius), min(height, cy + radius + 1)):
        for x in range(max(0, cx - radius), min(width, cx + radius + 1)):
            dx = x - cx
            dy = y - cy
            if dx * dx + dy * dy > radius * radius:
                continue
            r, g, b = rows[y][x]
            total += 1
            if r >= 238 and g >= 238 and b >= 238:
                very_light += 1
            if 70 <= r <= 190 and 70 <= g <= 190 and 70 <= b <= 190:
                mid_dark += 1
    outer_margin = max(8, min(width, height) // 18)
    outer_radius = min(width, height) // 2 - outer_margin
    for y in range(outer_margin, height - outer_margin):
        for x in range(outer_margin, width - outer_margin):
            dx = x - cx
            dy = y - cy
            distance_sq = dx * dx + dy * dy
            if distance_sq <= (radius + outer_margin) * (radius + outer_margin):
                continue
            if distance_sq > outer_radius * outer_radius:
                continue
            r, g, b = rows[y][x]
            luma = (r + g + b) / 3
            outer_total += 1
            if 78 <= luma <= 225 and max(r, g, b) - min(r, g, b) <= 18:
                neutral_gray += 1
    if not total:
        return False
    light_ratio = very_light / total
    mid_ratio = mid_dark / total
    gray_ratio = neutral_gray / outer_total if outer_total else 0
    # Official loading frames render a large white disk with a small gray spinner in the center.
    return (
        light_ratio > 0.68 and mid_ratio > 0.005
    ) or (
        light_ratio > 0.50 and mid_ratio > 0.002 and gray_ratio > 0.08
    )


def _payment_qr_png_has_code(png: bytes) -> bool:
    try:
        width, height, rows = _png_rows_rgb(png)
    except Exception:
        return False
    if width < 80 or height < 80 or not rows:
        return False
    dark = 0
    total = 0
    margin_x = max(0, width // 20)
    margin_y = max(0, height // 20)
    for y in range(margin_y, height - margin_y):
        for x in range(margin_x, width - margin_x):
            r, g, b = rows[y][x]
            total += 1
            if r <= 80 and g <= 80 and b <= 80:
                dark += 1
    if not total:
        return False
    dark_ratio = dark / total
    return 0.02 <= dark_ratio <= 0.70


async def _canvas_png_bytes(canvas: Any) -> bytes | None:
    try:
        data_url = await canvas.evaluate(
            """(el) => {
                if (!el || typeof el.toDataURL !== "function") return "";
                const width = Number(el.width || 0);
                const height = Number(el.height || 0);
                if (width < 80 || height < 80) return "";
                return el.toDataURL("image/png");
            }"""
        )
    except Exception as exc:
        if not _is_transient_page_error(exc):
            raise
        return None
    if not isinstance(data_url, str) or not data_url.startswith("data:image/png;base64,"):
        return None
    try:
        return base64.b64decode(data_url.split(",", 1)[1], validate=True)
    except Exception:
        return None


async def _wait_for_payment_qr_png(
    page: Any,
    canvas: Any,
    previous_png: bytes | None = None,
    previous_marker: str = "",
    timeout_ms: int = 15000,
) -> bytes | None:
    deadline = time.monotonic() + timeout_ms / 1000
    stable_png: bytes | None = None
    stable_seen = 0
    while time.monotonic() < deadline:
        try:
            if await canvas.count() == 1 and await canvas.is_visible():
                state = await _read_payment_page_state(page)
                state_marker = _payment_marker(state)
                if state and (not _payment_qr_state_ready(state) or (previous_marker and state_marker == previous_marker)):
                    await page.wait_for_timeout(300)
                    continue
                current_png = await _canvas_png_bytes(canvas)
                if not current_png and not await _payment_qr_busy(page, canvas):
                    current_png = await canvas.screenshot(type="png")
                if (
                    current_png
                    and current_png != previous_png
                    and _payment_qr_png_has_code(current_png)
                    and not _payment_qr_png_has_loading_overlay(current_png)
                ):
                    if current_png == stable_png:
                        stable_seen += 1
                    else:
                        stable_png = current_png
                        stable_seen = 1
                    if stable_seen >= 2:
                        return current_png
        except Exception as exc:
            if not _is_transient_page_error(exc):
                raise
        await page.wait_for_timeout(300)
    return None


async def _click_payment_qr_refresh(page: Any, canvas: Any) -> bool:
    trigger_candidates = (
        page.locator(".refresh-mask-init"),
        page.locator(".refresh-mask button"),
        page.locator(".refresh-mask"),
        canvas,
    )
    for locator in trigger_candidates:
        try:
            count = await locator.count()
        except Exception:
            continue
        for index in range(count - 1, -1, -1):
            candidate = locator.nth(index)
            try:
                if await candidate.is_visible():
                    await candidate.click(timeout=3000)
                    return True
            except Exception:
                continue
    return False


async def refresh_payment_qr(account: dict[str, Any]) -> dict[str, Any]:
    async with _account_operation_lock(int(account["id"])):
        session = _sessions.get(int(account["id"]))
        if not session:
            return {"status": "qr_unavailable", "message": "当前账号没有支付会话"}

        for page in session.context.pages:
            try:
                canvas = page.locator("#qrcode-canvas")
                if await canvas.count() != 1:
                    continue
                previous_state = await _read_payment_page_state(page)
                previous_marker = _payment_marker(previous_state)
                previous_png = await canvas.screenshot(type="png")
                _clear_payment_qr_cache(session)
                triggered = await _trigger_official_payment_qr_refresh(page)
                clicked = triggered or await _click_payment_qr_refresh(page, canvas)
                if not clicked:
                    return {"status": "qr_unavailable", "message": "没有找到可点击的官方二维码区域"}
                png = await _wait_for_payment_qr_png(
                    page,
                    canvas,
                    previous_png=previous_png,
                    previous_marker=previous_marker,
                    timeout_ms=18000,
                )
                if png:
                    state = await _remember_payment_state(session, page)
                    method = str((session.last_payment or {}).get("payment_method") or "")
                    _store_payment_qr_png(session, page, png, method, state)
                    return {"status": "ready", "message": "官方二维码已刷新"}
                return {"status": "qr_unavailable", "message": "官方二维码刷新后未生成新码"}
            except Exception as exc:
                return {"status": "qr_unavailable", "message": f"刷新二维码失败：{exc}"}
        return {"status": "qr_unavailable", "message": "当前支付页面没有二维码"}


async def check_recharge_payment_status(account: dict[str, Any]) -> dict[str, Any]:
    async with _account_operation_lock(int(account["id"])):
        session = _sessions.get(int(account["id"]))
        if not session:
            return {"status": "no_session", "message": "当前账号没有支付会话"}

        payment_pages = []
        fallback_pages = []
        for page in session.context.pages:
            try:
                url = str(page.url or "")
            except Exception:
                continue
            if _is_payment_url(url) or "checkstand" in url:
                payment_pages.append(page)
            elif "ctyun.cn" in url:
                fallback_pages.append(page)
        if not payment_pages and not fallback_pages:
            return {"status": "no_payment_page", "message": "当前没有打开官方收银台"}

        page = (payment_pages or fallback_pages)[-1]
        try:
            state = await _read_payment_page_state(page)
            await _remember_payment_state(session, page)
            account_id, out_trade_no = _payment_ids_from_state_or_session(state, session)
            official_status = await _query_official_payment_status(page, account_id, out_trade_no)
            if official_status:
                if official_status.get("status") == "pending" and _payment_state_expired(state):
                    return {
                        "status": "expired",
                        "message": "官方二维码已过期，请刷新二维码",
                        "url": page.url,
                        "order_no": out_trade_no,
                        "cookie_state_enc": await _save_state(session),
                    }
                return {
                    **official_status,
                    "url": page.url,
                    "cookie_state_enc": await _save_state(session),
                }
            if _payment_state_expired(state):
                return {
                    "status": "expired",
                    "message": "官方二维码已过期，请刷新二维码",
                    "url": page.url,
                    "order_no": out_trade_no,
                    "cookie_state_enc": await _save_state(session),
                }
            text = re.sub(r"\s+", " ", await _visible_text(page)).strip()
            success_keywords = ("支付成功", "充值成功", "交易成功", "付款成功", "已支付", "订单支付成功")
            expired_keywords = ("二维码已过期", "二维码失效", "已失效", "重新获取二维码", "刷新二维码")
            pending_keywords = ("微信支付", "支付宝支付", "翼支付", "扫码支付", "请扫码", "确认支付", "待支付")
            if any(keyword in text for keyword in success_keywords):
                return {
                    "status": "paid",
                    "message": "官方收银台显示支付成功",
                    "url": page.url,
                    "cookie_state_enc": await _save_state(session),
                }
            if any(keyword in text for keyword in expired_keywords):
                return {
                    "status": "expired",
                    "message": "官方二维码已过期，请刷新二维码",
                    "url": page.url,
                    "cookie_state_enc": await _save_state(session),
                }
            if any(keyword in text for keyword in pending_keywords):
                return {
                    "status": "pending",
                    "message": "等待扫码支付",
                    "url": page.url,
                    "cookie_state_enc": await _save_state(session),
                }
            return {
                "status": "pending",
                "message": text[:120] or "等待官方支付状态更新",
                "url": page.url,
                "cookie_state_enc": await _save_state(session),
            }
        except Exception as exc:
            return {"status": "unknown", "message": f"读取官方支付状态失败：{exc}", "url": page.url}


async def close_recharge_session(account_id: int) -> dict[str, Any]:
    async with _account_operation_lock(int(account_id)):
        session = _sessions.get(int(account_id))
        if not session:
            return {"status": "ready", "message": "没有需要关闭的充值会话", "closed": 0}

        closed = 0
        kept_recharge = 0
        for page in list(session.context.pages):
            try:
                url = str(page.url or "")
            except Exception:
                url = ""
            if _is_recharge_url(url):
                kept_recharge += 1
                session.page = page
                continue
            if not _is_payment_url(url):
                continue
            try:
                await page.close(run_before_unload=False)
                closed += 1
            except Exception:
                continue

        live_pages = [page for page in session.context.pages if not page.is_closed()]
        if live_pages:
            recharge_pages = []
            for page in live_pages:
                try:
                    if _is_recharge_url(str(page.url or "")):
                        recharge_pages.append(page)
                except Exception:
                    continue
            if recharge_pages:
                session.page = recharge_pages[-1]
            elif session.page.is_closed() or session.page not in live_pages:
                session.page = live_pages[0]
        else:
            session.page = await session.context.new_page()
        session.interactive_until = time.monotonic() + 1800 if kept_recharge else 0
        return {
            "status": "ready",
            "message": (
                f"已关闭 {closed} 个收银台页面，保留 {kept_recharge} 个充值页"
                if closed or kept_recharge
                else "没有需要关闭的充值页面"
            ),
            "closed": closed,
            "kept_recharge": kept_recharge,
            "cookie_state_enc": await _save_state(session),
        }


async def reset_browser_session(account_id: int) -> None:
    async with _account_operation_lock(int(account_id)):
        session = _sessions.pop(int(account_id), None)
        if session:
            try:
                await session.context.close()
            except Exception:
                pass


async def close_browser_sessions() -> None:
    global _playwright, _browser
    async with _browser_lock:
        for session in _sessions.values():
            try:
                await session.context.close()
            except Exception:
                pass
        _sessions.clear()
        if _browser:
            await _browser.close()
        if _playwright:
            await _playwright.stop()
        _browser = None
        _playwright = None

export function isLoginURL(url = "") {
  return /login|sso|passport|\/auth\//i.test(String(url));
}

export function isSecurityChallengeText(text = "") {
  // “短信登录” is a normal login mode on the current page. Do not treat its
  // SMS-code input as an unexpected security challenge before switching tabs.
  return /滑块验证|人机验证|图形验证码|请完成.{0,12}(?:安全)?验证|安全验证|访问异常|操作过于频繁/i.test(String(text));
}

export async function firstVisible(locator) {
  const count = await locator.count().catch(() => 0);
  for (let index = 0; index < count; index += 1) {
    const candidate = locator.nth(index);
    if (await candidate.isVisible().catch(() => false)) return candidate;
  }
  return null;
}

export async function waitForVisibleText(page, text, timeout = 15000) {
  const deadline = Date.now() + timeout;
  do {
    const candidate = await firstVisible(page.getByText(text, { exact: true }));
    if (candidate) return candidate;
    await page.waitForTimeout(200);
  } while (Date.now() < deadline);
  return null;
}

export async function fillAnyVisible(page, placeholders, value, timeout = 10000) {
  const deadline = Date.now() + timeout;
  do {
    for (const placeholder of placeholders) {
      const candidate = await firstVisible(page.getByPlaceholder(placeholder, { exact: true }));
      if (!candidate) continue;
      await candidate.fill(value);
      return true;
    }
    await page.waitForTimeout(200);
  } while (Date.now() < deadline);
  return false;
}

export async function firstVisibleButton(page, names) {
  for (const name of names) {
    const candidate = await firstVisible(page.getByRole("button", { name, exact: true }));
    if (candidate && await candidate.isEnabled().catch(() => true)) return candidate;
  }
  return null;
}

export function loginFailureMessage(text = "") {
  const value = String(text);
  if (/动态验证码错误|验证码不正确|验证码已失效|动态口令错误/i.test(value)) {
    return { status: "totp_failed", message: "MFA 动态验证码未通过，请检查保存的 TOTP 密钥和服务器 NTP 时间" };
  }
  if (/账号或密码错误|用户名或密码错误|密码错误|账号不存在|登录失败|帐号或密码错误/i.test(value)) {
    return { status: "login_failed", message: "天翼云拒绝了账号密码，请检查平台内保存的登录资料" };
  }
  return null;
}

export async function waitForLoginToFinish(page, timeout = 15000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (!isLoginURL(page.url())) return true;
    await page.waitForTimeout(250);
  }
  return !isLoginURL(page.url());
}

export async function safeLoginDiagnostics(page) {
  const placeholders = await page.locator("input").evaluateAll((elements) => elements
    .filter((element) => Boolean(element.offsetWidth || element.offsetHeight || element.getClientRects().length))
    .map((element) => element.getAttribute("placeholder") || element.getAttribute("aria-label") || element.type || "input")
    .filter(Boolean)).catch(() => []);
  const title = await page.title().catch(() => "");
  return { url: page.url(), title, placeholders: [...new Set(placeholders)].slice(0, 8) };
}

function deepValue(value, keys) {
  if (Array.isArray(value)) for (const item of value) { const found = deepValue(item, keys); if (found !== undefined) return found; }
  else if (value && typeof value === "object") {
    for (const key of keys) if (value[key] !== undefined && value[key] !== null) return value[key];
    for (const item of Object.values(value)) { const found = deepValue(item, keys); if (found !== undefined) return found; }
  }
}

export async function captureOfficialFeedback(response) {
  const rawURL = response.url();
  const parsedURL = new URL(rawURL);
  const method = response.request().method();
  if (parsedURL.origin !== "https://www.ctyun.cn" || method !== "POST" || /\/qrcode\/Verify$/i.test(parsedURL.pathname)) return null;
  const status = response.status();
  const contentType = String((await response.allHeaders().catch(() => ({})))["content-type"] || "");
  if (!/json/i.test(contentType)) return status >= 400 ? { path: parsedURL.pathname, http_status: status } : null;
  const payload = await response.json().catch(() => null);
  if (!payload) return status >= 400 ? { path: parsedURL.pathname, http_status: status } : null;
  const code = deepValue(payload, ["code", "errorCode", "error_code"]);
  const message = deepValue(payload, ["message", "msg", "reason", "errorMessage", "error_description"]);
  if (code === undefined && message === undefined && status < 400) return null;
  return {
    path: `${method} ${parsedURL.pathname}`,
    http_status: status,
    code: String(code ?? "").slice(0, 80),
    message: String(message ?? "").replace(/\s+/g, " ").slice(0, 200),
  };
}

export function formatOfficialFeedback(items = []) {
  return items.slice(-5).map((item) => `${item.path} HTTP ${item.http_status}${item.code ? ` code=${item.code}` : ""}${item.message ? ` message=${item.message}` : ""}`).join("；");
}

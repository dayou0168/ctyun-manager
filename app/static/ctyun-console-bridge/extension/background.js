const CTYUN_TARGET_DEFAULT = "https://console.ctyun.cn/console/index/#/console";

function isCtyunHost(host) {
  const normalized = String(host || "").toLowerCase().replace(/^\./, "");
  return normalized === "ctyun.cn" || normalized.endsWith(".ctyun.cn");
}

function cookieUrl(cookie) {
  if (cookie.url) return cookie.url;
  const domain = String(cookie.domain || "").replace(/^\./, "");
  const path = cookie.path || "/";
  const scheme = cookie.secure === false ? "http" : "https";
  return `${scheme}://${domain || "ctyun.cn"}${path.startsWith("/") ? path : `/${path}`}`;
}

function sameSite(value) {
  const normalized = String(value || "").toLowerCase();
  if (normalized === "strict") return "strict";
  if (normalized === "lax") return "lax";
  if (normalized === "none" || normalized === "no_restriction") return "no_restriction";
  return "unspecified";
}

function chromeCall(fn, ...args) {
  return new Promise((resolve, reject) => {
    fn(...args, (result) => {
      const err = chrome.runtime.lastError;
      if (err) reject(new Error(err.message));
      else resolve(result);
    });
  });
}

async function removeExistingCtyunCookies() {
  const cookies = await chromeCall(chrome.cookies.getAll, { domain: "ctyun.cn" });
  await Promise.all(
    cookies
      .filter((cookie) => isCtyunHost(cookie.domain))
      .map((cookie) => chromeCall(chrome.cookies.remove, { url: cookieUrl(cookie), name: cookie.name }).catch(() => null)),
  );
}

async function setCookie(cookie) {
  const domain = String(cookie.domain || "");
  const host = domain.replace(/^\./, "");
  const urlHost = cookie.url ? new URL(cookie.url).hostname : "";
  if (!isCtyunHost(host) && !isCtyunHost(urlHost)) return false;

  const details = {
    url: cookieUrl(cookie),
    name: String(cookie.name || ""),
    value: String(cookie.value || ""),
    path: cookie.path || "/",
    secure: cookie.secure !== false,
    httpOnly: Boolean(cookie.httpOnly),
    sameSite: sameSite(cookie.sameSite),
  };
  if (!details.name) return false;
  if (domain.startsWith(".")) details.domain = domain;
  if (typeof cookie.expires === "number" && cookie.expires > 0) details.expirationDate = cookie.expires;
  if (details.sameSite === "no_restriction") details.secure = true;
  await chromeCall(chrome.cookies.set, details);
  return true;
}

async function waitForTabComplete(tabId, timeoutMs = 15000) {
  const tab = await chromeCall(chrome.tabs.get, tabId).catch(() => null);
  if (tab && tab.status === "complete") return true;
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      resolve(false);
    }, timeoutMs);
    function listener(updatedTabId, changeInfo) {
      if (updatedTabId !== tabId || changeInfo.status !== "complete") return;
      clearTimeout(timer);
      chrome.tabs.onUpdated.removeListener(listener);
      resolve(true);
    }
    chrome.tabs.onUpdated.addListener(listener);
  });
}

async function applyLocalStorage(tabId, origins) {
  const byOrigin = {};
  for (const origin of origins || []) {
    if (!origin || !origin.origin) continue;
    try {
      const host = new URL(origin.origin).hostname;
      if (isCtyunHost(host)) byOrigin[origin.origin] = origin.localStorage || [];
    } catch {
      // ignore invalid origin
    }
  }
  if (!Object.keys(byOrigin).length) return;
  await waitForTabComplete(tabId, 15000);
  await chrome.scripting.executeScript({
    target: { tabId },
    func: (itemsByOrigin) => {
      const items = itemsByOrigin[location.origin] || [];
      for (const item of items) {
        if (!item || typeof item.name !== "string") continue;
        localStorage.setItem(item.name, String(item.value || ""));
      }
    },
    args: [byOrigin],
  }).catch(() => null);
}

async function openConsole(payload) {
  const state = payload.storage_state || {};
  const cookies = Array.isArray(state.cookies) ? state.cookies : [];
  if (!cookies.length) throw new Error("平台没有返回天翼云登录 cookie");

  await removeExistingCtyunCookies();
  const results = await Promise.all(cookies.map((cookie) => setCookie(cookie).catch(() => false)));
  const applied = results.filter(Boolean).length;
  if (!applied) throw new Error("写入天翼云 cookie 失败");

  const targetUrl = String(payload.target_url || CTYUN_TARGET_DEFAULT);
  const tab = await chromeCall(chrome.tabs.create, {
    url: targetUrl.startsWith("https://console.ctyun.cn/") ? targetUrl : CTYUN_TARGET_DEFAULT,
    active: true,
  });
  await applyLocalStorage(tab.id, state.origins || []);
  return { tabId: tab.id, applied };
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || message.type !== "ctyun-open-console") return false;
  openConsole(message.payload || {})
    .then((result) => {
      sendResponse({
        requestId: message.requestId || "",
        ok: true,
        message: `已写入 ${result.applied} 个天翼云登录 cookie，并打开官方控制台`,
        tabId: result.tabId,
      });
    })
    .catch((error) => {
      sendResponse({
        requestId: message.requestId || "",
        ok: false,
        message: error && error.message ? error.message : "打开官方控制台失败",
      });
    });
  return true;
});

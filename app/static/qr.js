const params = new URLSearchParams(location.search);
const accountId = params.get("account_id");
const paymentMethod = params.get("payment_method") || "wechat";
const paymentAmount = params.get("amount") || "";
const initialExpiresIn = Number(params.get("expires_in") || 0);
const initialExpiresAtEpoch = Number(params.get("expires_at") || 0);
const initialServerTimeEpoch = Number(params.get("server_time") || 0);
const paymentMethodNames = {
  wechat: "微信支付",
  alipay: "支付宝支付",
  bestpay: "翼支付",
};
const image = document.querySelector("#qrImage");
const statusNode = document.querySelector("#qrStatus");
const refreshButton = document.querySelector("#refreshQrBtn");
const closeButton = document.querySelector("#closeQrBtn");
const amountNode = document.querySelector("#paymentAmount");
document.querySelector("#paymentMethodTitle").textContent = paymentMethodNames[paymentMethod] || "扫码支付";
let attempts = 0;
let countdownTimer = null;
let statusTimer = null;
let remainingSeconds = 60;
let paymentFinished = false;
let loadingFrameAttempts = 0;
let qrExpired = false;
let refreshInFlight = false;
let statusInFlight = false;
let pendingRefreshStartedAtMs = 0;

function normalizedRemainingSeconds(value, fallback = 60) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return fallback;
  return Math.max(0, Math.min(3600, Math.ceil(numeric)));
}

let serverExpiresIn = normalizedRemainingSeconds(initialExpiresIn, 60);
let serverExpiresAtMs = localDeadlineFromServer(initialExpiresAtEpoch, initialServerTimeEpoch) || Date.now() + serverExpiresIn * 1000;

function localDeadlineFromServer(expiresAtEpoch, serverTimeEpoch = 0) {
  const expiresAt = Number(expiresAtEpoch);
  if (!Number.isFinite(expiresAt) || expiresAt <= 0) return 0;
  const serverTime = Number(serverTimeEpoch);
  const base = Number.isFinite(serverTime) && serverTime > 0 ? serverTime : Date.now() / 1000;
  const remaining = Math.max(0, expiresAt - base);
  return Date.now() + remaining * 1000;
}

function setServerExpiresIn(value, fallback = 60) {
  serverExpiresIn = normalizedRemainingSeconds(value, fallback);
  serverExpiresAtMs = Date.now() + serverExpiresIn * 1000;
}

function setServerExpiry(value, expiresAtEpoch = 0, serverTimeEpoch = 0, fallback = 60) {
  const deadline = localDeadlineFromServer(expiresAtEpoch, serverTimeEpoch);
  if (deadline) {
    serverExpiresAtMs = deadline;
    serverExpiresIn = normalizedRemainingSeconds((serverExpiresAtMs - Date.now()) / 1000, fallback);
    return;
  }
  setServerExpiresIn(value, fallback);
}

function currentServerRemaining() {
  return normalizedRemainingSeconds((serverExpiresAtMs - Date.now()) / 1000, serverExpiresIn);
}

function formatPaymentAmount(value) {
  const normalized = String(value || "").trim();
  if (!normalized) return "";
  const numeric = Number(normalized);
  if (!Number.isFinite(numeric)) return normalized;
  return numeric.toLocaleString("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function renderPaymentAmount() {
  if (!amountNode) return;
  const amount = formatPaymentAmount(paymentAmount);
  if (!amount) {
    amountNode.hidden = true;
    return;
  }
  const label = document.createElement("small");
  label.textContent = "付款金额";
  amountNode.replaceChildren(label, document.createTextNode(` ¥${amount}`));
  amountNode.hidden = false;
}

function fallback(message) {
  clearInterval(countdownTimer);
  clearInterval(statusTimer);
  refreshInFlight = false;
  statusInFlight = false;
  pendingRefreshStartedAtMs = 0;
  image.hidden = true;
  statusNode.textContent = message;
  parent.postMessage({ type: "ctyun-qr-error", message }, location.origin);
}

function notifyPaymentStatus(status, message) {
  parent.postMessage({ type: "ctyun-recharge-status", status, message }, location.origin);
}

function ensureStatusPolling() {
  if (statusTimer || paymentFinished) return;
  statusTimer = setInterval(pollPaymentStatus, 5000);
  setTimeout(pollPaymentStatus, 4500);
}

function imageLooksLikeLoadingFrame(img) {
  const width = img.naturalWidth;
  const height = img.naturalHeight;
  if (width < 80 || height < 80) return false;
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (!context) return false;
  try {
    context.drawImage(img, 0, 0, width, height);
    const pixels = context.getImageData(0, 0, width, height).data;
    const cx = Math.floor(width / 2);
    const cy = Math.floor(height / 2);
    const radius = Math.max(18, Math.floor(Math.min(width, height) / 5));
    let total = 0;
    let veryLight = 0;
    let midDark = 0;
    let outerTotal = 0;
    let neutralGray = 0;
    const read = (x, y) => {
      const offset = (y * width + x) * 4;
      return [pixels[offset], pixels[offset + 1], pixels[offset + 2]];
    };
    for (let y = Math.max(0, cy - radius); y <= Math.min(height - 1, cy + radius); y += 1) {
      for (let x = Math.max(0, cx - radius); x <= Math.min(width - 1, cx + radius); x += 1) {
        const dx = x - cx;
        const dy = y - cy;
        if (dx * dx + dy * dy > radius * radius) continue;
        const [r, g, b] = read(x, y);
        total += 1;
        if (r >= 238 && g >= 238 && b >= 238) veryLight += 1;
        if (r >= 70 && r <= 190 && g >= 70 && g <= 190 && b >= 70 && b <= 190) midDark += 1;
      }
    }
    const outerMargin = Math.max(8, Math.floor(Math.min(width, height) / 18));
    const outerRadius = Math.floor(Math.min(width, height) / 2) - outerMargin;
    for (let y = outerMargin; y < height - outerMargin; y += 1) {
      for (let x = outerMargin; x < width - outerMargin; x += 1) {
        const dx = x - cx;
        const dy = y - cy;
        const distanceSq = dx * dx + dy * dy;
        if (distanceSq <= (radius + outerMargin) * (radius + outerMargin)) continue;
        if (distanceSq > outerRadius * outerRadius) continue;
        const [r, g, b] = read(x, y);
        const luma = (r + g + b) / 3;
        outerTotal += 1;
        if (luma >= 78 && luma <= 225 && Math.max(r, g, b) - Math.min(r, g, b) <= 18) neutralGray += 1;
      }
    }
    if (!total) return false;
    const lightRatio = veryLight / total;
    const midRatio = midDark / total;
    const grayRatio = outerTotal ? neutralGray / outerTotal : 0;
    return (
      lightRatio > 0.68 && midRatio > 0.005
    ) || (
      lightRatio > 0.5 && midRatio > 0.002 && grayRatio > 0.08
    );
  } catch {
    return false;
  }
}

function loadQr() {
  if (paymentFinished) return;
  attempts += 1;
  image.onload = () => {
    if (paymentFinished) return;
    if (imageLooksLikeLoadingFrame(image)) {
      loadingFrameAttempts += 1;
      image.hidden = true;
      clearInterval(countdownTimer);
      statusNode.textContent = "官方二维码还在刷新，请稍等...";
      if (loadingFrameAttempts < 20) {
        setTimeout(loadQr, 1000);
      } else {
        fallback("官方二维码长时间未生成，请刷新二维码或重新创建订单");
      }
      return;
    }
    image.hidden = false;
    attempts = 0;
    loadingFrameAttempts = 0;
    qrExpired = false;
    image.style.cursor = "default";
    image.title = "";
    refreshInFlight = false;
    startCountdown(currentServerRemaining());
    pendingRefreshStartedAtMs = 0;
    ensureStatusPolling();
  };
  image.onerror = () => {
    image.hidden = true;
    if (attempts < 5) {
      statusNode.textContent = "收款码正在生成...";
      setTimeout(loadQr, 800);
      return;
    }
    fallback("二维码无法直接显示，请刷新二维码或重新创建订单");
  };
  image.src = `/api/accounts/${encodeURIComponent(accountId)}/recharge/qr?t=${Date.now()}`;
}

function markExpired(message = "二维码已过期，请点击刷新二维码") {
  clearInterval(countdownTimer);
  remainingSeconds = 0;
  qrExpired = true;
  image.style.cursor = "pointer";
  image.title = "二维码已过期，点击刷新";
  statusNode.textContent = message;
  refreshButton.disabled = false;
}

function startCountdown(seconds = 60) {
  if (paymentFinished) return;
  clearInterval(countdownTimer);
  remainingSeconds = normalizedRemainingSeconds(seconds, 60);
  if (remainingSeconds <= 0) {
    markExpired();
    return;
  }
  qrExpired = false;
  image.style.cursor = "default";
  image.title = "";
  refreshButton.disabled = true;
  statusNode.textContent = `请扫码付款，二维码有效期剩余 ${remainingSeconds} 秒`;
  countdownTimer = setInterval(() => {
    remainingSeconds -= 1;
    if (paymentFinished) {
      clearInterval(countdownTimer);
      return;
    }
    if (remainingSeconds > 0) {
      statusNode.textContent = `请扫码付款，二维码有效期剩余 ${remainingSeconds} 秒`;
      return;
    }
    markExpired();
  }, 1000);
}

function syncCountdownFromBackend(value, expiresAtEpoch = 0, serverTimeEpoch = 0) {
  if (paymentFinished || qrExpired) return;
  setServerExpiry(value, expiresAtEpoch, serverTimeEpoch, remainingSeconds);
  const next = currentServerRemaining();
  if (next <= 0) {
    markExpired();
    return;
  }
  if (Math.abs(next - remainingSeconds) < 3) return;
  startCountdown(currentServerRemaining());
}

async function pollPaymentStatus() {
  if (!accountId || paymentFinished || refreshInFlight || qrExpired || statusInFlight) return;
  statusInFlight = true;
  try {
    const response = await fetch(`/api/accounts/${encodeURIComponent(accountId)}/recharge/status?t=${Date.now()}`, {
      cache: "no-store",
      headers: { "Accept": "application/json" },
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) return;
    if (["paid", "success", "completed"].includes(result.status)) {
      paymentFinished = true;
      clearInterval(countdownTimer);
      clearInterval(statusTimer);
      refreshButton.disabled = true;
      closeButton.textContent = "关闭";
      statusNode.textContent = result.message || "支付成功，充值状态已回传";
      notifyPaymentStatus(result.status, statusNode.textContent);
      return;
    }
    if (result.qr_remaining_seconds !== undefined || result.qr_expires_in !== undefined) {
      syncCountdownFromBackend(result.qr_remaining_seconds ?? result.qr_expires_in, result.qr_expires_at_epoch, result.qr_server_time);
    }
    if (result.status === "expired") {
      markExpired(result.message || "二维码已过期，请点击刷新二维码");
    }
  } catch {
  } finally {
    statusInFlight = false;
  }
}

async function refreshQr() {
  if (paymentFinished || refreshInFlight || !accountId || !qrExpired) return;
  refreshInFlight = true;
  refreshButton.disabled = true;
  qrExpired = false;
  pendingRefreshStartedAtMs = Date.now();
  image.style.cursor = "wait";
  image.title = "";
  statusNode.textContent = "正在刷新官方二维码...";
  try {
    const response = await fetch(`/api/accounts/${encodeURIComponent(accountId)}/recharge/qr/refresh`, {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.detail || "二维码刷新失败");
    attempts = 0;
    loadingFrameAttempts = 0;
    image.hidden = true;
    setServerExpiry(result.qr_remaining_seconds ?? result.qr_expires_in, result.qr_expires_at_epoch, result.qr_server_time, 60);
    if (pendingRefreshStartedAtMs) {
      serverExpiresAtMs = Math.min(serverExpiresAtMs, pendingRefreshStartedAtMs + 60000);
    }
    loadQr();
  } catch (error) {
    refreshInFlight = false;
    pendingRefreshStartedAtMs = 0;
    statusNode.textContent = error.message || "二维码刷新失败";
    qrExpired = true;
    image.style.cursor = "pointer";
    image.title = "点击重试刷新";
    refreshButton.disabled = false;
  }
}

refreshButton.onclick = refreshQr;

image.onclick = () => {
  if (!qrExpired || paymentFinished) return;
  refreshQr();
};

closeButton.onclick = () => {
  clearInterval(countdownTimer);
  clearInterval(statusTimer);
  parent.postMessage({ type: "ctyun-close-recharge" }, location.origin);
};

renderPaymentAmount();

if (accountId) {
  loadQr();
} else {
  fallback("缺少充值账号信息");
}

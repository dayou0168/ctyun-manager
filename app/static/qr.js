const params = new URLSearchParams(location.search);
const accountId = params.get("account_id");
const paymentMethod = params.get("payment_method") || "wechat";
const paymentMethodNames = {
  wechat: "微信支付",
  alipay: "支付宝支付",
  bestpay: "翼支付",
};
const image = document.querySelector("#qrImage");
const statusNode = document.querySelector("#qrStatus");
const refreshButton = document.querySelector("#refreshQrBtn");
const closeButton = document.querySelector("#closeQrBtn");
document.querySelector("#paymentMethodTitle").textContent = paymentMethodNames[paymentMethod] || "扫码支付";
let attempts = 0;
let countdownTimer = null;
let statusTimer = null;
let remainingSeconds = 60;
let paymentFinished = false;
let loadingFrameAttempts = 0;

function fallback(message) {
  clearInterval(countdownTimer);
  clearInterval(statusTimer);
  image.hidden = true;
  statusNode.textContent = message;
  parent.postMessage({ type: "ctyun-qr-fallback", message }, location.origin);
}

function notifyPaymentStatus(status, message) {
  parent.postMessage({ type: "ctyun-recharge-status", status, message }, location.origin);
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
        fallback("官方二维码长时间未生成，已切换到 VNC");
      }
      return;
    }
    image.hidden = false;
    attempts = 0;
    loadingFrameAttempts = 0;
    startCountdown();
  };
  image.onerror = () => {
    image.hidden = true;
    if (attempts < 5) {
      statusNode.textContent = "收款码正在生成...";
      setTimeout(loadQr, 800);
      return;
    }
    fallback("二维码无法直接显示，已切换到 VNC");
  };
  image.src = `/api/accounts/${encodeURIComponent(accountId)}/recharge/qr?t=${Date.now()}`;
}

function startCountdown() {
  if (paymentFinished) return;
  clearInterval(countdownTimer);
  remainingSeconds = 60;
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
    clearInterval(countdownTimer);
    statusNode.textContent = "二维码已过期，请点击刷新二维码";
    refreshButton.disabled = false;
  }, 1000);
}

async function pollPaymentStatus() {
  if (!accountId || paymentFinished) return;
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
    if (result.status === "expired") {
      clearInterval(countdownTimer);
      refreshButton.disabled = false;
      statusNode.textContent = result.message || "二维码已过期，请点击刷新二维码";
      notifyPaymentStatus(result.status, statusNode.textContent);
    }
  } catch {}
}

refreshButton.onclick = async () => {
  if (paymentFinished) return;
  refreshButton.disabled = true;
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
    loadQr();
  } catch (error) {
    statusNode.textContent = error.message || "二维码刷新失败";
    refreshButton.disabled = false;
  }
};

closeButton.onclick = () => {
  clearInterval(countdownTimer);
  clearInterval(statusTimer);
  parent.postMessage({ type: "ctyun-close-recharge" }, location.origin);
};

if (accountId) {
  loadQr();
  pollPaymentStatus();
  statusTimer = setInterval(pollPaymentStatus, 3500);
} else {
  fallback("缺少充值账号信息，已切换到 VNC");
}

import RFB from "/novnc/core/rfb.js";

const statusNode = document.querySelector("#status");
const statusText = document.querySelector("#statusText");
const screen = document.querySelector("#screen");
const focusButton = document.querySelector("#focusBtn");
const fitButton = document.querySelector("#fitBtn");
let rfb = null;
let reconnectTimer = null;
let disconnectedByPage = false;
const viewerMode = new URLSearchParams(location.search).get("mode") || "console";
let paymentFocus = viewerMode === "payment";
if (viewerMode !== "payment") {
  focusButton.hidden = true;
}

function setStatus(message, type = "") {
  statusText.textContent = message;
  statusNode.className = type;
}

function websocketUrl() {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${location.host}/websockify`;
}

async function checkHealth() {
  try {
    const response = await fetch("/api/vnc/health", { cache: "no-store" });
    const result = await response.json();
    if (!result.ready) throw new Error(result.message || "VNC 服务未启动");
    return true;
  } catch (error) {
    setStatus(error.message || "VNC 服务未启动", "error");
    return false;
  }
}

async function connect() {
  clearTimeout(reconnectTimer);
  if (!(await checkHealth())) {
    reconnectTimer = setTimeout(connect, 3000);
    return;
  }

  try {
    setStatus("正在连接浏览器会话...");
    rfb = new RFB(screen, websocketUrl(), { shared: true });
    rfb.viewOnly = false;
    applyViewMode();
    rfb.addEventListener("connect", () => {
      setStatus("浏览器会话已连接", "connected");
      applyViewMode();
      setTimeout(centerPaymentArea, 150);
      setTimeout(centerPaymentArea, 900);
    });
    rfb.addEventListener("credentialsrequired", () => rfb.sendCredentials({ password: "" }));
    rfb.addEventListener("securityfailure", (event) => {
      setStatus(event.detail?.reason || "VNC 安全协商失败", "error");
    });
    rfb.addEventListener("disconnect", (event) => {
      rfb = null;
      if (disconnectedByPage) return;
      setStatus(event.detail?.clean ? "浏览器会话已断开，正在重连..." : "VNC 连接失败，正在重连...", "error");
      reconnectTimer = setTimeout(connect, 2000);
    });
  } catch (error) {
    setStatus(error.message || "VNC 连接失败", "error");
    reconnectTimer = setTimeout(connect, 3000);
  }
}

function centerPaymentArea() {
  if (!paymentFocus) return;
  const maxLeft = Math.max(0, screen.scrollWidth - screen.clientWidth);
  const maxTop = Math.max(0, screen.scrollHeight - screen.clientHeight);
  screen.scrollLeft = Math.round(maxLeft / 2);
  screen.scrollTop = Math.min(maxTop, Math.max(180, Math.round(maxTop * 0.45)));
}

function applyViewMode() {
  focusButton.classList.toggle("active", paymentFocus);
  fitButton.classList.toggle("active", !paymentFocus);
  screen.classList.toggle("fit", !paymentFocus);
  if (!rfb) return;
  rfb.scaleViewport = !paymentFocus;
  rfb.clipViewport = paymentFocus;
  rfb.resizeSession = false;
  if (paymentFocus) requestAnimationFrame(centerPaymentArea);
}

focusButton.onclick = () => {
  paymentFocus = true;
  applyViewMode();
  setTimeout(centerPaymentArea, 150);
};

fitButton.onclick = () => {
  paymentFocus = false;
  applyViewMode();
};

window.addEventListener("resize", () => setTimeout(centerPaymentArea, 100));

window.addEventListener("beforeunload", () => {
  disconnectedByPage = true;
  clearTimeout(reconnectTimer);
  if (rfb) rfb.disconnect();
});

connect();

import crypto from "node:crypto";
import QRCode from "qrcode";

export const RECHARGE_URL = "https://www.ctyun.cn/console/expense/fund/recharge";
export const RECHARGE_CREATE_URL = "https://www.ctyun.cn/gw/account/cash/Recharge";
export const RECHARGE_FRONT_URL = "https://www.ctyun.cn/virtual/redirect/funddetail";
export const CHECKSTAND_PAY_URL = "https://www.ctyun.cn/checkstand/webpay/pcCheckstand";
export const CHECKSTAND_GET_PAY_CHANNELS_URL = "https://www.ctyun.cn/checkstand/unifyapi/getPayChannels";
export const CHECKSTAND_PRECREATE_URL = "https://www.ctyun.cn/checkstand/unifyapi/upayprecreate";
export const CHECKSTAND_QUERY_URL = "https://www.ctyun.cn/checkstand/unifyapi/upayquery";

const CHECKSTAND_RSA_PUBLIC_KEY = [
  "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCnipScOLtgdPUYuhEcQEY7serbe1KCw10GXH3Bt+2SgeQE4KG5M26",
  "ixYIzT/wpKZFOtESuq6pZXpHN05HUK0FM/wCb28dBVH2aGZ+QSdR/z7aeitHTlR44FfsRSNhJulVbrioYSv55CDtvi7",
  "SEXRrtNHJU3hEbpgUnbL/cc/3QbwIDAQAB",
].join("");
const PUBLIC_KEY = `-----BEGIN PUBLIC KEY-----\n${CHECKSTAND_RSA_PUBLIC_KEY}\n-----END PUBLIC KEY-----\n`;
const CHANNELS = { alipay: ["7", "支付宝"], wechat: ["8", "微信支付"], bestpay: ["9", "翼支付"] };
const SUCCESS = new Set(["SUCCESS", "TRADE_SUCCESS", "PAY_SUCCESS", "PAID", "PAYED", "FINISHED", "COMPLETED"]);
const FAILED = new Set(["FAIL", "FAILED", "TRADE_CLOSED", "CLOSED", "CANCEL", "CANCELLED", "PAYERROR"]);
const QR_VALID_MS = 60_000;

export function amountToCents(value) {
  const text = String(value ?? "").trim();
  if (!/^(?:0|[1-9]\d*)(?:\.\d{1,2})?$/.test(text)) throw new Error("充值金额格式无效");
  const [whole, fraction = ""] = text.split(".");
  const cents = BigInt(whole) * 100n + BigInt((fraction + "00").slice(0, 2));
  if (cents <= 0n) throw new Error("充值金额必须大于 0");
  return cents.toString();
}

export function checkstandTransform(params) {
  const encodeKey = key => encodeURIComponent(key).replace(/[!'()*]/g, char => `%${char.charCodeAt(0).toString(16).toUpperCase()}`);
  return Object.entries(params).map(([key, value]) => `${encodeKey(key)}=${value === null ? "null" : `"${value}"`}`).join("&");
}

export function checkstandSign(params) {
  const digest = crypto.createHash("sha256").update(`&${checkstandTransform(params)}`, "utf8").digest("hex");
  return crypto.publicEncrypt({ key: PUBLIC_KEY, padding: crypto.constants.RSA_PKCS1_PADDING }, Buffer.from(digest, "utf8")).toString("base64");
}

export function findDeep(value, keys) {
  if (Array.isArray(value)) {
    for (const item of value) { const found = findDeep(item, keys); if (found !== undefined) return found; }
  } else if (value && typeof value === "object") {
    for (const key of keys) if (value[key] !== undefined && value[key] !== null) return value[key];
    for (const item of Object.values(value)) { const found = findDeep(item, keys); if (found !== undefined) return found; }
  }
}

export function extractPaymentURL(data) {
  const candidate = typeof data === "string" ? data : findDeep(data, ["nextUrl", "next_url", "payUrl", "pay_url", "redirectUrl", "redirect_url", "url"]);
  let text = String(candidate || "").trim();
  if (text.startsWith("//")) text = `https:${text}`;
  try { text = new URL(text, "https://www.ctyun.cn").toString(); } catch { return ""; }
  return text.includes("ctyun.cn/checkstand/") || text.includes("checkstand") ? text : "";
}

function timing(payment) {
  const remaining = Math.max(0, Math.ceil((Number(payment.qr_expires_at_ms || 0) - Date.now()) / 1000));
  return { qr_remaining_seconds: remaining, qr_expires_in: remaining, qr_server_time: Date.now() / 1000, qr_expires_at_epoch: Number(payment.qr_expires_at_ms || Date.now()) / 1000 };
}

function parseCheckout(url) {
  const parsed = new URL(url);
  const result = Object.fromEntries(parsed.searchParams.entries());
  if (result.sign) result.sign = result.sign.replaceAll(" ", "+");
  return result;
}

export class RechargeService {
  constructor(requestJSON) { this.requestJSON = requestJSON; this.payments = new Map(); this.locks = new Map(); }

  async locked(accountID, fn) {
    const key = String(accountID); const previous = this.locks.get(key) || Promise.resolve();
    let release; const current = new Promise(resolve => { release = resolve; }); this.locks.set(key, current);
    await previous.catch(() => {});
    try { return await fn(); } finally { release(); if (this.locks.get(key) === current) this.locks.delete(key); }
  }

  async order(account, amount, paymentMethod) {
    return this.locked(account.id, async () => {
      const cents = amountToCents(amount);
      const create = new URL(RECHARGE_CREATE_URL);
      create.search = new URLSearchParams({ amount: cents, frontUrl: RECHARGE_FRONT_URL, platform: "1" }).toString();
      const data = await this.requestJSON(account, create.toString(), "GET", undefined, RECHARGE_URL);
      const paymentURL = extractPaymentURL(data);
      if (!paymentURL) throw new Error(String(findDeep(data, ["message", "msg", "reason", "errorMsg", "error"]) || "官方未返回收银台地址"));
      const query = parseCheckout(paymentURL);
      const providerAccountID = query.account_id || ""; const orderNo = query.out_trade_no || "";
      if (!providerAccountID || !orderNo) throw new Error("官方收银台地址缺少 account_id 或 out_trade_no");
      const channelData = await this.requestJSON(account, CHECKSTAND_GET_PAY_CHANNELS_URL, "POST", { ...query, unipayUrl: CHECKSTAND_PAY_URL }, paymentURL);
      if (channelData?.success === false) throw new Error(String(channelData.errorMsg || "官方支付渠道查询失败"));
      const payment = {
        provider_account_id: providerAccountID, out_trade_no: orderNo, order_no: orderNo, amount,
        total_amount: query.total_amount || cents, back_url: query.back_url || "", body: query.body || "",
        front_url: query.front_url || RECHARGE_FRONT_URL, order_cancel_time: query.order_cancel_time || "",
        order_type: query.order_type || "2", subject: query.subject || "", platform: channelData?.result?.platform || 1,
        url: paymentURL, cookie_payment: true,
      };
      this.payments.set(String(account.id), payment);
      const activated = await this.activateUnlocked(account, paymentMethod, false);
      return {
        status: "ready", message: `官方后台 cookie 下单成功：¥${amount}`, url: paymentURL, order_no: orderNo,
        provider_account_id: providerAccountID, amount, cookie_payment: true, fast_path: true,
        payment_status: activated.status, payment_message: activated.message, payment_method: activated.payment_method,
        qr_available: Boolean(activated.qr_available), qr_cached: Boolean(activated.qr_cached),
        ...timing(payment), storage_state: activated.storage_state,
      };
    });
  }

  async activate(account, method) { return this.locked(account.id, () => this.activateUnlocked(account, method, false)); }

  async activateUnlocked(account, method, refresh) {
    const channel = CHANNELS[String(method || "").toLowerCase()];
    if (!channel) return { status: "payment_method_error", message: "不支持的支付方式", payment_method: method };
    const payment = this.payments.get(String(account.id));
    if (!payment) return { status: "payment_session_error", message: "当前账号没有后台支付订单", payment_method: method };
    const params = {
      accountId: payment.provider_account_id, backUrl: payment.back_url, body: payment.body, frontUrl: payment.front_url,
      orderCancelTime: payment.order_cancel_time, orderType: payment.order_type, outTradeNo: payment.out_trade_no,
      payChannel: channel[0], platform: payment.platform, totalAmount: payment.total_amount, subject: payment.subject,
    };
    const request = { ...params, ...(refresh ? { refreshFlag: 1 } : {}), sign: checkstandSign(params) };
    const data = await this.requestJSON(account, CHECKSTAND_PRECREATE_URL, "POST", request, payment.url || CHECKSTAND_PAY_URL);
    if (data?.success !== true) return { status: "payment_method_error", message: String(data?.errorMsg || "官方二维码生成失败"), payment_method: method, cookie_payment: true };
    const result = data.result && typeof data.result === "object" ? data.result : {}; const qrCode = String(result.qrCode || "");
    if (!qrCode) return { status: "qr_unavailable", message: "官方接口未返回二维码内容", payment_method: method, cookie_payment: true };
    Object.assign(payment, { payment_method: method, pay_channel: channel[0], qr_code: qrCode, trade_no: String(result.tradeNo || ""), out_trade_no: String(result.outTradeNo || payment.out_trade_no), order_no: String(result.outTradeNo || payment.order_no), qr_expires_at_ms: Date.now() + QR_VALID_MS });
    delete payment.qr_png_base64; this.payments.set(String(account.id), payment);
    return { status: "ready", message: `${channel[1]}收款信息已通过后台 cookie 加载`, payment_method: method, qr_available: true, qr_cached: false, url: payment.url, order_no: payment.order_no, provider_account_id: payment.provider_account_id, amount: payment.amount, ...timing(payment), cookie_payment: true };
  }

  async qr(account) {
    return this.locked(account.id, async () => {
      const payment = this.payments.get(String(account.id));
      if (!payment?.qr_code) return { status: "qr_unavailable", message: "当前支付方式没有生成二维码" };
      payment.qr_png_base64 ||= (await QRCode.toBuffer(payment.qr_code, { type: "png", errorCorrectionLevel: "M", margin: 3, scale: 8 })).toString("base64");
      return { status: "ready", message: "收款码已通过后台 cookie 生成", png_base64: payment.qr_png_base64 };
    });
  }

  async refresh(account) {
    return this.locked(account.id, async () => {
      const payment = this.payments.get(String(account.id));
      if (!payment) return { status: "qr_unavailable", message: "当前账号没有支付会话" };
      const previousQR = payment.qr_code || ""; const previousTrade = payment.trade_no || ""; const started = Date.now(); let result;
      for (let attempt = 1; attempt <= 2; attempt++) {
        result = await this.activateUnlocked(account, payment.payment_method || "wechat", true);
        if (result.status !== "ready") return result;
        if (!previousQR || payment.qr_code !== previousQR || (payment.trade_no && payment.trade_no !== previousTrade)) return { ...result, message: "官方二维码已通过后台 cookie 刷新", refresh_attempts: attempt, refresh_elapsed_ms: Date.now() - started, ...timing(payment) };
        if (attempt === 1) await new Promise(resolve => setTimeout(resolve, 500));
      }
      return { ...result, status: "qr_unchanged", message: "官方返回的二维码内容暂未变化，请稍后再点刷新", refresh_attempts: 2, refresh_elapsed_ms: Date.now() - started, ...timing(payment) };
    });
  }

  async status(account) {
    return this.locked(account.id, async () => {
      const payment = this.payments.get(String(account.id));
      if (!payment) return { status: "no_payment_page", message: "当前没有后台支付订单" };
      let data;
      try { data = await this.requestJSON(account, CHECKSTAND_QUERY_URL, "POST", { accountId: payment.provider_account_id, outTradeNo: payment.out_trade_no }, payment.url || CHECKSTAND_PAY_URL); }
      catch (error) { return { status: "unknown", message: `官方支付状态查询暂时无响应，稍后自动重试：${error.message}`, trade_status: "", order_no: payment.out_trade_no, ...timing(payment), cookie_payment: true }; }
      const trade = String(data?.result?.tradeStatus || data?.tradeStatus || "").trim().toUpperCase().replaceAll("-", "_");
      const common = { trade_status: trade, order_no: payment.out_trade_no, ...timing(payment), cookie_payment: true };
      if (data?.success === true && SUCCESS.has(trade)) return { status: "paid", message: "官方支付状态接口显示支付成功", ...common };
      if (data?.success === true && FAILED.has(trade)) return { status: "failed", message: "官方支付状态接口显示支付失败", ...common };
      if (timing(payment).qr_remaining_seconds <= 0 && payment.qr_code) return { status: "expired", message: "二维码已过期，请刷新二维码", ...common, qr_remaining_seconds: 0, qr_expires_in: 0 };
      if (data?.success === false) return { status: "unknown", message: String(data.errorMsg || "官方支付状态接口查询失败"), ...common };
      return { status: "pending", message: "官方支付状态接口显示等待付款", ...common, trade_status: trade || "NOTPAY" };
    });
  }

  close(accountID) { this.payments.delete(String(accountID)); this.locks.delete(String(accountID)); }
}

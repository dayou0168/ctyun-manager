import assert from "node:assert/strict";
import test from "node:test";
import { amountToCents, checkstandSign, checkstandTransform, extractPaymentURL, RechargeService } from "./recharge.mjs";

test("amountToCents preserves decimal values without float rounding", () => {
  assert.equal(amountToCents("0.01"), "1");
  assert.equal(amountToCents("100.20"), "10020");
  assert.throws(() => amountToCents("0"));
  assert.throws(() => amountToCents("1.001"));
});

test("checkstand transform is compatible and produces an RSA signature", () => {
  const params = { accountId: "a b", empty: "", missing: null };
  assert.equal(checkstandTransform(params), 'accountId="a b"&empty=""&missing=null');
  assert.ok(Buffer.from(checkstandSign(params), "base64").length > 100);
});

test("payment URL extraction rejects an unrelated redirect", () => {
  assert.match(extractPaymentURL({ result: { nextUrl: "//www.ctyun.cn/checkstand/webpay/pcCheckstand?a=1" } }), /^https:/);
  assert.equal(extractPaymentURL({ url: "https://example.com/pay" }), "");
});

test("cookie recharge lifecycle uses only injected official requests", async () => {
  const calls = [];
  const service = new RechargeService(async (_account, url, method, data) => {
    calls.push({ url, method, data });
    if (url.includes("/cash/Recharge")) return { nextUrl: "https://www.ctyun.cn/checkstand/webpay/pcCheckstand?account_id=acct&out_trade_no=order&total_amount=123&body=b&subject=s" };
    if (url.includes("getPayChannels")) return { success: true, result: { platform: 1 } };
    if (url.includes("upayprecreate")) return { success: true, result: { qrCode: "https://pay.example/qr", tradeNo: "trade", outTradeNo: "order" } };
    if (url.includes("upayquery")) return { success: true, result: { tradeStatus: "NOTPAY" } };
    throw new Error("unexpected request");
  });
  const account = { id: 7 };
  const order = await service.order(account, "1.23", "wechat");
  assert.equal(order.status, "ready"); assert.equal(order.qr_available, true);
  const qr = await service.qr(account); assert.equal(qr.status, "ready"); assert.ok(Buffer.from(qr.png_base64, "base64").subarray(1, 4).toString() === "PNG");
  const status = await service.status(account); assert.equal(status.status, "pending");
  assert.equal(calls.length, 4);
  service.close(account.id); assert.equal((await service.status(account)).status, "no_payment_page");
});

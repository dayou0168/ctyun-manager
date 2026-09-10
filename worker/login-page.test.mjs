import assert from "node:assert/strict";
import test from "node:test";
import { firstVisible, formatOfficialFeedback, isLoginURL, isSecurityChallengeText, loginFailureMessage, waitForVisibleText } from "./login-page.mjs";

test("normal SMS login copy is not treated as a security challenge", () => {
  assert.equal(isSecurityChallengeText("短信登录 请输入短信验证码 获取验证码"), false);
  assert.equal(isSecurityChallengeText("请完成滑块验证"), true);
  assert.equal(isSecurityChallengeText("访问异常，请完成人机验证"), true);
});

test("login URL recognition covers the current auth route", () => {
  assert.equal(isLoginURL("https://www.ctyun.cn/h5/auth/login"), true);
  assert.equal(isLoginURL("https://console.ctyun.cn/compute/index/"), false);
});

test("login errors are classified without exposing page contents", () => {
  assert.deepEqual(loginFailureMessage("用户名或密码错误"), {
    status: "login_failed",
    message: "天翼云拒绝了账号密码，请检查平台内保存的登录资料",
  });
  assert.equal(loginFailureMessage("欢迎登录天翼云"), null);
});

test("official feedback formatting includes only sanitized response fields", () => {
  assert.equal(formatOfficialFeedback([{ path: "POST /gw/auth/Login", http_status: 200, code: "E1", message: "密码错误" }]), "POST /gw/auth/Login HTTP 200 code=E1 message=密码错误");
});

test("firstVisible accepts duplicate locators and selects the visible field", async () => {
  const candidates = [
    { isVisible: async () => false },
    { isVisible: async () => true },
  ];
  const locator = { count: async () => candidates.length, nth: (index) => candidates[index] };
  assert.equal(await firstVisible(locator), candidates[1]);
});

test("waitForVisibleText tolerates an asynchronously rendered login tab", async () => {
  let rendered = false;
  const candidate = { isVisible: async () => rendered };
  const locator = { count: async () => 1, nth: () => candidate };
  const page = {
    getByText: () => locator,
    waitForTimeout: async () => { rendered = true; },
  };
  assert.equal(await waitForVisibleText(page, "账号登录", 1000), candidate);
});

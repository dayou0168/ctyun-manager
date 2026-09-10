import assert from "node:assert/strict";
import test from "node:test";
import { firstVisible, isLoginURL, isSecurityChallengeText, waitForVisibleText } from "./login-page.mjs";

test("normal SMS login copy is not treated as a security challenge", () => {
  assert.equal(isSecurityChallengeText("短信登录 请输入短信验证码 获取验证码"), false);
  assert.equal(isSecurityChallengeText("请完成滑块验证"), true);
  assert.equal(isSecurityChallengeText("访问异常，请完成人机验证"), true);
});

test("login URL recognition covers the current auth route", () => {
  assert.equal(isLoginURL("https://www.ctyun.cn/h5/auth/login"), true);
  assert.equal(isLoginURL("https://console.ctyun.cn/compute/index/"), false);
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

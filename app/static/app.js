const state = {
  accounts: [],
  view: "dashboard",
  resources: {},
  resourceOverrides: new Map(),
  pendingAction: null,
  pendingFieldsAction: null,
  imageTab: "private",
  mode: "openapi",
  refreshTimer: null,
  refreshing: false,
  refreshQueued: false,
  lastRefreshAt: null,
  openRegions: new Set(),
  openAccounts: new Set(),
  version: "",
  lastSyncByScope: new Map(),
  encryptionKeyStatus: "",
  rechargeAccountId: 0,
  paymentUrl: "",
  rechargeMode: "",
  rechargeClosing: false,
  rechargeToken: 0,
  rechargePrewarmQueued: false,
  rechargeProgressTimer: 0,
  rustdeskJobId: localStorage.getItem("ctyun:rustdeskJobId") || "",
  rustdeskPollTimer: 0,
  pendingFieldDefinitions: [],
  pendingFieldOptions: new Map(),
  pendingFieldGroupedOptions: new Map(),
  pendingFieldLoadSeq: new Map(),
  pendingFieldDependencySig: new Map(),
  groupedSelectFilters: new Map(),
  apiCache: new Map(),
  apiInflight: new Map(),
  soldOutFlavorKeys: new Set(),
  eipPriceSeq: 0,
  ecsPriceSeq: 0,
  loadingDepth: 0,
  cacheEpoch: 0,
  renderSeq: 0,
  manualSyncing: false,
  viewSwitchUntil: 0,
  ecsPrewarmKeys: new Set(),
  ecsPrewarmQueue: new Map(),
  ecsPrewarmTimer: 0,
  lastEcsRegionByAccount: new Map(JSON.parse(localStorage.getItem("ctyun:lastEcsRegions") || "[]")),
  flavorStockRefreshKeys: new Set(),
  postActionSyncTimers: new Map(),
  unavailableImageKeys: new Set(),
  totpCodes: new Map(),
  totpTimer: 0,
  totpRefreshing: new Set(),
  initializing: true,
  ikuaiGatewayId: Number(localStorage.getItem("ctyun:ikuaiGatewayId") || 0),
  ikuaiSection: localStorage.getItem("ctyun:ikuaiSection") || "homepage",
  ikuaiRows: [],
  ikuaiMatched: null,
  ikuaiMenuGroups: [],
};
const $ = (s) => document.querySelector(s);
const content = $("#content");
const resourceCacheTypes = ["ecs", "eip", "vpc", "subnet", "vip", "image", "security_group"];

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    cache: "no-store",
    ...options,
  });
  if (response.status === 401) {
    showLogin();
    throw new Error("请重新登录");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || "请求失败");
  return data;
}

async function cachedApi(path, ttl = 20000) {
  const now = Date.now();
  const cached = state.apiCache.get(path);
  if (cached && cached.expiresAt > now) return cached.data;
  const inflight = state.apiInflight.get(path);
  if (inflight) return inflight;
  const epoch = state.cacheEpoch;
  const request = api(path)
    .then((data) => {
      if (epoch === state.cacheEpoch) {
        state.apiCache.set(path, { data, expiresAt: Date.now() + ttl });
      }
      return data;
    })
    .finally(() => state.apiInflight.delete(path));
  state.apiInflight.set(path, request);
  return request;
}

function hasFreshCache(path) {
  const cached = state.apiCache.get(path);
  return Boolean(cached && cached.expiresAt > Date.now());
}

function clearApiCache(prefix = "") {
  state.cacheEpoch += 1;
  if (!prefix) {
    state.apiCache.clear();
    state.apiInflight.clear();
    return;
  }
  [...state.apiCache.keys()].forEach((key) => {
    if (key.startsWith(prefix)) state.apiCache.delete(key);
  });
  [...state.apiInflight.keys()].forEach((key) => {
    if (key.startsWith(prefix)) state.apiInflight.delete(key);
  });
}

function clearResourceCaches(types = resourceCacheTypes) {
  [...new Set(types)].forEach((type) => clearApiCache(`/api/resources/${type}`));
  clearApiCache("/api/operations");
}

function clearActionCaches(resourceType, action) {
  clearResourceCaches(resourceTypesAfterAction(resourceType, action));
}

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(node.timer);
  node.timer = setTimeout(() => node.classList.remove("show"), 2600);
}

function showLogin() {
  $("#loginView").classList.remove("hidden");
  $("#appView").classList.add("hidden");
}

function showApp() {
  $("#loginView").classList.add("hidden");
  $("#appView").classList.remove("hidden");
  $("#modeBadge").textContent = "正式 OpenAPI";
  $("#modeBadge").title = "当前不会生成模拟资源，同步失败会显示天翼云 OpenAPI 的真实错误。";
  $("#modeBadge").classList.remove("key-warning");
  renderVersion();
}

function renderVersion() {
  const text = state.version ? `构建 ${state.version}` : "版本未知";
  const loginVersion = $("#loginVersion");
  const appVersion = $("#appVersion");
  if (loginVersion) loginVersion.textContent = text;
  if (appVersion) appVersion.textContent = text;
}

function updateRefreshBadge(text = "") {
  const badge = $("#refreshBadge");
  if (!badge) return;
  if (text) {
    badge.textContent = text;
    return;
  }
  badge.textContent = state.lastRefreshAt ? `最后刷新 ${new Date(state.lastRefreshAt).toLocaleTimeString()}` : "未刷新";
}

function setPageLoading(loading, message = "正在加载...") {
  const loader = $("#pageLoading");
  const main = document.querySelector("main");
  if (!loader || !main) return;
  state.loadingDepth = Math.max(0, state.loadingDepth + (loading ? 1 : -1));
  if (loading) {
    loader.querySelector("span").textContent = message;
    loader.classList.remove("hidden");
    main.classList.add("view-loading");
    $("#content")?.setAttribute("aria-busy", "true");
    return;
  }
  if (state.loadingDepth > 0) return;
  loader.classList.add("hidden");
  main.classList.remove("view-loading");
  const node = $("#content");
  node?.removeAttribute("aria-busy");
  node?.classList.remove("content-enter");
  if (node) {
    void node.offsetWidth;
    node.classList.add("content-enter");
    window.setTimeout(() => node.classList.remove("content-enter"), 260);
  }
}

function resetPageLoading() {
  state.loadingDepth = 0;
  const loader = $("#pageLoading");
  const main = document.querySelector("main");
  loader?.classList.add("hidden");
  main?.classList.remove("view-loading");
  $("#content")?.removeAttribute("aria-busy");
}

function nextFrame() {
  return new Promise((resolve) => {
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      resolve();
    };
    requestAnimationFrame(finish);
    window.setTimeout(finish, 80);
  });
}

function wait(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function withPageLoading(message, callback) {
  setPageLoading(true, message);
  await nextFrame();
  try {
    return await callback();
  } finally {
    setPageLoading(false);
  }
}

function accountName(id) {
  return state.accounts.find((a) => a.id === id)?.name || `账号 ${id}`;
}

function selectedAccountId() {
  return Number($("#accountFilter").value || 0);
}

async function loadAccounts() {
  state.accounts = await api("/api/accounts");
  clearApiCache();
  const select = $("#accountFilter");
  const current = select.value;
  select.innerHTML = `<option value="">全部账号</option>` + state.accounts.map((a) => `<option value="${a.id}">${escapeHtml(a.name)}</option>`).join("");
  select.value = current;
}

function triggerRechargePrewarm() {
  if (state.rechargePrewarmQueued || !state.accounts.length) return;
  state.rechargePrewarmQueued = true;
  api("/api/recharge/prewarm", { method: "POST" }).catch(() => {}).finally(() => {
    state.rechargePrewarmQueued = false;
  });
}

function accountById(id) {
  return state.accounts.find((a) => a.id === Number(id));
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[c]));
}

const statusLabels = {
  running: "运行中",
  started: "运行中",
  active: "运行中",
  available: "可用",
  ready: "正常",
  success: "成功",
  succeeded: "成功",
  partial: "部分完成",
  skipped: "已跳过",
  ok: "正常",
  normal: "正常",
  bound: "已绑定",
  inuse: "使用中",
  in_use: "使用中",
  using: "使用中",
  used: "已使用",
  stopped: "已关机",
  stop: "已关机",
  shutoff: "已关机",
  closed: "已关闭",
  unbound: "未绑定",
  free: "未绑定",
  creating: "创建中",
  building: "创建中",
  starting: "开机中",
  stopping: "关机中",
  rebooting: "重启中",
  restarting: "重启中",
  updating: "更新中",
  modifying: "修改中",
  resizing: "变更规格中",
  rebuilding: "重装中",
  resetting: "重置中",
  binding: "绑定中",
  unbinding: "解绑中",
  releasing: "释放中",
  unsubscribing: "退订中",
  deleting: "删除中",
  sharing: "共享中",
  unsharing: "取消共享中",
  accepting: "接受中",
  rejecting: "拒绝中",
  copying: "复制中",
  imaging: "制作镜像中",
  pending: "等待中",
  waiting: "等待中",
  processing: "处理中",
  submitted: "已提交",
  syncing: "同步中",
  synced: "已同步",
  failed: "失败",
  failure: "失败",
  error: "异常",
  fault: "异常",
  abnormal: "异常",
  expired: "已过期",
  disabled: "已停用",
  enabled: "已启用",
  unknown: "未知",
  interactive: "需人工处理",
  finance_error: "余额获取失败",
};

const resourceTypeLabels = {
  ecs: "云主机",
  eip: "弹性 IP",
  vpc: "VPC",
  subnet: "子网",
  vip: "虚拟 IP",
  image: "镜像",
  security_group: "安全组",
  route_table: "路由表",
  acl: "网络 ACL",
  account: "云账号",
  ikuai: "爱快网关",
};

const operationActionLabels = {
  create: "创建",
  update: "更新",
  delete: "删除",
  start: "开机",
  stop: "关机",
  reboot: "重启",
  release: "释放",
  unsubscribe: "退订",
  rename: "改名",
  bind: "绑定",
  unbind: "解绑",
  copy: "复制",
  share: "共享",
  unshare: "取消共享",
  accept: "接受",
  reject: "拒绝",
  resize: "变更规格",
  rebuild: "重装系统",
  reset_password: "重置密码",
  auto_renew: "自动续订",
  deletion_protection: "删除保护",
  create_image: "制作镜像",
  open_console: "打开官方控制台",
  open_recharge: "打开充值页",
  create_subnet: "创建子网",
  create_rule: "添加规则",
  delete_rule: "删除规则",
  change_private_ip: "修改内网 IP",
  change_vpc: "更换 VPC",
  bind_ecs: "绑定云主机",
  bind_eip: "绑定弹性 IP",
  sync: "同步资源",
  auto_sync: "自动同步",
  background_sync: "后台同步",
  create_gateway: "添加网关",
  update_gateway: "更新网关",
  delete_gateway: "删除网关",
  test_gateway: "测试登录",
  refresh_gateway: "刷新状态",
};

function statusKey(value) {
  const raw = String(value || "unknown").trim().toLowerCase();
  return raw.replace(/[^a-z0-9_-]+/g, "_") || "unknown";
}

function statusLabel(value, label = "") {
  if (label) return label;
  const raw = String(value || "").trim();
  if (!raw) return "未知";
  return statusLabels[statusKey(raw)] || raw;
}

function status(value, label = "") {
  return `<span class="status ${escapeHtml(statusKey(value))}">${escapeHtml(statusLabel(value, label))}</span>`;
}

function ecsStatusLabel(value) {
  return statusLabel(value);
}

function resourceTypeLabel(type) {
  return resourceTypeLabels[type] || type || "-";
}

function actionDisplayLabel(resourceType, action) {
  return operationActionLabels[action] || `${resourceTypeLabel(resourceType)}操作`;
}

function panel(title, body, extra = "", className = "") {
  return `<div class="panel ${escapeHtml(className)}"><div class="panel-head"><h3>${title}</h3>${extra ? `<div class="panel-head-actions">${extra}</div>` : ""}</div>${body}</div>`;
}

function regionLabel(row) {
  return row.payload?.region_name || row.payload?.regionName || row.region_name || "未知资源池";
}

function regionKey(row) {
  return row.region || row.payload?.regionID || regionLabel(row);
}

function groupByRegion(rows) {
  return rows.reduce((groups, row) => {
    const key = regionKey(row);
    if (!groups[key]) groups[key] = { label: regionLabel(row), rows: [] };
    groups[key].rows.push(row);
    return groups;
  }, {});
}

function regionGroup(title, count, inner, key = title) {
  const meta = typeof count === "number" ? `${count} 项资源` : count;
  const storageKey = `${state.view}:${selectedAccountId() || "all"}:${key}`;
  return `<details class="region-section" data-region-key="${escapeHtml(storageKey)}" ${state.openRegions.has(storageKey) ? "open" : ""}>
    <summary class="region-title"><strong>${escapeHtml(title)}</strong><span>${escapeHtml(meta)}</span></summary>
    ${inner}
  </details>`;
}

function bindRegionToggles() {
  document.querySelectorAll("[data-region-key]").forEach((details) => details.ontoggle = () => {
    if (details.open) state.openRegions.add(details.dataset.regionKey);
    else state.openRegions.delete(details.dataset.regionKey);
  });
}

function groupByAccount(rows) {
  return rows.reduce((groups, row) => {
    const id = Number(row.account_id);
    if (!groups[id]) groups[id] = [];
    groups[id].push(row);
    return groups;
  }, {});
}

function accountGroup(accountId, count, inner) {
  const key = `${state.view}:${selectedAccountId() || "all"}:${accountId}`;
  return `<details class="account-section" data-account-key="${escapeHtml(key)}" ${state.openAccounts.has(key) ? "open" : ""}>
    <summary class="account-title"><strong>${escapeHtml(accountName(accountId))}</strong><span>${escapeHtml(`${count} 项资源`)}</span></summary>
    ${inner}
  </details>`;
}

function bindAccountToggles() {
  document.querySelectorAll("[data-account-key]").forEach((details) => details.ontoggle = () => {
    if (details.open) state.openAccounts.add(details.dataset.accountKey);
    else state.openAccounts.delete(details.dataset.accountKey);
  });
}

function isActiveRender(seq) {
  return seq === state.renderSeq;
}

async function renderDashboard(seq = state.renderSeq) {
  const [all, balances] = await Promise.all([
    Promise.all(["ecs", "eip", "vpc", "image"].map((type) => cachedApi(`/api/resources/${type}`))),
    cachedApi("/api/finance", 10000),
  ]);
  if (!isActiveRender(seq)) return;
  const balanceByAccount = new Map(balances.map((item) => [Number(item.account_id), item]));
  const hasBalance = (item) => item?.available !== null
    && item?.available !== undefined
    && Number.isFinite(Number(item.available))
    && (item.status === "ready" || Number(item.available) !== 0);
  const knownBalances = balances.filter(hasBalance);
  const totalBalance = knownBalances.reduce((n, b) => n + Number(b.available), 0);
  const balanceText = knownBalances.length ? `¥ ${totalBalance.toFixed(2)}` : "未获取";
  const formatBalance = (item) => hasBalance(item)
    ? `¥ ${Number(item.available).toFixed(2)}`
    : `<span class="muted" title="${escapeHtml(item?.message || "登录态尚未建立")}">未获取</span>`;
  content.innerHTML = `
    <div class="metric-grid">
      <div class="metric"><small>云账号</small><strong>${state.accounts.length}</strong></div>
      <div class="metric"><small>云主机</small><strong>${all[0].length}</strong></div>
      <div class="metric"><small>弹性 IP</small><strong>${all[1].length}</strong></div>
      <div class="metric"><small>账户余额合计</small><strong>${balanceText}</strong></div>
    </div>
    ${panel("账号概览", state.accounts.length ? `<table><thead><tr><th>账号</th><th>区域</th><th>API</th><th>2FA</th><th>余额</th><th>操作</th></tr></thead><tbody>${state.accounts.map((a) => `<tr><td><strong>${escapeHtml(a.name)}</strong></td><td>${escapeHtml(a.region || "全部资源池")}</td><td>${a.ak_masked || "-"}</td><td>${a.has_totp ? "已配置" : "未配置"}</td><td>${formatBalance(balanceByAccount.get(a.id))}</td><td class="actions"><button data-balance="${a.id}">刷新余额</button><button data-recharge="${a.id}">充值</button><button data-sync="${a.id}">同步</button></td></tr>`).join("")}</tbody></table>` : `<div class="empty">先添加一个完整的天翼云账号</div>`)}
  `;
  bindCommonActions();
}

function totpRemaining(item) {
  if (!item?.code) return 0;
  const deadline = Number(item.deadlineMs || 0) || ((item.clientAt || Date.now()) + Number(item.remaining || 30) * 1000);
  return Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
}

function renderTotpInline(account) {
  const item = state.totpCodes.get(Number(account.id));
  if (!account.has_totp) return `<div class="totp-inline muted">未配置 Google 2FA</div>`;
  if (!item?.code) {
    return `<div class="totp-inline"><button type="button" data-totp="${account.id}">查看验证码</button><span>验证码会显示在这里，可单击复制</span></div>`;
  }
  return `
    <div class="totp-inline active">
      <button type="button" class="totp-code" data-copy-totp="${account.id}" title="单击复制当前验证码">${escapeHtml(item.code)}</button>
      <span class="totp-meta">${totpRemaining(item)}s 后刷新，单击复制</span>
    </div>
  `;
}

function updateTotpInline(accountId) {
  const account = accountById(accountId);
  const wrapper = document.querySelector(`[data-totp-wrap="${accountId}"]`);
  if (!account || !wrapper) return;
  wrapper.innerHTML = renderTotpInline(account);
  bindTotpActions();
}

async function showTotpCode(accountId) {
  const id = Number(accountId);
  if (!id || state.totpRefreshing.has(id)) return;
  state.totpRefreshing.add(id);
  try {
    const result = await api(`/api/accounts/${id}/totp`);
    if (!result.code) {
      state.totpCodes.delete(id);
      toast("该账号未配置 2FA");
      return;
    }
    state.totpCodes.set(id, {
      code: result.code,
      period: Number(result.period || 30),
      remaining: Number(result.remaining || 30),
      clientAt: Date.now(),
      deadlineMs: Date.now() + Number(result.remaining || 30) * 1000,
      serverTime: Number(result.server_time || 0),
      expiresAt: Number(result.expires_at || 0),
    });
    ensureTotpTicker();
  } catch (error) {
    toast(error.message);
  } finally {
    state.totpRefreshing.delete(id);
    updateTotpInline(id);
  }
}

async function copyTotpCode(accountId) {
  const item = state.totpCodes.get(Number(accountId));
  if (!item?.code) return showTotpCode(accountId);
  await navigator.clipboard?.writeText(item.code);
  const meta = document.querySelector(`[data-totp-wrap="${accountId}"] .totp-meta`);
  if (meta) meta.textContent = "已复制";
  window.setTimeout(() => updateTotpInline(Number(accountId)), 900);
}

function bindTotpActions() {
  document.querySelectorAll("[data-totp]").forEach((button) => {
    button.onclick = () => showTotpCode(Number(button.dataset.totp));
  });
  document.querySelectorAll("[data-copy-totp]").forEach((button) => {
    button.onclick = () => copyTotpCode(Number(button.dataset.copyTotp));
  });
}

function ensureTotpTicker() {
  if (state.totpTimer) return;
  state.totpTimer = window.setInterval(() => {
    if (!state.totpCodes.size) {
      window.clearInterval(state.totpTimer);
      state.totpTimer = 0;
      return;
    }
    state.totpCodes.forEach((item, accountId) => {
      if (totpRemaining(item) <= 0) showTotpCode(accountId);
      else updateTotpInline(accountId);
    });
  }, 1000);
}

function renderAccounts(seq = state.renderSeq) {
  if (!isActiveRender(seq)) return;
  content.innerHTML = state.accounts.length ? `<div class="account-grid">${state.accounts.map((a) => `
    <article class="account-card">
      <h3>${escapeHtml(a.name)}</h3>${status(a.status)}
      <dl>
        <dt>资源池</dt><dd>${escapeHtml(a.region || "自动同步全部资源池")}</dd>
        <dt>天翼云账号ID</dt><dd>${escapeHtml(a.provider_account_id || "等待登录后自动获取")}</dd>
        <dt>登录账号</dt><dd>${escapeHtml(a.username_masked)}</dd>
        <dt>AccessKey</dt><dd>${escapeHtml(a.ak_masked)}</dd>
        <dt>Google 2FA</dt><dd>${a.has_totp ? "已加密保存" : "未配置"}</dd>
        <dt>登录态</dt><dd>${a.has_cookie ? "已保存" : "尚未建立"}</dd>
      </dl>
      <div data-totp-wrap="${a.id}">${renderTotpInline(a)}</div>
      <div class="actions"><button data-edit-account="${a.id}">编辑</button><button data-console="${a.id}">官方控制台</button><button data-regions="${a.id}">查询资源池</button><button data-sync="${a.id}">同步资源</button><button data-balance="${a.id}">刷新余额</button><button data-recharge="${a.id}">打开充值</button><button class="danger" data-delete-account="${a.id}">删除</button></div>
    </article>`).join("")}</div>` : `<div class="empty">暂无账号。点击右上角“添加账号”一次性录入完整资料。</div>`;
  bindCommonActions();
  bindTotpActions();
}

const viewInfo = {
  dashboard: ["概览", "查看全部账号和资源状态"],
  accounts: ["云账号", "管理 API 凭证和官方网页登录资料"],
  ecs: ["云主机", "申请、启停、重启、释放与退订"],
  eip: ["弹性 IP", "申请、绑定、解绑和释放"],
  vpc: ["VPC 网络", "管理 VPC、子网、安全组和路由"],
  image: ["镜像", "管理公共镜像、私有镜像和制作任务"],
  ikuai: ["爱快网关", "集中管理多个爱快 Web 后台"],
  rustdesk: ["RustDesk 定制", "生成定制客户端源码并写入公开 GitHub 仓库"],
  recycle: ["退订与释放", "处理包周期退订与按需资源释放"],
  operations: ["操作日志", "查看资源操作和自动化执行记录"],
};

const actionsByType = {
  ecs: [
    ["remote_login", "远程登录"], ["start", "开机"], ["stop", "关机"], ["reboot", "重启"], ["update", "编辑"],
    ["reset_password", "重置密码"], ["rebuild", "重装系统"], ["resize", "变更规格"], ["change_private_ip", "修改内网IP"], ["change_vpc", "更换VPC"],
    ["deletion_protection", "删除保护"], ["auto_renew", "自动续订"],
    ["create_image", "制作镜像"], ["release", "释放"], ["unsubscribe", "退订"],
  ],
  eip: [["rename", "改名"], ["bind", "绑定"], ["unbind", "解绑"], ["release", "释放"], ["unsubscribe", "退订"]],
  vpc: [["create_subnet", "建子网"], ["manage_security_group", "安全组"], ["delete", "删除"]],
  image: [["create_ecs", "创建云主机"], ["copy", "复制"], ["share", "共享"], ["delete", "删除"]],
};

const resourceActionLabels = Object.fromEntries(
  Object.values(actionsByType).flat().map(([key, label]) => [key, label])
);

const resourceActionGroups = {
  eip: [
    { label: "绑定", actions: ["bind", "unbind"] },
    { label: "配置", actions: ["rename"] },
    { label: "高风险", danger: true, actions: ["release", "unsubscribe"] },
  ],
};

const ecsActionGroups = [
  { label: "电源", actions: ["start", "stop", "reboot"] },
  { label: "配置", actions: ["update", "reset_password", "rebuild", "resize", "deletion_protection", "auto_renew"] },
  { label: "镜像", actions: ["create_image"] },
  { label: "试验功能", experimental: true, actions: ["change_private_ip", "change_vpc"] },
  { label: "高风险", danger: true, actions: ["release", "unsubscribe"] },
];
resourceActionGroups.ecs = ecsActionGroups;

function resourceActionButton(key, label, row, type, extraAttrs = "") {
  const danger = ["release", "unsubscribe", "delete"].includes(key) ? " danger" : "";
  return `<button class="resource-action${danger}" data-resource-action="${key}" data-resource-type="${type}" data-resource-id="${row.provider_id}" data-account-id="${row.account_id}" data-region-id="${escapeHtml(row.region || "")}" data-resource-name="${escapeHtml(row.name || "")}"${extraAttrs}>${label}</button>`;
}

function renderResourceActions(type, row, extraAttrs = "") {
  const groups = resourceActionGroups[type];
  if (!groups) {
    return actionsByType[type].map(([key, label]) => resourceActionButton(key, label, row, type, extraAttrs)).join("");
  }
  const remote = type === "ecs" ? resourceActionButton("remote_login", resourceActionLabels.remote_login, row, type, extraAttrs) : "";
  const menus = groups.filter((group) => group.actions?.length).map((group) => `
    <details class="action-menu ${group.danger ? "danger-menu" : ""} ${group.experimental ? "experimental-menu" : ""}">
      <summary>${group.label}</summary>
      <div class="action-menu-panel">
        ${group.actions.map((key) => resourceActionButton(key, resourceActionLabels[key] || key, row, type, extraAttrs)).join("")}
      </div>
    </details>
  `).join("");
  return remote + menus;
}

const bulkableResourceTypes = new Set(["ecs", "eip"]);

function bulkSelectionAttrs(type, row, scope) {
  return `data-bulk-select data-resource-type="${type}" data-resource-id="${escapeHtml(row.provider_id)}" data-account-id="${row.account_id}" data-region-id="${escapeHtml(row.region || "")}" data-resource-name="${escapeHtml(row.name || "")}" data-bulk-scope="${escapeHtml(scope)}"`;
}

function bulkSelectHeader(type, scope) {
  if (!bulkableResourceTypes.has(type)) return "";
  return `<th class="select-col"><input type="checkbox" title="选择当前资源池" data-bulk-select-all data-resource-type="${type}" data-bulk-scope="${escapeHtml(scope)}"></th>`;
}

function bulkSelectCell(type, row, scope) {
  if (!bulkableResourceTypes.has(type)) return "";
  return `<td class="select-col"><input type="checkbox" ${bulkSelectionAttrs(type, row, scope)}></td>`;
}

function bulkToolbar(type) {
  if (type === "ecs") {
    return `<div class="bulk-toolbar"><button data-create="ecs" class="primary">申请 / 创建</button><span class="bulk-group"><button data-bulk-action="start" data-bulk-type="ecs">批量开机</button><button data-bulk-action="stop" data-bulk-type="ecs">批量关机</button><button data-bulk-action="reboot" data-bulk-type="ecs">批量重启</button></span><span class="bulk-group danger-group"><button class="danger" data-bulk-action="release" data-bulk-type="ecs">批量释放</button><button class="danger" data-bulk-action="unsubscribe" data-bulk-type="ecs">批量退订</button></span></div>`;
  }
  if (type === "eip") {
    return `<div class="bulk-toolbar"><button data-create="eip" class="primary">申请</button><button data-bulk-create="eip">批量申请</button><span class="bulk-group"><button data-bulk-action="unbind" data-bulk-type="eip">批量解绑</button></span><span class="bulk-group danger-group"><button class="danger" data-bulk-action="release" data-bulk-type="eip">批量释放</button><button class="danger" data-bulk-action="unsubscribe" data-bulk-type="eip">批量退订</button></span></div>`;
  }
  return `<button data-create="${type}" class="primary">申请 / 创建</button>`;
}

const imageTypes = {
  private: { label: "私有镜像", values: ["0", "private"] },
  shared: { label: "共享镜像", values: ["2", "shared"] },
};

function imageCategory(row) {
  const typeText = String(
    row.payload.imageType
    ?? row.payload.image_type
    ?? row.payload.type
    ?? ""
  ).toLowerCase();
  const value = String(
    row.payload.visibility
    ?? row.payload.imageVisibilityCode
    ?? row.payload.imageVisibility
    ?? ""
  ).toLowerCase();
  if (imageTypes.shared.values.includes(value) || /shared|share|共享/.test(typeText) || row.payload.source_user || row.payload.sourceUser) return "shared";
  if (imageTypes.private.values.includes(value) || /private|personal|私有/.test(typeText) || value === "") return "private";
  return "";
}

function actionButton(label, action, row, extraClass = "") {
  return `<button class="${extraClass}" data-resource-action="${action}" data-resource-type="${row.resource_type}" data-resource-id="${row.provider_id}" data-account-id="${row.account_id}" data-region-id="${escapeHtml(row.region || "")}" data-resource-name="${escapeHtml(row.name || "")}" data-image-type="${escapeHtml(row.payload?.visibility ?? "")}">${label}</button>`;
}

function resourceRowFromButton(button) {
  const type = button.dataset.resourceType;
  const rows = state.resources[type] || [];
  return rows.find((row) => (
    Number(row.account_id) === Number(button.dataset.accountId)
    && String(row.provider_id) === String(button.dataset.resourceId)
  )) || null;
}

function collectAccountIdValues(value, output = []) {
  if (!value) return output;
  if (Array.isArray(value)) {
    value.forEach((item) => collectAccountIdValues(item, output));
    return output;
  }
  if (typeof value === "object") {
    [
      "destinationAccountID", "destinationAccountId", "destinationUser", "destination_user",
      "targetAccountID", "targetAccountId", "targetUser", "receiverAccountID",
      "sharedAccountID", "sharedAccountId", "sharedUser", "accountID", "accountId",
      "userID", "userId", "id",
    ].forEach((key) => collectAccountIdValues(value[key], output));
    return output;
  }
  String(value).split(/[,，;；\s]+/).map((item) => item.trim()).filter(Boolean).forEach((item) => output.push(item));
  return output;
}

function imageSharedTargetIds(row) {
  const payload = row?.payload || {};
  const values = [
    payload.destination_user,
    payload.destinationUser,
    payload.destinationAccountID,
    payload.destinationAccountId,
    payload.destinationAccountIDs,
    payload.destinationUsers,
    payload.targetAccountID,
    payload.targetAccountId,
    payload.targetAccountIDs,
    payload.targetUsers,
    payload.receiverAccountID,
    payload.receiverAccountIDs,
    payload.sharedAccountID,
    payload.sharedAccountId,
    payload.sharedAccountIDs,
    payload.sharedUsers,
    payload.shareAccounts,
    payload.sharedAccounts,
    payload.sharedAccountList,
    payload.destinationAccountList,
    payload.receiverList,
    payload.targetList,
    payload.userList,
    payload.accountList,
  ];
  const ownerProviderId = state.accounts.find((account) => Number(account.id) === Number(row?.account_id))?.provider_account_id || "";
  return [...new Set(values.flatMap((value) => collectAccountIdValues(value)))]
    .filter((id) => id && String(id) !== String(ownerProviderId) && !String(id).startsWith("img-"));
}

function providerAccountOption(providerId) {
  const account = state.accounts.find((item) => String(item.provider_account_id || "") === String(providerId));
  return {
    value: providerId,
    label: account ? `${account.name} (${providerId})` : providerId,
  };
}

function imageSharedTargetOptions(row) {
  return imageSharedTargetIds(row).map(providerAccountOption);
}

function imageSourceTargetText(row) {
  const payload = row?.payload || {};
  const targets = imageSharedTargetOptions(row).map((option) => option.label);
  const source = payload.source_user || payload.sourceUser || payload.sourceAccountID || payload.sourceAccountId || "";
  if (targets.length) return targets.join("、");
  return source || payload.destination_user || payload.destinationUser || payload.destinationAccountID || payload.destinationAccountId || "-";
}

async function renderImages(seq = state.renderSeq) {
  const id = selectedAccountId();
  const rows = applyResourceOverrides("image", await cachedApi(`/api/resources/image${id ? `?account_id=${id}` : ""}`, 90000));
  if (!isActiveRender(seq)) return;
  state.resources.image = rows;
  const tabs = Object.entries(imageTypes).map(([key, meta]) => {
    const count = rows.filter((row) => imageCategory(row) === key).length;
    return `<button class="${state.imageTab === key ? "active" : ""}" data-image-tab="${key}">${meta.label}<span>${count}</span></button>`;
  }).join("");
  const filtered = rows.filter((row) => imageCategory(row) === state.imageTab);
  const groups = groupByRegion(filtered);
  const body = filtered.length ? Object.entries(groups).map(([key, group]) => regionGroup(group.label, group.rows.length, `
      <table><thead><tr><th>镜像</th><th>账号</th><th>状态</th><th>系统</th><th>来源/目标账号ID</th><th>操作</th></tr></thead><tbody>
        ${group.rows.map((row) => {
          const category = imageCategory(row);
          let actions = actionButton("创建云主机", "create_ecs", row);
          if (category === "private") {
            actions += actionButton("复制", "copy", row);
            actions += actionButton("共享", "share", row);
            if (imageSharedTargetOptions(row).length) actions += actionButton("取消共享", "unshare", row);
            actions += actionButton("删除", "delete", row, "danger");
          }
          if (category === "shared") actions += actionButton("接受", "accept", row) + actionButton("拒绝", "reject", row, "danger");
          return `<tr>
            <td><strong>${escapeHtml(row.name)}</strong><br><span class="muted">${escapeHtml(row.provider_id)}</span></td>
            <td>${escapeHtml(accountName(row.account_id))}</td>
            <td>${status(row.status || row.payload.status)}</td>
            <td>${escapeHtml(row.payload.os || row.payload.osDistro || row.payload.osVersion || "-")}</td>
            <td>${escapeHtml(imageSourceTargetText(row))}</td>
            <td><div class="actions">${actions}</div></td>
          </tr>`;
        }).join("")}
      </tbody></table>
    `, key)).join("") : `<div class="empty">没有${imageTypes[state.imageTab].label}</div>`;
  content.innerHTML = `<div class="segmented-tabs">${tabs}</div>${panel(imageTypes[state.imageTab].label, body)}`;
  document.querySelectorAll("[data-image-tab]").forEach((button) => button.onclick = async () => {
    state.imageTab = button.dataset.imageTab;
    await renderImages();
  });
  bindResourceActions();
  bindRegionToggles();
}

async function renderVpcNetwork(seq = state.renderSeq) {
  const id = selectedAccountId();
  const query = id ? `?account_id=${id}` : "";
  const [rawVpcs, rawSubnets, rawVips, rawEcs, rawEips, rawSecurityGroups] = await Promise.all([
    cachedApi(`/api/resources/vpc${query}`, 90000),
    cachedApi(`/api/resources/subnet${query}`, 90000),
    cachedApi(`/api/resources/vip${query}`, 90000),
    cachedApi(`/api/resources/ecs${query}`, 90000),
    cachedApi(`/api/resources/eip${query}`, 90000),
    cachedApi(`/api/resources/security_group${query}`, 90000),
  ]);
  if (!isActiveRender(seq)) return;
  const vpcs = applyResourceOverrides("vpc", rawVpcs);
  const subnets = applyResourceOverrides("subnet", rawSubnets);
  const vips = applyResourceOverrides("vip", rawVips);
  const ecs = applyResourceOverrides("ecs", rawEcs);
  const eips = applyResourceOverrides("eip", rawEips);
  const securityGroups = applyResourceOverrides("security_group", rawSecurityGroups);
  const allNetworkRows = [...vpcs, ...subnets, ...vips, ...securityGroups];
  const renderNetworkRegions = (accountId, accountRows, accountVpcs) => {
    const accountSubnets = subnets.filter((row) => Number(row.account_id) === Number(accountId));
    const accountVips = vips.filter((row) => Number(row.account_id) === Number(accountId));
    const accountSecurityGroups = securityGroups.filter((row) => Number(row.account_id) === Number(accountId));
    const regionKeys = [...new Set(accountVpcs.map(regionKey))];
    const vpcName = (vpcID) => accountVpcs.find((row) => row.provider_id === vpcID)?.name || "未识别 VPC";
    return regionKeys.map((key) => {
    const regionRows = accountRows.filter((row) => regionKey(row) === key);
    const label = regionLabel(regionRows[0] || {});
    const regionVpcs = accountVpcs.filter((row) => regionKey(row) === key);
    const regionSubnets = accountSubnets.filter((row) => regionKey(row) === key);
    const regionVips = accountVips.filter((row) => regionKey(row) === key);
    const regionSecurityGroups = accountSecurityGroups.filter((row) => regionKey(row) === key);
    return regionGroup(label, `${regionVpcs.length} VPC / ${regionSubnets.length} 子网 / ${regionVips.length} 虚拟IP / ${regionSecurityGroups.length} 安全组`, `
      <div class="network-block">
        <div class="subhead"><strong>VPC</strong></div>
        ${regionVpcs.length ? `<table><thead><tr><th>名称</th><th>状态</th><th>CIDR</th><th>子网数</th><th>操作</th></tr></thead><tbody>${regionVpcs.map((row) => `<tr><td>${escapeHtml(row.name)}</td><td>${status(row.status || row.payload.status)}</td><td>${escapeHtml(row.payload.cidr || row.payload.CIDR || "-")}</td><td>${Array.isArray(row.payload.subnetIDs) ? row.payload.subnetIDs.length : "-"}</td><td><div class="actions"><button data-edit-vpc="${row.provider_id}" data-account-id="${row.account_id}" data-region-id="${row.region}" data-name="${escapeHtml(row.name)}" data-description="${escapeHtml(row.payload.description || "")}">编辑</button><button class="danger" data-delete-vpc="${row.provider_id}" data-account-id="${row.account_id}" data-region-id="${row.region}">删除</button></div></td></tr>`).join("")}</tbody></table>` : `<div class="empty compact">无 VPC</div>`}
      </div>
      <div class="network-block">
        <div class="subhead"><strong>子网</strong></div>
        ${regionSubnets.length ? `<table><thead><tr><th>名称</th><th>状态</th><th>所属 VPC</th><th>CIDR</th><th>网关</th><th>可用IP</th><th>操作</th></tr></thead><tbody>${regionSubnets.map((row) => `<tr><td>${escapeHtml(row.name)}</td><td>${status(row.status || row.payload.status)}</td><td>${escapeHtml(vpcName(row.payload.vpc_id || row.payload.vpcID))}</td><td>${escapeHtml(row.payload.cidr || row.payload.CIDR || "-")}</td><td>${escapeHtml(row.payload.gatewayIP || "-")}</td><td>${escapeHtml(row.payload.availableIPCount ?? "-")}</td><td><div class="actions"><button data-edit-subnet="${row.provider_id}" data-account-id="${row.account_id}" data-region-id="${row.region}" data-name="${escapeHtml(row.name)}" data-description="${escapeHtml(row.payload.description || "")}" data-dns="${escapeHtml((row.payload.dnsList || []).join?.(",") || "")}">编辑</button><button data-create-vip-subnet="${row.provider_id}" data-account-id="${row.account_id}" data-region-id="${row.region}" data-vpc-id="${escapeHtml(row.payload.vpc_id || row.payload.vpcID || "")}">配置虚拟IP</button><button class="danger" data-delete-subnet="${row.provider_id}" data-account-id="${row.account_id}" data-region-id="${row.region}">删除</button></div></td></tr>`).join("")}</tbody></table>` : `<div class="empty compact">无子网</div>`}
      </div>
      <div class="network-block">
        <div class="subhead"><strong>虚拟IP</strong></div>
        ${regionVips.length ? `<table><thead><tr><th>虚拟IP</th><th>状态</th><th>子网</th><th>绑定云主机</th><th>绑定弹性IP</th><th>操作</th></tr></thead><tbody>${regionVips.map((row) => `<tr><td>${escapeHtml(row.payload.ip || row.payload.ipv4 || "-")}</td><td>${status(row.status || row.payload.status)}</td><td>${escapeHtml(regionSubnets.find((item) => item.provider_id === (row.payload.subnet_id || row.payload.subnetID))?.name || "未识别子网")}</td><td>${escapeHtml(row.payload.bound_instances || "-")}</td><td>${escapeHtml(row.payload.bound_eips || "-")}</td><td><div class="actions"><button data-vip-action="bind_ecs" data-resource-id="${row.provider_id}" data-account-id="${row.account_id}" data-region-id="${row.region}">绑定云主机</button><button data-vip-action="bind_eip" data-resource-id="${row.provider_id}" data-account-id="${row.account_id}" data-region-id="${row.region}">绑定弹性IP</button><button class="danger" data-vip-action="delete" data-resource-id="${row.provider_id}" data-account-id="${row.account_id}" data-region-id="${row.region}">删除</button></div></td></tr>`).join("")}</tbody></table>` : `<div class="empty compact">无虚拟IP</div>`}
      </div>
      <div class="network-block">
        <div class="subhead"><strong>安全组</strong></div>
        ${regionSecurityGroups.length ? `<table><thead><tr><th>名称</th><th>状态</th><th>所属 VPC</th><th>关联云主机</th><th>规则数</th><th>描述</th><th>操作</th></tr></thead><tbody>${regionSecurityGroups.map((row) => `<tr><td>${escapeHtml(row.name)}</td><td>${status(row.status || row.payload.status)}</td><td>${escapeHtml(vpcName(row.payload.vpc_id || row.payload.vpcID))}</td><td>${escapeHtml(row.payload.vmNum ?? "-")}</td><td>${escapeHtml((row.payload.securityGroupRuleList || []).length)}</td><td>${escapeHtml(row.payload.description || "-")}</td><td><div class="actions"><button data-sg-rule-action="create" data-resource-id="${row.provider_id}" data-account-id="${row.account_id}" data-region-id="${row.region}">添加规则</button><button data-sg-rule-action="delete" data-resource-id="${row.provider_id}" data-account-id="${row.account_id}" data-region-id="${row.region}">删除规则</button><button data-network-action="update" data-resource-type="security_group" data-resource-id="${row.provider_id}" data-account-id="${row.account_id}" data-region-id="${row.region}" data-name="${escapeHtml(row.name)}" data-description="${escapeHtml(row.payload.description || "")}">改名/描述</button>${row.payload.origin === "default" ? "" : `<button class="danger" data-network-action="delete" data-resource-type="security_group" data-resource-id="${row.provider_id}" data-account-id="${row.account_id}" data-region-id="${row.region}">删除</button>`}</div></td></tr>`).join("")}</tbody></table>` : `<div class="empty compact">无安全组</div>`}
      </div>
    `, `${accountId}:${key}`);
  }).join("");
  };
  const vpcsByAccount = groupByAccount(vpcs);
  const body = vpcs.length ? Object.entries(vpcsByAccount).map(([accountId, accountVpcs]) => {
    const rows = allNetworkRows.filter((row) => Number(row.account_id) === Number(accountId));
    return accountGroup(Number(accountId), accountVpcs.length, renderNetworkRegions(Number(accountId), rows, accountVpcs));
  }).join("") : `<div class="empty">没有已同步的 VPC</div>`;
  content.innerHTML = panel("VPC 网络", body, `<div class="actions"><button data-create-vpc>创建 VPC</button><button data-create-subnet>创建子网</button><button data-create-network="security_group">创建安全组</button></div>`);
  bindVpcActions({ subnets, ecs, eips, securityGroups });
  bindAccountToggles();
  bindRegionToggles();
}

function resourceSummaryFields(type, row) {
  const payload = row.payload || {};
  if (type === "ecs") {
    return [
      ["规格", payload.spec || "-"],
      ["私网 IP", payload.private_ip || payload.privateIP || "-"],
      ["公网 IP", payload.public_ip || payload.floatingIp || payload.floatingIP || "-"],
    ];
  }
  if (type === "eip") {
    return [
      ["公网 IP", payload.ip || payload.eipAddress || payload.address || "-"],
      ["带宽", payload.bandwidth_mbps ?? payload.bandwidth ?? payload.bandwidthSize ?? "-"],
      ["资源池", regionLabel(row)],
    ];
  }
  return [];
}

function resourceCardsHtml(type, rows, scope, actionAttrs = () => "") {
  if (!["ecs", "eip"].includes(type)) return "";
  return `<div class="resource-card-list">${rows.map((row) => {
    const stateValue = row.status || row.payload.instanceStatus;
    const stateLabel = type === "ecs" ? ecsStatusLabel(stateValue) : statusLabel(stateValue);
    return `<article class="resource-card">
      <div class="resource-card-top">
        ${bulkableResourceTypes.has(type) ? `<input type="checkbox" class="resource-card-check" ${bulkSelectionAttrs(type, row, scope)}>` : ""}
        <div class="resource-card-title"><strong>${escapeHtml(row.name)}</strong><span>${escapeHtml(row.provider_id)}</span></div>
        ${status(stateValue, stateLabel)}
      </div>
      <div class="resource-card-meta">${resourceSummaryFields(type, row).map(([label, value]) => `<div><small>${escapeHtml(label)}</small><b>${escapeHtml(value)}</b></div>`).join("")}</div>
      <div class="resource-card-actions actions resource-actions">${renderResourceActions(type, row, actionAttrs(row))}</div>
    </article>`;
  }).join("")}</div>`;
}

async function renderResources(type, seq = state.renderSeq) {
  const id = selectedAccountId();
  const rows = applyResourceOverrides(type, await cachedApi(`/api/resources/${type}${id ? `?account_id=${id}` : ""}`, 90000));
  if (!isActiveRender(seq)) return;
  const columns = {
    ecs: ["规格", "spec", "私网 IP", "private_ip"],
    eip: ["公网 IP", "ip", "带宽", "bandwidth_mbps"],
    vpc: ["CIDR", "cidr", "计费", "billing_mode"],
    image: ["类型", "image_type", "系统", "os"],
  }[type];
  const extraHeaders = type === "ecs"
    ? `<th>规格</th><th>私网 IP</th><th>公网 IP</th>`
    : `<th>${columns[0]}</th><th>${columns[2]}</th>`;
  const extraCells = (row) => type === "ecs"
    ? `<td>${escapeHtml(row.payload.spec || "-")}</td><td>${escapeHtml(row.payload.private_ip || row.payload.privateIP || "-")}</td><td>${escapeHtml(row.payload.public_ip || row.payload.floatingIp || row.payload.floatingIP || "-")}</td>`
    : `<td>${escapeHtml(row.payload[columns[1]] || "-")}</td><td>${escapeHtml(row.payload[columns[3]] ?? "-")}</td>`;
  const ecsNetworkAttrs = (row) => {
    if (type !== "ecs") return "";
    const payload = row.payload || {};
    const firstCard = Array.isArray(payload.networkCardList) ? (payload.networkCardList[0] || {}) : {};
    const privateIP = payload.private_ip || payload.privateIP || payload.privateIp || payload.ipAddress || firstCard.primaryPrivateIp || firstCard.primaryPrivateIP || firstCard.privateIP || "";
    const vpcID = payload.vpc_id || payload.vpcID || payload.vpcId || firstCard.vpcID || firstCard.vpcId || "";
    const subnetID = payload.subnet_id || payload.subnetID || payload.subnetId || firstCard.subnetID || firstCard.subnetId || "";
    const nicID = payload.network_card_id || payload.networkInterfaceID || payload.networkInterfaceId || payload.networkCardID || payload.networkCardId || firstCard.networkCardID || firstCard.networkCardId || firstCard.networkInterfaceID || firstCard.portID || "";
    return ` data-private-ip="${escapeHtml(privateIP)}" data-vpc-id="${escapeHtml(vpcID)}" data-subnet-id="${escapeHtml(subnetID)}" data-nic-id="${escapeHtml(nicID)}"`;
  };
  const accountGroups = groupByAccount(rows);
  const body = rows.length ? Object.entries(accountGroups).map(([accountId, accountRows]) => {
    const regions = groupByRegion(accountRows);
    const regionBody = Object.entries(regions).map(([key, group]) => {
      const bulkScope = `${type}:${accountId}:${key}`;
      return regionGroup(group.label, group.rows.length, `
        <div class="table-wrap resource-table"><table><thead><tr>${bulkSelectHeader(type, bulkScope)}<th>名称</th><th>状态</th>${extraHeaders}<th>操作</th></tr></thead><tbody>${group.rows.map((r) => `
          <tr>${bulkSelectCell(type, r, bulkScope)}<td><strong>${escapeHtml(r.name)}</strong><br><span class="muted">${escapeHtml(r.provider_id)}</span></td><td>${type === "ecs" ? status(r.status || r.payload.instanceStatus, ecsStatusLabel(r.status || r.payload.instanceStatus)) : status(r.status || r.payload.instanceStatus)}</td>${extraCells(r)}<td><div class="actions resource-actions">${renderResourceActions(type, r, ecsNetworkAttrs(r))}</div></td></tr>`).join("")}</tbody></table></div>
        ${resourceCardsHtml(type, group.rows, bulkScope, ecsNetworkAttrs)}
      `, `${accountId}:${key}`);
    }).join("");
    return accountGroup(Number(accountId), accountRows.length, regionBody);
  }).join("") : `<div class="empty">没有已同步的${viewInfo[type][0]}资源</div>`;
  content.innerHTML = panel(`${viewInfo[type][0]}列表`, body, bulkToolbar(type));
  bindResourceActions();
  bindBulkSelection();
  bindBulkActions();
  bindActionMenus();
  bindAccountToggles();
  bindRegionToggles();
}

function selectedBulkItems(type) {
  const seen = new Set();
  return Array.from(document.querySelectorAll(`[data-bulk-select][data-resource-type="${type}"]:checked`)).flatMap((input) => {
    const key = `${input.dataset.accountId}:${input.dataset.regionId}:${input.dataset.resourceId}`;
    if (seen.has(key)) return [];
    seen.add(key);
    return [{
      accountId: Number(input.dataset.accountId),
      resourceId: input.dataset.resourceId || "",
      regionId: input.dataset.regionId || "",
      name: input.dataset.resourceName || input.dataset.resourceId || "",
    }];
  });
}

function syncBulkDuplicateChecks(input) {
  const selector = `[data-bulk-select][data-resource-type="${input.dataset.resourceType}"][data-bulk-scope="${CSS.escape(input.dataset.bulkScope)}"][data-resource-id="${CSS.escape(input.dataset.resourceId)}"]`;
  document.querySelectorAll(selector).forEach((other) => {
    other.checked = input.checked;
  });
}

function updateBulkSelectAll(scope, type) {
  const items = Array.from(document.querySelectorAll(`[data-bulk-select][data-resource-type="${type}"][data-bulk-scope="${CSS.escape(scope)}"]`));
  const checked = items.filter((item) => item.checked);
  document.querySelectorAll(`[data-bulk-select-all][data-resource-type="${type}"][data-bulk-scope="${CSS.escape(scope)}"]`).forEach((control) => {
    control.checked = Boolean(items.length && checked.length === items.length);
    control.indeterminate = Boolean(checked.length && checked.length < items.length);
  });
}

function bindBulkSelection() {
  document.querySelectorAll("[data-bulk-select-all]").forEach((control) => {
    control.onchange = () => {
      document.querySelectorAll(`[data-bulk-select][data-resource-type="${control.dataset.resourceType}"][data-bulk-scope="${CSS.escape(control.dataset.bulkScope)}"]`).forEach((item) => {
        item.checked = control.checked;
      });
      updateBulkSelectAll(control.dataset.bulkScope, control.dataset.resourceType);
    };
  });
  document.querySelectorAll("[data-bulk-select]").forEach((input) => {
    input.onchange = () => {
      syncBulkDuplicateChecks(input);
      updateBulkSelectAll(input.dataset.bulkScope, input.dataset.resourceType);
    };
  });
}

function bulkActionText(type, action) {
  const labels = {
    ecs: { start: "批量开机", stop: "批量关机", reboot: "批量重启", release: "批量释放", unsubscribe: "批量退订" },
    eip: { unbind: "批量解绑", release: "批量释放", unsubscribe: "批量退订" },
  };
  return labels[type]?.[action] || "批量操作";
}

async function submitBulkResourceAction(type, action, items, payloadForItem = () => ({})) {
  if (!items.length) return toast("请先勾选要操作的资源");
  const changedTypes = resourceTypesAfterAction(type, action);
  const label = bulkActionText(type, action);
  const results = await withPageLoading(`正在提交${label}...`, () => runLimited(items, 3, (item) => submitAction(
    item.accountId,
    type,
    action,
    item.resourceId,
    { regionID: item.regionId, ...payloadForItem(item) },
    { clearCache: false, toastMessage: false },
  )));
  const failed = results.filter((result) => !result.ok);
  const success = results.filter((result) => result.ok);
  clearResourceCaches(changedTypes);
  if (viewMatchesResourceTypes(changedTypes)) await render();
  [...new Set(success.map((result) => result.item.accountId))].forEach((accountId) => {
    schedulePostActionSync(changedTypes, accountId);
  });
  if (failed.length) {
    toast(`${label}：成功 ${success.length} 项，失败 ${failed.length} 项：${failed[0].error?.message || "未知错误"}`);
    return;
  }
  toast(`${label}已提交 ${success.length} 项，后台刷新中`);
}

function bindBulkActions() {
  document.querySelectorAll("[data-bulk-action]").forEach((button) => {
    button.onclick = () => {
      const type = button.dataset.bulkType;
      const action = button.dataset.bulkAction;
      const items = selectedBulkItems(type);
      const label = bulkActionText(type, action);
      if (!items.length) return toast("请先勾选要操作的资源");
      const destructive = ["release", "unsubscribe", "delete"].includes(action);
      confirmAction(
        destructive ? "确认批量高风险操作" : "确认批量操作",
        `${label} ${items.length} 项资源。${destructive ? "此操作可能不可逆，请确认资源已不再需要。" : ""}`,
        () => submitBulkResourceAction(type, action, items),
      );
    };
  });
  document.querySelectorAll("[data-bulk-create='eip']").forEach((button) => {
    button.onclick = () => openEipCreateDialog(selectedAccountId(), true);
  });
}

function bindActionMenus() {
  document.querySelectorAll(".action-menu").forEach((menu) => {
    menu.addEventListener("toggle", () => {
      if (!menu.open) return;
      menu.closest(".resource-actions")?.querySelectorAll(".action-menu").forEach((other) => {
        if (other !== menu) other.open = false;
      });
    });
  });
}

async function renderRecycle(seq = state.renderSeq) {
  const ops = await cachedApi("/api/operations", 8000);
  if (!isActiveRender(seq)) return;
  const rows = ops.filter((o) => ["release", "unsubscribe", "delete"].includes(o.action));
  content.innerHTML = panel("回收记录", rows.length ? `<table><thead><tr><th>时间</th><th>账号</th><th>资源类型</th><th>资源 ID</th><th>动作</th><th>结果</th></tr></thead><tbody>${rows.map((o) => `<tr><td>${escapeHtml(o.created_at)}</td><td>${escapeHtml(accountName(o.account_id))}</td><td>${escapeHtml(resourceTypeLabel(o.resource_type))}</td><td>${escapeHtml(o.resource_id || "-")}</td><td>${escapeHtml(actionDisplayLabel(o.resource_type, o.action))}</td><td>${status(o.status)}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">暂无退订或释放记录</div>`);
}

async function renderOperations(seq = state.renderSeq) {
  const rows = await cachedApi("/api/operations", 8000);
  if (!isActiveRender(seq)) return;
  content.innerHTML = panel("最近 200 条操作", rows.length ? `<table><thead><tr><th>时间</th><th>账号</th><th>资源</th><th>动作</th><th>状态</th><th>消息</th></tr></thead><tbody>${rows.map((o) => `<tr><td>${escapeHtml(o.created_at)}</td><td>${escapeHtml(accountName(o.account_id))}</td><td>${escapeHtml(resourceTypeLabel(o.resource_type))} ${escapeHtml(o.resource_id || "")}</td><td>${escapeHtml(actionDisplayLabel(o.resource_type, o.action))}</td><td>${status(o.status)}</td><td>${escapeHtml(o.message || "-")}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">暂无操作记录</div>`);
}

function rustdeskStatusLabel(job = {}) {
  const labels = { queued: "排队中", running: "执行中", success: "已完成", failed: "失败" };
  return status(job.status || "unknown", labels[job.status] || "未知");
}

function rustdeskLogHtml(job = {}) {
  const logs = job.logs || [];
  if (!logs.length) return `<div class="rustdesk-log-empty">任务日志会显示在这里</div>`;
  return logs.map((item) => `<div><time>${escapeHtml(item.time || "")}</time><span>${escapeHtml(item.message || "")}</span></div>`).join("");
}

function renderRustdeskJob(job = null) {
  if (!job) {
    return `<div class="rustdesk-job-card muted">还没有提交任务。填写左侧表单后，后台会开始拉取 RustDesk 官方源码并写入目标仓库。</div>`;
  }
  const result = job.result || {};
  return `
    <div class="rustdesk-job-card">
      <div class="rustdesk-job-head">
        <div>
          <strong>${escapeHtml(job.payload?.repo || result.repo || "RustDesk 定制任务")}</strong>
          <p>${escapeHtml(job.message || job.error || "等待执行")}</p>
        </div>
        ${rustdeskStatusLabel(job)}
      </div>
      <dl class="rustdesk-job-meta">
        <dt>RustDesk 版本</dt><dd>${escapeHtml(job.payload?.rustdesk_version || result.rustdesk_version || "-")}</dd>
        <dt>ID 服务器</dt><dd>${escapeHtml(job.payload?.id_server || "-")}</dd>
        <dt>创建时间</dt><dd>${escapeHtml(job.created_at || "-")}</dd>
        <dt>更新时间</dt><dd>${escapeHtml(job.updated_at || "-")}</dd>
      </dl>
      ${result.url ? `<div class="rustdesk-result-actions"><a class="button-link" href="${escapeHtml(result.url)}" target="_blank" rel="noopener">打开目标仓库</a><a class="button-link" href="${escapeHtml(result.actions_url || `${result.url}/actions`)}" target="_blank" rel="noopener">打开 Actions</a></div>` : ""}
      ${job.error ? `<div class="form-error">${escapeHtml(job.error)}</div>` : ""}
      <div class="rustdesk-log">${rustdeskLogHtml(job)}</div>
    </div>`;
}

async function loadRustdeskJob(jobId = state.rustdeskJobId) {
  if (!jobId) return null;
  return api(`/api/tools/rustdesk/jobs/${encodeURIComponent(jobId)}?t=${Date.now()}`);
}

function startRustdeskPolling(jobId) {
  state.rustdeskJobId = jobId || "";
  if (state.rustdeskJobId) localStorage.setItem("ctyun:rustdeskJobId", state.rustdeskJobId);
  clearInterval(state.rustdeskPollTimer);
  if (!state.rustdeskJobId) return;
  state.rustdeskPollTimer = window.setInterval(async () => {
    if (state.view !== "rustdesk") return;
    try {
      const job = await loadRustdeskJob();
      const target = $("#rustdeskJobPanel");
      if (target) target.innerHTML = renderRustdeskJob(job);
      if (["success", "failed"].includes(job.status)) {
        clearInterval(state.rustdeskPollTimer);
        state.rustdeskPollTimer = 0;
      }
    } catch (error) {
      clearInterval(state.rustdeskPollTimer);
      state.rustdeskPollTimer = 0;
    }
  }, 3000);
}

function rustdeskPayloadFromForm(form) {
  const data = Object.fromEntries(new FormData(form));
  return {
    repo: data.repo || "",
    token: data.token || "",
    rustdesk_version: data.rustdesk_version || "",
    id_server: data.id_server || "",
    rs_pub_key: data.rs_pub_key || "",
    relay_server: data.relay_server || "",
    api_server: data.api_server || "",
    default_password: data.default_password || "",
    allow_remote_config_modification: Boolean(form.elements.allow_remote_config_modification.checked),
    hide_cm: Boolean(form.elements.hide_cm.checked),
    hide_builtin_server_values: true,
    commit_message: data.commit_message || "",
    target_branch: data.target_branch || "",
    about: {
      title: data.about_title || "",
      product_name: data.about_product_name || "",
      vendor_name: data.about_vendor_name || "",
      support_url: data.about_support_url || "",
      privacy_url: data.about_privacy_url || "",
      show_official_link: Boolean(form.elements.about_show_official_link.checked),
      show_license_text: Boolean(form.elements.about_show_license_text.checked),
    },
  };
}

async function submitRustdeskCustomize(event) {
  event.preventDefault();
  const form = event.target;
  const error = $("#rustdeskError");
  const button = $("#rustdeskSubmitBtn");
  const jobPanel = $("#rustdeskJobPanel");
  error.textContent = "";
  button.disabled = true;
  const oldText = button.textContent;
  button.textContent = "正在创建任务...";
  try {
    const job = await api("/api/tools/rustdesk/jobs", {
      method: "POST",
      body: JSON.stringify(rustdeskPayloadFromForm(form)),
    });
    form.elements.token.value = "";
    jobPanel.innerHTML = renderRustdeskJob(job);
    startRustdeskPolling(job.id);
    toast("RustDesk 定制任务已开始");
  } catch (err) {
    error.textContent = err.message;
  } finally {
    button.disabled = false;
    button.textContent = oldText;
  }
}

async function renderRustdesk(seq = state.renderSeq) {
  let job = null;
  if (state.rustdeskJobId) {
    try {
      job = await loadRustdeskJob();
    } catch {
      job = null;
      state.rustdeskJobId = "";
      localStorage.removeItem("ctyun:rustdeskJobId");
    }
  }
  if (!isActiveRender(seq)) return;
  content.innerHTML = `
    <div class="rustdesk-layout">
      <section class="panel rustdesk-form-panel">
        <div class="panel-head"><div><h3>RustDesk 定制源码生成</h3><p class="muted">token 只用于本次任务，不保存数据库，不写日志。</p></div></div>
        <form id="rustdeskForm" class="rustdesk-form">
          <div class="form-grid">
            <label>GitHub 公开仓库<input name="repo" placeholder="owner/repo 或 https://github.com/owner/repo" required></label>
            <label>Personal access token classic<input name="token" type="password" autocomplete="off" placeholder="至少需要 workflow，可用完删除" required></label>
            <label>RustDesk 官方版本<input name="rustdesk_version" placeholder="例如 1.4.7" required></label>
            <label>目标分支<input name="target_branch" placeholder="留空使用仓库默认分支"></label>
            <label>ID 服务器<input name="id_server" placeholder="例如 hbbs.example.com" required></label>
            <label>RS_PUB_KEY<input name="rs_pub_key" autocomplete="off" required></label>
            <label>中继服务器<input name="relay_server" placeholder="可选，留空使用默认逻辑"></label>
            <label>API 服务器<input name="api_server" placeholder="留空则 http://ID服务器:21114"></label>
            <label>默认密码<input name="default_password" type="password" autocomplete="new-password" placeholder="可选"></label>
            <label>提交说明<input name="commit_message" placeholder="留空自动生成"></label>
            <label class="checkbox-field wide"><input name="allow_remote_config_modification" type="checkbox" checked>允许远程修改配置</label>
            <label class="checkbox-field wide"><input name="hide_cm" type="checkbox" checked>隐藏“自建服务器”跳转入口</label>
          </div>
          <details class="rustdesk-advanced">
            <summary>关于页面选项（可选）</summary>
            <div class="form-grid">
              <label>关于页标题<input name="about_title"></label>
              <label>产品名<input name="about_product_name"></label>
              <label>服务商名称<input name="about_vendor_name"></label>
              <label>支持链接<input name="about_support_url"></label>
              <label>隐私链接<input name="about_privacy_url"></label>
              <label class="checkbox-field wide"><input name="about_show_official_link" type="checkbox" checked>显示官方 RustDesk 链接</label>
              <label class="checkbox-field wide"><input name="about_show_license_text" type="checkbox" checked>保留开源许可证文本</label>
            </div>
          </details>
          <div class="inline-notice">
            <div>当前方案按 RustDesk 1.4.7 成功路径实现：服务器信息写源码常量，删除 <code>res/local_custom_client.json</code>，并把子模块本地化。</div>
            <div>目标仓库必须是公开仓库；如果 GitHub 选择 <code>workflow</code> 时自动勾选 <code>repo</code>，可以继续使用，用完后建议立即删除 token。</div>
          </div>
          <div id="rustdeskError" class="form-error"></div>
          <div class="dialog-actions rustdesk-actions"><button id="rustdeskSubmitBtn" class="primary" type="submit">开始写入目标仓库</button></div>
        </form>
      </section>
      <section class="panel rustdesk-status-panel">
        <div class="panel-head"><div><h3>执行状态</h3><p class="muted">拉源码和推送可能需要数分钟，期间不要重复提交。</p></div><button id="rustdeskRefreshBtn" type="button">刷新状态</button></div>
        <div id="rustdeskJobPanel">${renderRustdeskJob(job)}</div>
      </section>
    </div>`;
  $("#rustdeskForm").onsubmit = submitRustdeskCustomize;
  $("#rustdeskRefreshBtn").onclick = async () => {
    if (!state.rustdeskJobId) return toast("暂无任务");
    const current = await loadRustdeskJob();
    $("#rustdeskJobPanel").innerHTML = renderRustdeskJob(current);
    if (!["success", "failed"].includes(current.status)) startRustdeskPolling(current.id);
  };
  if (job && !["success", "failed"].includes(job.status)) startRustdeskPolling(job.id);
}

function flattenIkuaiMenus(groups = []) {
  return groups.flatMap((group) => (group.items || []).map((item) => ({ ...item, group: group.label, groupId: group.id })));
}

function ikuaiMenuLabel(groups = [], section = state.ikuaiSection) {
  return flattenIkuaiMenus(groups).find((item) => item.id === section)?.label || section;
}

function ikuaiGatewayName(id, gateways = []) {
  return gateways.find((item) => Number(item.id) === Number(id))?.name || `网关 ${id}`;
}

function formatIkuaiBytes(value) {
  const number = Number(value || 0);
  if (!Number.isFinite(number)) return "-";
  if (number < 1024) return `${number} B`;
  if (number < 1024 ** 2) return `${(number / 1024).toFixed(1)} KB`;
  if (number < 1024 ** 3) return `${(number / 1024 ** 2).toFixed(1)} MB`;
  return `${(number / 1024 ** 3).toFixed(1)} GB`;
}

function formatIkuaiUptime(seconds) {
  const total = Number(seconds || 0);
  if (!Number.isFinite(total) || total <= 0) return "-";
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  return `${days ? `${days}天` : ""}${hours ? `${hours}小时` : ""}${minutes}分`;
}

function compactIkuaiValue(value) {
  if (value === null || value === undefined || value === "") return "-";
  if (Array.isArray(value)) return value.length ? value.map((item) => compactIkuaiValue(item)).join(" / ") : "-";
  if (typeof value === "object") {
    const pairs = Object.entries(value)
      .filter(([, item]) => ikuaiHasValue(item))
      .slice(0, 8)
      .map(([key, item]) => `${key}: ${compactIkuaiValue(item)}`);
    return pairs.length ? pairs.join("；") : "-";
  }
  return String(value);
}

function ikuaiHasValue(value) {
  if (value === null || value === undefined || value === "") return false;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === "object") return Object.values(value).some((item) => ikuaiHasValue(item));
  return true;
}

function ikuaiRowsFromResult(section, result) {
  if (!result || typeof result !== "object") return [];
  const dataNode = result.Data || result.data || result.results || result.ResultData;
  if (dataNode && typeof dataNode === "object") {
    if (Array.isArray(dataNode.data) && dataNode.data.length) return dataNode.data;
    if (Array.isArray(dataNode[section]) && dataNode[section].length) return dataNode[section];
    let emptyArray = null;
    if (Array.isArray(dataNode)) return dataNode;
    for (const value of Object.values(dataNode)) {
      if (Array.isArray(value) && value.every((item) => item && typeof item === "object")) {
        if (value.length) return value;
        emptyArray = value;
      }
    }
    const entries = Object.entries(dataNode);
    if (entries.length === 1 && entries[0][1] && typeof entries[0][1] === "object" && !Array.isArray(entries[0][1])) {
      return [entries[0][1]];
    }
    if (emptyArray) return emptyArray;
    if (Object.keys(dataNode).length) return [dataNode];
  }
  if (Array.isArray(result.data)) return result.data;
  if (Array.isArray(result[section])) return result[section];
  let emptyArray = null;
  for (const value of Object.values(result)) {
    if (Array.isArray(value) && value.every((item) => item && typeof item === "object")) {
      if (value.length) return value;
      emptyArray = value;
    }
  }
  return emptyArray || [];
}

function renderIkuaiOverview(row = {}) {
  const stream = row.stream || {};
  const online = row.online_user || {};
  const memory = row.memory || {};
  const verinfo = row.verinfo || {};
  const flow = `↑ ${formatIkuaiBytes(stream.upload)}/s  ↓ ${formatIkuaiBytes(stream.download)}/s`;
  const items = [
    ["主机名", row.hostname || "iKuai"],
    ["系统版本", verinfo.verstring || verinfo.version || "-"],
    ["运行时间", formatIkuaiUptime(row.uptime)],
    ["在线终端", online.count ?? "-"],
    ["连接数", stream.connect_num ?? "-"],
    ["实时流量", flow],
    ["CPU", compactIkuaiValue(row.cpu)],
    ["内存", memory.used ? `${memory.used} 已用` : "-"],
  ];
  return `<div class="metric-grid ikuai-overview">${items.map(([label, value]) => `
    <div class="metric"><small>${escapeHtml(label)}</small><strong>${escapeHtml(value)}</strong></div>
  `).join("")}</div>`;
}

const ikuaiColumnLabels = {
  ip_addr: "IP 地址",
  ip: "IP 地址",
  mac: "MAC 地址",
  hostname: "主机名",
  username: "账号",
  comment: "备注",
  enabled: "状态",
  enable: "状态",
  status: "状态",
  ifname: "接口",
  interface: "接口",
  line: "线路",
  type: "类型",
  mode: "模式",
  name: "名称",
  ssid: "SSID",
  upload: "上行",
  download: "下行",
  up_rate: "上行速率",
  down_rate: "下行速率",
  total_up: "累计上行",
  total_down: "累计下行",
  connect_num: "连接数",
  uptime: "运行时间",
  time: "时间",
  add_time: "添加时间",
  update_time: "更新时间",
  timestamp: "时间戳",
  ppptype: "接入方式",
  ip_addr_int: "IP 排序",
  auth_type: "认证类型",
  client_type: "终端类型",
  gateway: "网关",
  netmask: "掩码",
  dns: "DNS",
  protocol: "协议",
  port: "端口",
  src_addr: "源地址",
  dst_addr: "目标地址",
  pppdev: "拨号设备",
  pppname: "认证名称",
  packname: "套餐",
  packages: "套餐",
  duration: "有效时长",
  expires: "到期时间",
  start_time: "开始时间",
  create_time: "创建时间",
  last_conntime: "最近上线",
  last_offtime: "最近离线",
  auth_time: "认证时间",
  session: "会话",
  share: "共享数",
  phone: "手机",
  address: "地址",
  cardid: "证件号",
  proxy_username: "代拨账号",
  bind_vlanid: "绑定 VLAN",
  auto_vlanid: "自动 VLAN",
  bind_ifname: "绑定网卡",
  auto_mac: "自动绑定 MAC",
  ip_type: "IP 类型",
  server_name: "服务名称",
  server_ip: "服务端地址",
  dns1: "主 DNS",
  dns2: "备用 DNS",
  authmode: "认证方式",
  radius_ip: "RADIUS 地址",
  authport: "认证端口",
  accountport: "计费端口",
  addr_pool: "客户端地址池",
  force_verify_name: "校验服务名",
  force_pppoe: "强制拨号",
  bind_vlan: "支持绑定 VLAN",
  verify_vlan: "校验 VLAN",
  bind_iface: "支持绑定网卡",
  rate_limit_lan: "LAN 互访限速",
  drop_client: "禁止客户端互访",
  enhance_check: "加强断线检测",
  share_deny: "超共享数操作",
  mtu: "MTU",
  mru: "MRU",
  lcp_echo_interval: "LCP 间隔",
  lcp_echo_failure: "LCP 失败次数",
  maxconnect: "最大连接数",
  restart_timer: "定时重启",
  restart_week: "重启星期",
  restart_time: "重启时间",
  group_id: "认证组 ID",
  group_key: "认证组秘钥",
  user_auth: "账号认证",
  phone_auth: "短信认证",
  coupon_auth: "上网码认证",
  static_pwd: "固定密码",
  nopasswd: "免密码",
  weixin: "微信认证",
  qq_auth: "QQ 认证",
  weibo_auth: "微博认证",
  allow_tryout: "允许试用",
  tryout_time: "试用时间",
  authip_mode: "认证范围",
  custom_auth: "自定义认证",
  template: "模板",
  price: "价格",
  money: "金额",
  cycle_type: "周期类型",
  expire_type: "有效期类型",
  check_vlan_res: "VLAN 检测结果",
  twitter_auth: "Twitter 认证",
  google_auth: "Google 认证",
  facebook_auth: "Facebook 认证",
  redpacket_auth: "红包认证",
  hotel_auth: "酒店认证",
  openapi_auth: "开放接口认证",
  auto_auth: "自动认证",
  api_switch: "接口开关",
  api_radius: "接口 RADIUS",
  api_ipchange_url: "IP 变更接口地址",
  api_url: "接口地址",
  authip_mode: "认证 IP 范围",
  custom_appkey: "自定义 AppKey",
  custom_auth: "自定义认证",
  auto_auth_timeout: "自动认证超时",
  user_timeout: "账号认证超时",
  coupon_timeout: "上网码超时",
  qq_timeout: "QQ 认证超时",
  weibo_timeout: "微博认证超时",
  phone_timeout: "短信认证超时",
  static_timeout: "固定密码超时",
  nopasswd_timeout: "免密码超时",
  weixin_timeout: "微信认证超时",
  tryout_timeout: "试用超时",
  max_time: "最长在线时间",
  idle_time: "空闲超时",
  user_max_time: "账号最长在线",
  user_idle_time: "账号空闲超时",
  enc_ssid_noauth: "加密 SSID 免认证",
  timer_restart: "定时重启",
  timer_event: "定时事件",
  timer_restart_week: "定时重启星期",
  timer_restart_time: "定时重启时间",
  interface_list: "接口列表",
  phy_iface: "物理接口",
  parent: "父接口",
  ip_method: "IP 获取方式",
  macaddr: "MAC 地址",
  mask: "掩码",
  gateway_iface: "网关接口",
  default_gateway: "默认网关",
  metric: "优先级",
  static_addr: "静态地址",
  dhcp_addr: "DHCP 地址",
  lease_time: "租约时间",
  pool_start: "地址池开始",
  pool_end: "地址池结束",
  start_addr: "开始地址",
  end_addr: "结束地址",
  vlanid: "VLAN ID",
  vlan_id: "VLAN ID",
  src_ip: "源 IP",
  dst_ip: "目标 IP",
  src_port: "源端口",
  dst_port: "目标端口",
  wan: "外网",
  lan: "内网",
  acl: "ACL",
  app: "应用",
  domain: "域名",
  url: "网址",
  action: "动作",
  policy: "策略",
  priority: "优先级",
  bandwidth: "带宽",
  min_tx: "最小上行",
  max_tx: "最大上行",
  min_rx: "最小下行",
  max_rx: "最大下行",
  tx_rate: "上行速率",
  rx_rate: "下行速率",
  upload_rate: "上行速率",
  download_rate: "下行速率",
  local_addr: "本地地址",
  remote_addr: "远端地址",
  local_port: "本地端口",
  remote_port: "远端端口",
  server: "服务器",
  client: "客户端",
  device: "设备",
  model: "型号",
  version: "版本",
  channel: "信道",
  encryption: "加密方式",
  hidden: "隐藏",
  signal: "信号",
  noise: "噪声",
  online: "在线",
  offline: "离线",
  rx_bytes: "接收流量",
  tx_bytes: "发送流量",
  total: "总数",
  speed: "速率",
  duplex: "双工模式",
  qos_up: "QoS 上行",
  qos_down: "QoS 下行",
  bandmode: "绑定模式",
  linkmode: "链路模式",
  lan_visit: "内网访问",
  wifiwisp: "无线中继",
  ip_mask: "IP 掩码",
  lease: "租约",
  delay: "延迟",
  opttype12: "DHCP Option 12",
  opttype15: "DHCP Option 15",
  opttype28: "DHCP Option 28",
  opttype43: "DHCP Option 43",
  opttype60: "DHCP Option 60",
  opttype61: "DHCP Option 61",
  opttype66: "DHCP Option 66",
  opttype67: "DHCP Option 67",
  opttype80: "DHCP Option 80",
  opt_type12: "DHCP Option 12",
  opt_type15: "DHCP Option 15",
  opt_type28: "DHCP Option 28",
  opt_type43: "DHCP Option 43",
  opt_type60: "DHCP Option 60",
  opt_type61: "DHCP Option 61",
  opt_type66: "DHCP Option 66",
  opt_type67: "DHCP Option 67",
  opt_type80: "DHCP Option 80",
  opt_type119: "DHCP Option 119",
  opt_type121: "DHCP Option 121",
  opt_type125: "DHCP Option 125",
  opt_type128: "DHCP Option 128",
  opt_type138: "DHCP Option 138",
  opt15: "DHCP Option 15 内容",
  opt28: "DHCP Option 28 内容",
  opt43: "DHCP Option 43 内容",
  opt60: "DHCP Option 60 内容",
  opt66: "DHCP Option 66 内容",
  opt67: "DHCP Option 67 内容",
  opt80: "DHCP Option 80 内容",
  opt119: "DHCP Option 119 内容",
  opt121: "DHCP Option 121 内容",
  opt125: "DHCP Option 125 内容",
  opt128: "DHCP Option 128 内容",
  opt138: "DHCP Option 138 内容",
  phy_ifnames: "物理接口",
  exclude_pool: "排除地址池",
  next_server: "下一跳服务器",
  wins1: "主 WINS",
  wins2: "备用 WINS",
  available: "可用",
  check_addr_valid: "检查地址有效性",
  check_relay_only: "仅检查中继",
  vendorclass: "Vendor Class",
  clientid: "客户端 ID",
  wifi_bssid: "无线 BSSID",
  wifi_ssid: "无线 SSID",
  wifi_psk: "无线密码",
  timing_rst_switch: "定时重拨开关",
  timing_rst_week: "定时重拨星期",
  timing_rst_time: "定时重拨时间",
  cycle_rst_time: "循环重拨时间",
  pppoe_service: "PPPoE 服务名",
  pppoe_ac: "PPPoE AC",
  default_route: "默认路由",
  disc_auto_switch: "断线自动重拨",
  link_time: "连接时长",
  check_link_mode: "链路检测模式",
  check_link_host: "链路检测主机",
  qos_switch: "QoS 开关",
  enable_ipv6: "启用 IPv6",
  lte_service: "LTE 服务",
  lte_mode: "LTE 模式",
  lte_apn: "LTE APN",
  lte_dialnum: "LTE 拨号号码",
  lte_pincode: "LTE PIN 码",
  lte_antenna_switch: "LTE 天线开关",
  bandlist_5g: "5G 频段列表",
  sim_switch: "SIM 切换",
  pppoe_ass_switch: "PPPoE 辅助开关",
  ass_multi_total: "辅助检测总数",
  ass_disc_rst_switch: "断线重拨检测开关",
  ass_rst_check_week: "重拨检测星期",
  ass_rst_check_time: "重拨检测时间",
  ass_rst_check_interval: "重拨检测间隔",
  ass_rst_disc_num: "断线检测次数",
  ass_rst_disc_norestart: "断线不重启",
  ass_check_errip_switch: "异常 IP 检测开关",
  ass_check_errip_list: "异常 IP 检测列表",
  pppoe_check_errip_switch: "PPPoE 异常 IP 检测开关",
  pppoe_check_errip_list: "PPPoE 异常 IP 检测列表",
  pppoe_status: "PPPoE 状态",
  dhcp_status: "DHCP 状态",
  dhcp_lease: "DHCP 租约",
  dhcp_dns1: "DHCP 主 DNS",
  dhcp_dns2: "DHCP 备用 DNS",
  dhcp_updatetime: "DHCP 更新时间",
  dhcp_gateway: "DHCP 网关",
  dhcp_netmask: "DHCP 掩码",
  dhcp_ip_addr: "DHCP IP 地址",
  pppoe_macremote: "PPPoE 远端 MAC",
  pppoe_dns1: "PPPoE 主 DNS",
  pppoe_dns2: "PPPoE 备用 DNS",
  pppoe_ip_addr: "PPPoE IP 地址",
  pppoe_updatetime: "PPPoE 更新时间",
  pppoe_netmask: "PPPoE 掩码",
  pppoe_gateway: "PPPoE 网关",
  modified_time: "修改时间",
  bandif: "绑定接口",
  bandeth: "绑定网卡",
  internet: "联网方式",
  group_name: "组名称",
  perm_config: "权限配置",
  perm_default: "默认权限",
  force: "强制登录",
  interval: "间隔",
  passwd_timeout: "密码超时",
  sesstimeout: "会话超时",
  bind_status: "绑定状态",
  bind_addr: "绑定地址",
  client_total: "客户端总数",
  client_data: "客户端列表",
  lan_data: "内网列表",
  lan_interface: "内网接口",
  wan_interface: "外网接口",
  upstream: "上游接口",
  downstream: "下游接口",
  wan_iface: "外网接口",
  lan_iface: "内网接口",
  wan_vlanid: "外网 VLAN",
  lan_vlanid: "内网 VLAN",
  nat_port: "转换端口",
  nat_addr: "转换地址",
  nat_addr_int: "转换地址",
  dst_addr_int: "目标地址",
  src_addr_int: "源地址",
  ointerface: "出接口",
  preferred_lft: "首选有效期",
  valid_lft: "有效期",
  prefix: "前缀",
  prefix_hint: "前缀提示",
  dhcp6_info: "DHCPv6 信息",
  dhcp6_ip_gateway: "DHCPv6 网关",
  dhcp6_dns1: "DHCPv6 主 DNS",
  dhcp6_dns2: "DHCPv6 备用 DNS",
  dhcp6_prefix1: "DHCPv6 前缀 1",
  dhcp6_prefix2: "DHCPv6 前缀 2",
  link_addr: "链路地址",
  force_prefix: "强制前缀",
  force_gen_duid: "强制生成 DUID",
  route_table: "路由表",
  route_static: "静态路由",
  vpn_pptp_client: "PPTP 客户端",
  vpn_l2tp_client: "L2TP 客户端",
  vpn_openvpn_client: "OpenVPN 客户端",
  ipgroup: "IP 分组",
  macgroup: "MAC 分组",
  upnp_status: "UPnP 状态",
  igmp_proxy: "IGMP 代理",
  iptv: "IPTV 透传",
};

const ikuaiHiddenColumns = new Set([
  "id",
  "timestamp",
  "ip_addr_int",
  "client_typeid",
  "client_device",
  "auth_type",
  "client_type",
  "reject",
  "ac_gid",
  "webid",
  "passwd",
  "pass",
  "password",
  "secret",
  "radius_key",
  "group_key",
  "ldap_admin_passwd",
  "custom_appkey",
  "api_url",
  "api_ipchange_url",
  "whiteip",
  "whitelist",
  "whitelist_https",
  "noauth_mac",
]);

function ikuaiColumnLabel(key) {
  if (ikuaiColumnLabels[key]) return ikuaiColumnLabels[key];
  const normalizedKey = String(key || "").toLowerCase();
  if (ikuaiColumnLabels[normalizedKey]) return ikuaiColumnLabels[normalizedKey];
  const tokenLabels = {
    addr: "地址",
    address: "地址",
    account: "账号",
    action: "动作",
    ap: "AP",
    app: "应用",
    auth: "认证",
    auto: "自动",
    bind: "绑定",
    bytes: "流量",
    check: "检测",
    client: "客户端",
    code: "码",
    config: "配置",
    connect: "连接",
    count: "数量",
    data: "数据",
    device: "设备",
    dhcp: "DHCP",
    dns: "DNS",
    domain: "域名",
    down: "下行",
    download: "下行",
    dst: "目标",
    enable: "启用",
    enabled: "状态",
    event: "事件",
    failure: "失败次数",
    flow: "流量",
    force: "强制",
    gateway: "网关",
    group: "组",
    host: "主机",
    iface: "网卡",
    ifname: "接口",
    interval: "间隔",
    ip: "IP",
    key: "密钥",
    lan: "内网",
    line: "线路",
    local: "本地",
    mac: "MAC",
    mask: "掩码",
    max: "最大",
    min: "最小",
    mode: "模式",
    mtu: "MTU",
    name: "名称",
    noauth: "免认证",
    online: "在线",
    openapi: "开放接口",
    packet: "数据包",
    phone: "手机",
    pool: "地址池",
    port: "端口",
    ppp: "PPP",
    pppoe: "PPPoE",
    proto: "协议",
    protocol: "协议",
    radius: "RADIUS",
    rate: "速率",
    redpacket: "红包",
    remote: "远端",
    res: "结果",
    restart: "重启",
    rx: "接收",
    server: "服务端",
    session: "会话",
    src: "源",
    ssid: "SSID",
    static: "静态",
    status: "状态",
    state: "状态",
    speed: "速率",
    duplex: "双工",
    delay: "延迟",
    lease: "租约",
    prefix: "前缀",
    preferred: "首选",
    valid: "有效",
    visit: "访问",
    qos: "QoS",
    opttype12: "DHCP Option 12",
    opttype15: "DHCP Option 15",
    opttype28: "DHCP Option 28",
    opttype43: "DHCP Option 43",
    opttype60: "DHCP Option 60",
    opttype61: "DHCP Option 61",
    opttype66: "DHCP Option 66",
    opttype67: "DHCP Option 67",
    opttype80: "DHCP Option 80",
    bandmode: "绑定模式",
    linkmode: "链路模式",
    wifiwisp: "无线中继",
    time: "时间",
    timeout: "超时",
    timer: "定时",
    total: "总计",
    tx: "发送",
    type: "类型",
    up: "上行",
    upload: "上行",
    url: "地址",
    user: "用户",
    vlan: "VLAN",
    wan: "外网",
    web: "WEB",
    ipv6: "IPv6",
    igmp: "IGMP",
    iptv: "IPTV",
    upnp: "UPnP",
    nat: "NAT",
    duid: "DUID",
    upstream: "上游",
    downstream: "下游",
    week: "星期",
    weibo: "微博",
    weixin: "微信",
  };
  return normalizedKey.split("_").map((token) => tokenLabels[token] || "参数").join("");
}

function ikuaiTruthy(value) {
  const raw = String(value ?? "").trim().toLowerCase();
  if (["1", "yes", "true", "on", "enable", "enabled", "启用"].includes(raw)) return true;
  if (["0", "no", "false", "off", "disable", "disabled", "停用"].includes(raw)) return false;
  return Boolean(Number(value));
}

function formatIkuaiTimestamp(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return compactIkuaiValue(value);
  const ms = number < 100000000000 ? number * 1000 : number;
  return new Date(ms).toLocaleString("zh-CN", { hour12: false });
}

const ikuaiTimestampColumns = new Set([
  "time",
  "add_time",
  "update_time",
  "start_time",
  "create_time",
  "last_conntime",
  "last_offtime",
  "auth_time",
  "expires",
]);

function formatIkuaiCell(key, value) {
  if (value === null || value === undefined || value === "") return "-";
  if (["upload", "download", "up_rate", "down_rate"].includes(key) && Number.isFinite(Number(value))) return `${formatIkuaiBytes(value)}/s`;
  if (["total_up", "total_down"].includes(key) && Number.isFinite(Number(value))) return formatIkuaiBytes(value);
  if (key === "uptime" && Number.isFinite(Number(value))) return formatIkuaiUptime(value);
  if (key === "duration" && Number.isFinite(Number(value)) && Number(value) > 0) return formatIkuaiUptime(value);
  if (ikuaiTimestampColumns.has(key) && Number.isFinite(Number(value)) && Number(value) > 0) return formatIkuaiTimestamp(value);
  if (["enabled", "enable"].includes(key)) return ikuaiTruthy(value) ? "启用" : "停用";
  return compactIkuaiValue(value);
}

function ikuaiTableKeys(rows, section = state.ikuaiSection) {
  const authPriority = ["username", "name", "enabled", "ppptype", "server_name", "server_ip", "ip_addr", "interface", "packages", "packname", "expires", "start_time", "last_conntime", "upload", "download", "share", "phone", "comment"];
  const networkPriority = ["name", "interface", "ifname", "enabled", "status", "ip_addr", "dhcp_ip_addr", "pppoe_ip_addr", "ip_mask", "netmask", "gateway", "dhcp_gateway", "pppoe_gateway", "dns1", "dns2", "dhcp_dns1", "dhcp_dns2", "mac", "speed", "duplex", "upload", "download", "comment"];
  const defaultPriority = ["name", "hostname", "ip_addr", "mac", "username", "ifname", "interface", "status", "enabled", "upload", "download", "uptime", "comment"];
  const networkSections = new Set([
    "wan",
    "lan",
    "wifi",
    "mesh",
    "dhcp_server",
    "dhcp_addr_bind",
    "dhcp_lease",
    "dns",
    "multi_dns",
    "ipgroup",
    "macgroup",
    "route_static",
    "route_table",
    "vlan",
    "vpn_pptp_client",
    "vpn_l2tp_client",
    "vpn_openvpn_client",
    "upnp",
    "upnp_status",
    "nat_rule",
    "port_mapping",
    "ipv6",
    "igmp_proxy",
    "iptv",
  ]);
  const priority = section.startsWith("auth_") ? authPriority : (networkSections.has(section) ? networkPriority : defaultPriority);
  const keys = [...new Set(rows.flatMap((row) => Object.keys(row || {}).filter((key) => !ikuaiHiddenColumns.has(key) && ikuaiHasValue(row[key]))))];
  return [...priority.filter((key) => keys.includes(key)), ...keys.filter((key) => !priority.includes(key))].slice(0, 12);
}

const ikuaiEnabledOptions = [
  { value: "yes", label: "启用" },
  { value: "no", label: "停用" },
];
const ikuaiAuthTypeOptions = [
  { value: "pppoe", label: "PPPoE" },
  { value: "pppoe_proxy", label: "PPPoE透传" },
  { value: "web", label: "WEB账号" },
  { value: "pptp", label: "PPTP" },
  { value: "l2tp", label: "L2TP" },
  { value: "openvpn", label: "OpenVPN" },
  { value: "ikev2", label: "IKEv2" },
];
const ikuaiIpTypeOptions = [
  { value: "0", label: "不限" },
  { value: "1", label: "固定 IP" },
  { value: "2", label: "地址池" },
];
const ikuaiAuthModeOptions = [
  { value: "local", label: "本地账户认证" },
  { value: "localnopass", label: "本地账户+空密码" },
  { value: "any", label: "任意用户拨号" },
  { value: "radius", label: "第三方 RADIUS" },
];
const ikuaiCycleOptions = [
  { value: "month", label: "月" },
  { value: "day", label: "天" },
  { value: "hour", label: "小时" },
];

function ikuaiRawValue(row = {}, key, fallback = "") {
  const value = row?.[key];
  if (value === undefined || value === null) return fallback;
  return String(value);
}

function ikuaiOptionField(name, label, options, value = "") {
  return { name, label, type: "select", options, value: String(value ?? "") };
}

function ikuaiEnabledField(row, name, label, fallback = "yes") {
  const raw = ikuaiRawValue(row, name, "");
  return ikuaiOptionField(name, label, ikuaiEnabledOptions, raw === "" ? fallback : (ikuaiTruthy(raw) ? "yes" : "no"));
}

function ikuaiTextField(row, name, label, options = {}) {
  return { name, label, value: ikuaiRawValue(row, name, options.value || ""), ...options };
}

function ikuaiSelectField(row, name, label, options, fallback = "") {
  return { name, label, type: "select", options, value: ikuaiRawValue(row, name, fallback) };
}

function ikuaiHiddenField(row, name = "id") {
  return { name, type: "hidden", value: ikuaiRawValue(row, name) };
}

function ikuaiInputValue(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

const ikuaiBooleanFieldKeys = new Set([
  "enabled",
  "enable",
  "force_pppoe",
  "force_verify_name",
  "bind_vlan",
  "verify_vlan",
  "bind_iface",
  "rate_limit_lan",
  "drop_client",
  "enhance_check",
  "timer_restart",
  "api_switch",
  "auto_auth",
  "custom_auth",
  "user_auth",
  "phone_auth",
  "coupon_auth",
  "static_pwd",
  "nopasswd",
  "weixin",
  "qq_auth",
  "weibo_auth",
  "twitter_auth",
  "google_auth",
  "facebook_auth",
  "redpacket_auth",
  "hotel_auth",
  "openapi_auth",
  "force_prefix",
  "force_gen_duid",
]);

function ikuaiGenericFields(row = {}) {
  const fields = [];
  if (row.id !== undefined && row.id !== null && row.id !== "") fields.push(ikuaiHiddenField(row));
  if (state.ikuaiMatched?.func_name) fields.push({ name: "_func_name", type: "hidden", value: state.ikuaiMatched.func_name });
  Object.keys(row || {})
    .filter((key) => key !== "id" && !ikuaiHiddenColumns.has(key) && !key.startsWith("_"))
    .slice(0, 60)
    .forEach((key) => {
      if (ikuaiBooleanFieldKeys.has(key)) {
        fields.push(ikuaiEnabledField(row, key, ikuaiColumnLabel(key), ""));
        return;
      }
      const value = row[key];
      const isLong = typeof value === "object" || String(value ?? "").length > 80;
      fields.push({
        name: key,
        label: ikuaiColumnLabel(key),
        value: ikuaiInputValue(value),
        type: isLong ? "textarea" : "text",
        wide: isLong || ["comment", "addr_pool", "description"].includes(key),
      });
    });
  return fields;
}

function ikuaiAccountFields(row = {}, mode = "update") {
  return [
    mode === "create" ? null : ikuaiHiddenField(row),
    ikuaiTextField(row, "username", "账号", { required: true }),
    { name: "passwd", label: "密码", type: "password", value: "", placeholder: mode === "create" ? "" : "留空不修改", required: mode === "create" },
    ikuaiSelectField(row, "ppptype", "认证类型", ikuaiAuthTypeOptions, "pppoe"),
    ikuaiEnabledField(row, "enabled", "状态", "yes"),
    ikuaiTextField(row, "packages", "套餐"),
    ikuaiTextField(row, "start_time", "开始时间戳"),
    ikuaiTextField(row, "expires", "到期时间戳"),
    ikuaiTextField(row, "share", "共享数"),
    ikuaiTextField(row, "upload", "上行速率"),
    ikuaiTextField(row, "download", "下行速率"),
    ikuaiSelectField(row, "ip_type", "IP绑定类型", ikuaiIpTypeOptions, "0"),
    ikuaiTextField(row, "ip_addr", "绑定 IP"),
    ikuaiTextField(row, "mac", "绑定 MAC"),
    ikuaiTextField(row, "bind_vlanid", "绑定 VLAN"),
    ikuaiTextField(row, "bind_ifname", "绑定网卡"),
    ikuaiTextField(row, "name", "姓名"),
    ikuaiTextField(row, "phone", "手机"),
    ikuaiTextField(row, "address", "地址", { wide: true }),
    ikuaiTextField(row, "comment", "备注", { type: "textarea", wide: true }),
  ].filter(Boolean);
}

function ikuaiPackageFields(row = {}, mode = "update") {
  return [
    mode === "create" ? null : ikuaiHiddenField(row),
    ikuaiTextField(row, "name", "套餐名称", { value: ikuaiRawValue(row, "name", ikuaiRawValue(row, "packname")), required: true }),
    ikuaiTextField(row, "duration", "有效期"),
    ikuaiSelectField(row, "cycle_type", "周期类型", ikuaiCycleOptions, "month"),
    ikuaiTextField(row, "price", "套餐价格", { value: ikuaiRawValue(row, "price", ikuaiRawValue(row, "money")) }),
    ikuaiTextField(row, "upload", "上行带宽"),
    ikuaiTextField(row, "download", "下行带宽"),
    ikuaiTextField(row, "comment", "备注", { type: "textarea", wide: true }),
  ].filter(Boolean);
}

function ikuaiServiceFields(row = {}) {
  return [
    ikuaiHiddenField(row),
    ikuaiEnabledField(row, "enabled", "服务端状态", "yes"),
    ikuaiTextField(row, "server_name", "服务名称"),
    ikuaiTextField(row, "server_ip", "服务器地址"),
    ikuaiTextField(row, "dns1", "主 DNS"),
    ikuaiTextField(row, "dns2", "备用 DNS"),
    ikuaiSelectField(row, "authmode", "认证方式", ikuaiAuthModeOptions, ikuaiRawValue(row, "authmode")),
    ikuaiTextField(row, "radius_ip", "RADIUS 地址"),
    { name: "secret", label: "RADIUS 密钥", type: "password", value: "", placeholder: "留空不修改" },
    ikuaiTextField(row, "authport", "认证端口"),
    ikuaiTextField(row, "accountport", "计费端口"),
    ikuaiTextField(row, "interface", "接口"),
    ikuaiTextField(row, "addr_pool", "客户端地址池", { type: "textarea", wide: true }),
    ikuaiEnabledField(row, "force_pppoe", "强制客户端拨号", ""),
    ikuaiEnabledField(row, "force_verify_name", "强制校验服务名", ""),
    ikuaiEnabledField(row, "bind_vlan", "支持账号绑定 VLAN", ""),
    ikuaiEnabledField(row, "bind_iface", "支持账号绑定网卡", ""),
    ikuaiTextField(row, "mtu", "MTU"),
    ikuaiTextField(row, "mru", "MRU"),
    ikuaiTextField(row, "comment", "备注", { type: "textarea", wide: true }),
  ];
}

function ikuaiWebFields(row = {}) {
  return [
    ikuaiHiddenField(row),
    ikuaiEnabledField(row, "enabled", "WEB认证状态", "yes"),
    ikuaiTextField(row, "interface", "接口"),
    ikuaiTextField(row, "authip_mode", "认证范围"),
    ikuaiEnabledField(row, "user_auth", "账号认证", ""),
    ikuaiEnabledField(row, "phone_auth", "短信认证", ""),
    ikuaiEnabledField(row, "static_pwd", "固定密码", ""),
    ikuaiEnabledField(row, "nopasswd", "免密码", ""),
    ikuaiEnabledField(row, "coupon_auth", "上网码认证", ""),
    ikuaiEnabledField(row, "weixin", "微信认证", ""),
    ikuaiTextField(row, "group_id", "认证组 ID"),
    { name: "group_key", label: "认证组秘钥", type: "password", value: "", placeholder: "留空不修改" },
    ikuaiTextField(row, "max_time", "最长在线时间"),
    ikuaiTextField(row, "idle_time", "空闲超时"),
  ];
}

const ikuaiReadOnlySections = new Set([
  "homepage",
  "monitor_lanip",
  "monitor_iface",
  "monitor_wan",
  "monitor_flow",
  "dhcp_lease",
  "upnp_status",
  "auth_online",
  "auth_notice",
  "behavior_log",
  "attack_defense",
  "syslog",
  "operation_log",
  "login_log",
  "auth_log",
  "reboot",
]);

function ikuaiSectionActionConfig(section, groups = []) {
  const configs = {
    auth_account: { create: true, update: true, toggle: true, delete: true, noun: "账号", fields: ikuaiAccountFields },
    auth_package: { create: true, update: true, delete: true, noun: "套餐", fields: ikuaiPackageFields },
    auth_web: { update: true, toggle: true, noun: "WEB认证服务", fields: ikuaiWebFields },
    auth_pppoe_server: { update: true, toggle: true, noun: "PPPoE服务端", fields: ikuaiServiceFields },
    auth_pptp_server: { update: true, toggle: true, noun: "PPTP服务端", fields: ikuaiServiceFields },
    auth_l2tp_server: { update: true, toggle: true, noun: "L2TP服务端", fields: ikuaiServiceFields },
    auth_proxy: { update: true, toggle: true, noun: "代拨服务", fields: ikuaiServiceFields },
  };
  if (configs[section]) return configs[section];
  const item = flattenIkuaiMenus(groups).find((menu) => menu.id === section);
  if (!item || item.local_page || ikuaiReadOnlySections.has(section)) return null;
  return { update: true, noun: item.label || "配置", fields: ikuaiGenericFields, generic: true };
}

function renderIkuaiRowActions(section, row, index, groups = []) {
  const config = ikuaiSectionActionConfig(section, groups);
  if (!config) return "";
  const enabled = ikuaiTruthy(row.enabled ?? row.enable);
  return `<td class="ikuai-actions-cell"><div class="actions">
    ${config.update ? `<button data-ikuai-row-action="update" data-ikuai-row-index="${index}">编辑</button>` : ""}
    ${config.toggle ? `<button data-ikuai-row-action="${enabled ? "disable" : "enable"}" data-ikuai-row-index="${index}">${enabled ? "停用" : "启用"}</button>` : ""}
    ${config.delete ? `<button class="danger" data-ikuai-row-action="delete" data-ikuai-row-index="${index}">删除</button>` : ""}
  </div></td>`;
}

function renderIkuaiSectionList(section, rows, groups = []) {
  const keys = ikuaiTableKeys(rows, section);
  const label = ikuaiMenuLabel(groups, section);
  const actionConfig = ikuaiSectionActionConfig(section, groups);
  if (!keys.length) {
    return `<div class="ikuai-section">
      ${actionConfig?.create ? `<div class="ikuai-toolbar"><div class="ikuai-section-stats"><span>${escapeHtml(label)}</span></div><button class="primary" data-ikuai-row-action="create">添加${escapeHtml(actionConfig.noun)}</button></div>` : ""}
      <div class="ikuai-empty-state"><strong>${escapeHtml(label)}</strong><p>当前页面没有可展示字段。</p></div>
    </div>`;
  }
  return `<div class="ikuai-section">
    <div class="ikuai-toolbar">
      <div class="ikuai-section-stats"><span>共 ${rows.length} 条</span><span>${escapeHtml(label)}</span></div>
      <div class="ikuai-toolbar-actions">
        <input data-ikuai-search placeholder="搜索当前列表">
        ${actionConfig?.create ? `<button class="primary" data-ikuai-row-action="create">添加${escapeHtml(actionConfig.noun)}</button>` : ""}
      </div>
    </div>
    <table class="ikuai-data-table"><thead><tr>${keys.map((key) => `<th>${escapeHtml(ikuaiColumnLabel(key))}</th>`).join("")}${actionConfig ? "<th>操作</th>" : ""}</tr></thead>
    <tbody>${rows.map((row, index) => {
      const haystack = keys.map((key) => formatIkuaiCell(key, row[key])).join(" ").toLowerCase();
      return `<tr data-ikuai-row="${escapeHtml(haystack)}">${keys.map((key) => `<td>${escapeHtml(formatIkuaiCell(key, row[key]))}</td>`).join("")}${renderIkuaiRowActions(section, row, index, groups)}</tr>`;
    }).join("")}</tbody></table>
    <div class="ikuai-filter-count" data-ikuai-filter-count></div>
  </div>`;
}

function ikuaiFriendlyError(message) {
  const text = String(message || "");
  if (/unknown TYPE|unknown func|not found|不存在|未找到/i.test(text)) {
    return "当前网关未启用该功能，或此页面在当前版本没有返回可读取的数据。";
  }
  return text || "读取当前页面失败。";
}

function renderIkuaiSectionError(section, message, groups = []) {
  const label = ikuaiMenuLabel(groups, section);
  return `<div class="ikuai-section">
    <div class="ikuai-empty-state">
      <strong>${escapeHtml(label)}</strong>
      <p>${escapeHtml(ikuaiFriendlyError(message))}</p>
    </div>
  </div>`;
}

function renderIkuaiLocalPage(item = {}) {
  const label = item.label || "";
  if (String(item.local_page || "").startsWith("auth_")) {
    return `<div class="ikuai-section">
      <div class="ikuai-empty-state">
        <strong>${escapeHtml(label)}</strong>
        <p>当前页已按爱快 3.7 菜单保留入口；这台网关没有返回稳定的本地配置接口，暂不提交修改。</p>
      </div>
    </div>`;
  }
  if (String(item.local_page || "").startsWith("network_")) {
    return `<div class="ikuai-section">
      <div class="ikuai-empty-state">
        <strong>${escapeHtml(label)}</strong>
        <p>当前页已按爱快 3.7 网络设置菜单保留入口；这台网关没有返回稳定的本地配置接口，暂不提交修改。</p>
      </div>
    </div>`;
  }
  if (item.local_page === "packet") {
    return `<div class="ikuai-section">
      <div class="ikuai-tool-panel">
        <label>接口<select><option>自动选择</option></select></label>
        <label>过滤条件<input placeholder="host 192.168.1.10"></label>
        <button data-ikuai-tool-run>开始抓包</button>
      </div>
      <div class="ikuai-empty-state"><strong>${escapeHtml(label)}</strong><p>工具执行接口将在下一步接入，当前页面已按 3.7 UI 保留入口。</p></div>
    </div>`;
  }
  const placeholders = { ping: "192.168.1.1", trace: "www.baidu.com", nslookup: "www.baidu.com" };
  return `<div class="ikuai-section">
    <div class="ikuai-tool-panel">
      <label>目标地址<input value="${escapeHtml(placeholders[item.local_page] || "")}"></label>
      <button data-ikuai-tool-run>执行</button>
    </div>
    <div class="ikuai-empty-state"><strong>${escapeHtml(label)}</strong><p>工具执行接口将在下一步接入，当前页面已按 3.7 UI 保留入口。</p></div>
  </div>`;
}

function renderIkuaiResultTable(section, result, groups = []) {
  const rows = ikuaiRowsFromResult(section, result);
  state.ikuaiRows = rows;
  if (!rows.length) {
    if (ikuaiSectionActionConfig(section, groups)?.create) return renderIkuaiSectionList(section, rows, groups);
    return `<div class="ikuai-section"><div class="ikuai-empty-state"><strong>${escapeHtml(ikuaiMenuLabel(groups, section))}</strong><p>暂无数据</p></div></div>`;
  }
  if (section === "homepage") return renderIkuaiOverview(rows[0]);
  return renderIkuaiSectionList(section, rows, groups);
}

function renderIkuaiWorkspace(gateways = [], selected = null, menuGroups = [], activeMenu = null) {
  const gatewayList = gateways.length ? gateways.map((g) => {
    const summary = g.summary || {};
    const active = Number(g.id) === Number(state.ikuaiGatewayId);
    const chips = [
      summary.hostname ? ["主机名", summary.hostname] : null,
      summary.version ? ["版本", summary.version] : null,
      summary.ip_addr ? ["管理 IP", summary.ip_addr] : null,
      summary.wan?.ip_addr ? ["WAN IP", summary.wan.ip_addr] : null,
    ].filter(Boolean);
    return `<article class="ikuai-gateway-card ${active ? "active" : ""}">
      <div class="ikuai-gateway-top">
        <div><strong>${escapeHtml(g.name)}</strong><span>${escapeHtml(g.base_url)}</span></div>
        ${status(g.last_status || g.status)}
      </div>
      <dl class="ikuai-gateway-meta">
        <div><dt>账号</dt><dd>${escapeHtml(g.username_masked || "-")}</dd></div>
        ${chips.slice(0, 3).map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("")}
      </dl>
      <div class="actions ikuai-gateway-actions">
        <button class="${active ? "" : "primary"}" data-ikuai-select="${g.id}">${active ? "当前" : "管理"}</button>
        <button data-ikuai-refresh="${g.id}">刷新</button>
        <button data-ikuai-test="${g.id}">测试</button>
        <button data-ikuai-edit="${g.id}">编辑</button>
        <button class="danger" data-ikuai-delete="${g.id}">删除</button>
      </div>
    </article>`;
  }).join("") : `<div class="ikuai-rail-empty">还没有爱快网关</div>`;

  const menuHtml = selected ? menuGroups.map((group) => `<details class="ikuai-menu-group" ${group.id === activeMenu?.groupId ? "open" : ""}>
    <summary><span>${escapeHtml(group.label)}</span><em>${(group.items || []).length}</em></summary>
    <div>${(group.items || []).map((item) => `<button class="${state.ikuaiSection === item.id ? "active" : ""}" data-ikuai-section="${escapeHtml(item.id)}">${escapeHtml(item.label)}</button>`).join("")}</div>
  </details>`).join("") : "";

  const pageHtml = selected ? `<section class="ikuai-page">
    <div class="ikuai-page-head">
      <div><h3>${escapeHtml(ikuaiMenuLabel(menuGroups))}</h3><p>${escapeHtml(selected.base_url)}</p></div>
      <div class="actions"><button data-ikuai-refresh-section="${selected.id}">刷新当前页</button></div>
    </div>
    <div class="table-wrap" data-ikuai-section-result><div class="empty compact">正在读取 ${escapeHtml(ikuaiMenuLabel(menuGroups))}...</div></div>
  </section>` : `<section class="ikuai-page ikuai-page-empty">
    <div class="ikuai-empty-state"><strong>选择或添加网关</strong><p>添加爱快 Web 地址后，可以在这里集中查看和配置网关功能。</p></div>
  </section>`;

  return panel("爱快网关", `
    <div class="ikuai-workspace">
      <aside class="ikuai-rail">
        <div class="ikuai-rail-section">
          <div class="ikuai-rail-title"><strong>网关</strong><span>${gateways.length} 台</span></div>
          <div class="ikuai-gateway-list">${gatewayList}</div>
        </div>
        ${selected ? `<div class="ikuai-rail-section ikuai-menu-section"><div class="ikuai-rail-title"><strong>功能菜单</strong><span>${menuGroups.length} 组</span></div><div class="ikuai-menu">${menuHtml}</div></div>` : ""}
      </aside>
      ${pageHtml}
    </div>
  `, `<button id="addIkuaiBtn" class="primary">添加网关</button>`, "ikuai-workspace-panel");
}

async function submitIkuaiSectionAction(action, payload = {}) {
  if (!state.ikuaiGatewayId) throw new Error("请先选择爱快网关");
  await api(`/api/ikuai/gateways/${state.ikuaiGatewayId}/sections/${state.ikuaiSection}/actions`, {
    method: "POST",
    body: JSON.stringify({ action, payload }),
  });
  clearApiCache(`/api/ikuai/gateways/${state.ikuaiGatewayId}/sections/${state.ikuaiSection}`);
  clearApiCache("/api/operations");
  await render();
}

function openIkuaiActionDialog(action, row = {}) {
  const config = ikuaiSectionActionConfig(state.ikuaiSection, state.ikuaiMenuGroups);
  if (!config) return toast("当前页面暂未接入编辑操作");
  const create = action === "create";
  const titleAction = create ? "添加" : "编辑";
  const fields = config.fields(row, create ? "create" : "update");
  openFieldsDialog(`${titleAction}${config.noun}`, "", fields, async (data) => {
    const payload = create ? data : { ...row, ...data };
    if (state.ikuaiMatched?.func_name) payload._func_name = state.ikuaiMatched.func_name;
    await submitIkuaiSectionAction(create ? "create" : "update", payload);
    toast(create ? `${config.noun}已提交添加` : `${config.noun}已提交修改`);
  }, create ? "添加" : "保存修改");
}

function handleIkuaiRowAction(button) {
  const config = ikuaiSectionActionConfig(state.ikuaiSection, state.ikuaiMenuGroups);
  if (!config) return;
  const action = button.dataset.ikuaiRowAction;
  const row = state.ikuaiRows[Number(button.dataset.ikuaiRowIndex)] || {};
  if (action === "create" || action === "update") {
    return openIkuaiActionDialog(action, row);
  }
  if (action === "enable" || action === "disable") {
    const title = action === "enable" ? `启用${config.noun}` : `停用${config.noun}`;
    return confirmAction(title, `将直接提交到当前爱快网关：${row.username || row.name || row.server_name || row.id || config.noun}`, async () => {
      await submitIkuaiSectionAction(action, row);
      toast(action === "enable" ? "启用操作已提交" : "停用操作已提交");
    });
  }
  if (action === "delete") {
    return confirmAction(`删除${config.noun}`, `将直接从当前爱快网关删除：${row.username || row.name || row.server_name || row.id || config.noun}`, async () => {
      await submitIkuaiSectionAction("delete", row);
      toast("删除操作已提交");
    });
  }
}

async function renderIkuai(seq = state.renderSeq) {
  const [gateways, menuGroups] = await Promise.all([
    cachedApi("/api/ikuai/gateways", 8000),
    cachedApi("/api/ikuai/menus", 600000),
  ]);
  if (!isActiveRender(seq)) return;
  state.ikuaiMenuGroups = menuGroups;
  if (!state.ikuaiGatewayId && gateways.length) state.ikuaiGatewayId = Number(gateways[0].id);
  const allMenus = flattenIkuaiMenus(menuGroups);
  if (!allMenus.some((item) => item.id === state.ikuaiSection) && allMenus.length) {
    state.ikuaiSection = allMenus[0].id;
  }
  const selected = gateways.find((item) => Number(item.id) === Number(state.ikuaiGatewayId));
  const activeMenu = allMenus.find((item) => item.id === state.ikuaiSection);
  const cards = gateways.length ? `<div class="account-grid ikuai-grid">${gateways.map((g) => {
    const summary = g.summary || {};
    const chips = [
      summary.hostname ? ["主机名", summary.hostname] : null,
      summary.version ? ["版本", summary.version] : null,
      summary.ip_addr ? ["管理 IP", summary.ip_addr] : null,
      summary.wan?.ip_addr ? ["WAN IP", summary.wan.ip_addr] : null,
    ].filter(Boolean);
    return `<article class="account-card ${Number(g.id) === Number(state.ikuaiGatewayId) ? "selected-card" : ""}">
      <h3>${escapeHtml(g.name)}</h3>${status(g.last_status || g.status)}
      <dl>
        <dt>Web 地址</dt><dd>${escapeHtml(g.base_url)}</dd>
        <dt>登录账号</dt><dd>${escapeHtml(g.username_masked || "-")}</dd>
        ${chips.map(([label, value]) => `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd>`).join("")}
      </dl>
      <div class="actions">
        <button class="primary" data-ikuai-select="${g.id}">管理</button>
        <button data-ikuai-refresh="${g.id}">刷新</button>
        <button data-ikuai-test="${g.id}">测试登录</button>
        <button data-ikuai-edit="${g.id}">编辑</button>
        <button class="danger" data-ikuai-delete="${g.id}">删除</button>
      </div>
    </article>`;
  }).join("")}</div>` : `<div class="empty">还没有爱快网关，先添加一个 Web 管理地址。</div>`;
  let detail = "";
  if (selected) {
    const menuHtml = menuGroups.map((group) => `<details class="ikuai-menu-group" ${group.id === activeMenu?.groupId ? "open" : ""}>
      <summary><span>${escapeHtml(group.label)}</span><em>${(group.items || []).length}</em></summary>
      <div>${(group.items || []).map((item) => `<button class="${state.ikuaiSection === item.id ? "active" : ""}" data-ikuai-section="${escapeHtml(item.id)}">${escapeHtml(item.label)}</button>`).join("")}</div>
    </details>`).join("");
    detail = panel(`${escapeHtml(selected.name)} - 爱快管理`, `
      <div class="ikuai-console">
        <div class="ikuai-menu">${menuHtml}</div>
        <section class="ikuai-page">
          <div class="ikuai-page-head">
            <div><h3>${escapeHtml(ikuaiMenuLabel(menuGroups))}</h3><p>${escapeHtml(selected.base_url)}</p></div>
            <div class="actions"><button data-ikuai-refresh-section="${selected.id}">刷新当前页</button></div>
          </div>
          <div class="table-wrap" data-ikuai-section-result><div class="empty compact">正在读取 ${escapeHtml(ikuaiMenuLabel(menuGroups))}...</div></div>
        </section>
      </div>
    `);
  }
  content.innerHTML = renderIkuaiWorkspace(gateways, selected, menuGroups, activeMenu);
  bindIkuaiActions(gateways);
  if (selected) {
    const resultTarget = content.querySelector("[data-ikuai-section-result]");
    if (activeMenu?.local_page) {
      state.ikuaiRows = [];
      state.ikuaiMatched = null;
      if (resultTarget) {
        resultTarget.innerHTML = renderIkuaiLocalPage(activeMenu);
        bindIkuaiResultActions(resultTarget);
      }
      return;
    }
    try {
      const section = await cachedApi(`/api/ikuai/gateways/${selected.id}/sections/${state.ikuaiSection}`, 15000);
      if (!isActiveRender(seq)) return;
      if (resultTarget) {
        state.ikuaiMatched = section.matched || null;
        resultTarget.innerHTML = renderIkuaiResultTable(state.ikuaiSection, section.result, menuGroups);
        bindIkuaiResultActions(resultTarget);
      }
    } catch (error) {
      if (!isActiveRender(seq)) return;
      if (resultTarget) {
        state.ikuaiRows = [];
        state.ikuaiMatched = null;
        resultTarget.innerHTML = renderIkuaiSectionError(state.ikuaiSection, error.message, menuGroups);
      }
    }
  }
}

function bindIkuaiResultActions(root = document) {
  root.querySelectorAll("[data-ikuai-tool-run]").forEach((button) => {
    button.onclick = () => toast("该工具执行接口下一步接入");
  });
  root.querySelectorAll("[data-ikuai-row-action]").forEach((button) => {
    button.onclick = () => handleIkuaiRowAction(button);
  });
  const input = root.querySelector("[data-ikuai-search]");
  if (!input) return;
  const rows = Array.from(root.querySelectorAll("[data-ikuai-row]"));
  const counter = root.querySelector("[data-ikuai-filter-count]");
  input.oninput = () => {
    const query = input.value.trim().toLowerCase();
    let visible = 0;
    rows.forEach((row) => {
      const matched = !query || row.dataset.ikuaiRow.includes(query);
      row.hidden = !matched;
      if (matched) visible += 1;
    });
    if (counter) counter.textContent = query ? `显示 ${visible} 条` : "";
  };
}

function bindIkuaiActions(gateways = []) {
  $("#addIkuaiBtn")?.addEventListener("click", () => openIkuaiDialog());
  document.querySelectorAll("[data-ikuai-select]").forEach((button) => button.onclick = async () => {
    state.ikuaiGatewayId = Number(button.dataset.ikuaiSelect);
    localStorage.setItem("ctyun:ikuaiGatewayId", String(state.ikuaiGatewayId));
    await render();
  });
  document.querySelectorAll("[data-ikuai-section]").forEach((button) => button.onclick = async () => {
    state.ikuaiSection = button.dataset.ikuaiSection;
    localStorage.setItem("ctyun:ikuaiSection", state.ikuaiSection);
    await render();
  });
  document.querySelectorAll("[data-ikuai-refresh-section]").forEach((button) => button.onclick = async () => {
    clearApiCache(`/api/ikuai/gateways/${button.dataset.ikuaiRefreshSection}/sections/${state.ikuaiSection}`);
    await render();
  });
  document.querySelectorAll("[data-ikuai-edit]").forEach((button) => button.onclick = () => openIkuaiDialog(Number(button.dataset.ikuaiEdit), gateways));
  document.querySelectorAll("[data-ikuai-test]").forEach((button) => button.onclick = async () => {
    try {
      await api(`/api/ikuai/gateways/${button.dataset.ikuaiTest}/test`, { method: "POST" });
      toast("爱快登录测试成功");
    } catch (error) {
      toast(error.message);
    }
  });
  document.querySelectorAll("[data-ikuai-refresh]").forEach((button) => button.onclick = async () => {
    try {
      await withPageLoading("正在刷新爱快网关...", async () => {
        await api(`/api/ikuai/gateways/${button.dataset.ikuaiRefresh}/refresh`, { method: "POST" });
        clearApiCache("/api/ikuai");
        await render();
      });
      toast("爱快网关状态已刷新");
    } catch (error) {
      toast(error.message);
    }
  });
  document.querySelectorAll("[data-ikuai-delete]").forEach((button) => button.onclick = () => confirmAction(
    "删除爱快网关",
    "将删除该爱快网关在平台里的登录资料和缓存摘要，不会修改网关本身配置。",
    async () => {
      await api(`/api/ikuai/gateways/${button.dataset.ikuaiDelete}`, { method: "DELETE" });
      clearApiCache("/api/ikuai");
      await render();
      toast("爱快网关已删除");
    }
  ));
}

function openIkuaiDialog(id = 0, gateways = []) {
  const gateway = id ? gateways.find((item) => Number(item.id) === Number(id)) : null;
  const form = $("#ikuaiForm");
  form.elements.id.value = gateway?.id || "";
  form.elements.name.value = gateway?.name || "";
  form.elements.base_url.value = gateway?.base_url || "";
  form.elements.username.value = "";
  form.elements.password.value = "";
  form.elements.notes.value = gateway?.notes || "";
  $("#ikuaiDialogTitle").textContent = gateway ? "编辑爱快网关" : "添加爱快网关";
  $("#ikuaiError").textContent = gateway ? "不修改账号或密码时保持为空。" : "";
  $("#ikuaiDialog").showModal();
}

async function render() {
  const seq = ++state.renderSeq;
  const [title, subtitle] = viewInfo[state.view];
  $("#pageTitle").textContent = title;
  $("#pageSubtitle").textContent = subtitle;
  $("#nav").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.view === state.view));
  if (state.view === "dashboard") return renderDashboard(seq);
  if (state.view === "accounts") return renderAccounts(seq);
  if (state.view === "vpc") return renderVpcNetwork(seq);
  if (state.view === "image") return renderImages(seq);
  if (state.view === "ikuai") return renderIkuai(seq);
  if (state.view === "rustdesk") return renderRustdesk(seq);
  if (["ecs", "eip"].includes(state.view)) return renderResources(state.view, seq);
  if (state.view === "recycle") return renderRecycle(seq);
  return renderOperations(seq);
}

function viewResourcePaths(view = state.view) {
  const id = selectedAccountId();
  const query = id ? `?account_id=${id}` : "";
  if (view === "ecs" || view === "eip") return [`/api/resources/${view}${query}`];
  if (view === "image") return [`/api/resources/image${query}`];
  if (view === "vpc") {
    return ["vpc", "subnet", "vip", "ecs", "eip", "security_group"].map((type) => `/api/resources/${type}${query}`);
  }
  if (view === "operations" || view === "recycle") return ["/api/operations"];
  return [];
}

function prefetchResourceViews() {
  if (document.hidden || !["ecs", "eip", "vpc", "image"].includes(state.view)) return;
  const paths = [...new Set(["ecs", "eip", "vpc", "image"].flatMap((view) => viewResourcePaths(view)))];
  paths
    .filter((path) => !hasFreshCache(path))
    .slice(0, 6)
    .forEach((path) => cachedApi(path, 90000).catch(() => {}));
}

async function renderWithOptionalLoading(message) {
  const paths = viewResourcePaths(state.view);
  if (paths.length && paths.every(hasFreshCache)) {
    await render();
    window.setTimeout(prefetchResourceViews, 80);
    return;
  }
  await withPageLoading(message, render);
  window.setTimeout(prefetchResourceViews, 80);
}

function bindCommonActions() {
  document.querySelectorAll("[data-sync]").forEach((b) => b.onclick = () => syncAccount(Number(b.dataset.sync)));
  document.querySelectorAll("[data-console]").forEach((b) => b.onclick = () => openOfficialConsole(Number(b.dataset.console)));
  document.querySelectorAll("[data-recharge]").forEach((b) => b.onclick = () => openRecharge(Number(b.dataset.recharge)));
  document.querySelectorAll("[data-balance]").forEach((b) => b.onclick = () => refreshBalance(Number(b.dataset.balance)));
  bindTotpActions();
  document.querySelectorAll("[data-edit-account]").forEach((b) => b.onclick = () => openAccountDialog(Number(b.dataset.editAccount)));
  document.querySelectorAll("[data-regions]").forEach((b) => b.onclick = () => showRegions(Number(b.dataset.regions)));
  document.querySelectorAll("[data-delete-account]").forEach((b) => b.onclick = () => confirmAction(
    "删除云账号", "将删除该账号在本平台中的加密凭证和资源缓存。", async () => {
      await api(`/api/accounts/${b.dataset.deleteAccount}`, { method: "DELETE" });
      await loadAccounts(); await render(); toast("账号已删除");
    }
  ));
}

async function refreshBalance(id) {
  toast(`正在刷新 ${accountName(id)} 的余额...`);
  try {
    const result = await api(`/api/accounts/${id}/balance?t=${Date.now()}`);
    if (result.status !== "ready") throw new Error(result.message || "天翼云登录状态未建立");
    const amount = result.available === null || result.available === undefined
      ? "未获取"
      : `¥ ${Number(result.available).toFixed(2)}`;
    toast(`余额已更新：${amount}`);
    clearApiCache();
    await loadAccounts();
    await render();
  } catch (error) {
    toast(error.message);
  }
}

async function showRegions(id) {
  $("#regionsContent").innerHTML = `<div class="empty">正在查询 ${escapeHtml(accountName(id))} 可用资源池...</div>`;
  $("#regionsDialog").showModal();
  try {
    const regions = await api(`/api/accounts/${id}/regions`);
    $("#regionsContent").innerHTML = regions.length ? `<table><thead><tr><th>资源池名称</th><th>regionID</th><th>操作</th></tr></thead><tbody>${regions.map((r) => `<tr><td>${escapeHtml(r.regionName || "-")}</td><td><code>${escapeHtml(r.regionID)}</code></td><td><button data-copy-region="${escapeHtml(r.regionID)}">复制</button></td></tr>`).join("")}</tbody></table>` : `<div class="empty">没有查询到资源池</div>`;
    document.querySelectorAll("[data-copy-region]").forEach((button) => button.onclick = async () => {
      copyText(button.dataset.copyRegion);
      toast("regionID 已复制");
    });
  } catch (error) {
    $("#regionsContent").innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  }
}

function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).catch(() => fallbackCopy(text));
    return;
  }
  fallbackCopy(text);
}

function fallbackCopy(text) {
  const node = document.createElement("textarea");
  node.value = text;
  node.setAttribute("readonly", "");
  node.style.position = "fixed";
  node.style.left = "-9999px";
  document.body.appendChild(node);
  node.select();
  document.execCommand("copy");
  node.remove();
}

function fieldFormValues() {
  const form = $("#fieldsForm");
  const values = Object.fromEntries(new FormData(form));
  state.pendingFieldDefinitions.forEach((field) => {
    const input = form.elements[field.name];
    if (field.type === "checkbox") {
      values[field.name] = input?.checked ? "true" : "false";
    } else if (input && values[field.name] === undefined) {
      values[field.name] = input.value || "";
    }
  });
  return values;
}

function flattenOptions(options = []) {
  return options.flatMap((option) => Array.isArray(option.options) ? flattenOptions(option.options) : [option]);
}

function ensureFlatOptions(options = []) {
  return flattenOptions(options).filter((option) => option && !Array.isArray(option.options));
}

function defaultResourceName(prefix) {
  const now = new Date();
  const pad = (value) => String(value).padStart(2, "0");
  const stamp = [
    String(now.getFullYear()).slice(2),
    pad(now.getMonth() + 1),
    pad(now.getDate()),
    pad(now.getHours()),
    pad(now.getMinutes()),
  ].join("");
  return `${prefix}-${stamp}`;
}

function fieldOptionHtml(option, selectedValue = "") {
  const selected = String(option.value) === String(selectedValue) ? "selected" : "";
  const disabled = option.disabled ? "disabled" : "";
  return `<option value="${escapeHtml(option.value)}" ${selected} ${disabled}>${escapeHtml(option.label)}</option>`;
}

function fieldOptionsHtml(options = [], selectedValue = "", parentLabel = "") {
  return options.map((option) => {
    if (Array.isArray(option.options)) {
      const groupLabel = parentLabel ? `${parentLabel} / ${option.label}` : option.label;
      const hasNestedGroups = option.options.some((child) => Array.isArray(child.options));
      if (hasNestedGroups) return fieldOptionsHtml(option.options, selectedValue, groupLabel);
      return `<optgroup label="${escapeHtml(groupLabel)}">${fieldOptionsHtml(option.options, selectedValue, groupLabel)}</optgroup>`;
    }
    return fieldOptionHtml(option, selectedValue);
  }).join("");
}

function optionFilterText(option) {
  if (Array.isArray(option.options)) {
    return `${option.label || ""} ${option.options.map(optionFilterText).join(" ")}`.toLowerCase();
  }
  return [
    option.label,
    option.value,
    option.meta ? JSON.stringify(option.meta) : "",
  ].filter(Boolean).join(" ").toLowerCase();
}

function flavorCpu(option) {
  return option.meta?.cpuNum ?? option.meta?.vcpus ?? option.meta?.cpu ?? "";
}

function flavorMem(option) {
  return option.meta?.memSize ?? option.meta?.memory ?? option.meta?.ram ?? "";
}

function flavorSpecName(option) {
  return option.meta?.specName || option.meta?.flavorName || option.label || "";
}

function flavorArchitecture(option) {
  const meta = option.meta || {};
  const text = [
    meta.cpuArch,
    meta.arch,
    meta.architecture,
    meta.cpuArchitecture,
    option.label,
    meta.specName,
  ].filter(Boolean).join(" ").toLowerCase();
  if (/arm|aarch|kunpeng|鲲鹏/.test(text)) return "ARM计算";
  return "X86计算";
}

function flavorFamilyCode(option) {
  const meta = option.meta || {};
  const spec = String(meta.spec_name || meta.specName || option.value || meta.flavor_id || meta.flavorID || option.label || "").trim();
  const code = spec.match(/^[a-z]+[0-9]+/i)?.[0];
  if (code) return code.toUpperCase();
  const raw = String(meta.series || meta.flavorType || meta.flavor_type || "").replace(/^CPU[_-]?/i, "").trim();
  return raw.match(/^[a-z]+[0-9]+/i)?.[0]?.toUpperCase() || "";
}

function flavorSeriesName(option) {
  const meta = option.meta || {};
  const explicit = [
    meta.seriesName,
    meta.familyName,
    meta.flavorFamilyName,
    meta.flavorSeriesName,
    meta.flavor_type_name,
    meta.typeName,
  ].find((value) => value && !/^[a-z]+\d+\./i.test(String(value)) && !String(value).includes("."));
  if (explicit) return String(explicit).replace(/\s+/g, "");
  const spec = String(meta.specName || option.label || "").toLowerCase();
  if (/^c\d/i.test(spec)) return "计算增强型";
  if (/^m\d/i.test(spec)) return "通用型";
  if (/^s\d/i.test(spec)) return "通用型";
  if (/^g\d/i.test(spec)) return "通用型";
  return flavorCategory(option) === "GPU加速/AI加速型" ? "GPU加速型" : "通用型";
}

function flavorFamily(option) {
  const code = flavorFamilyCode(option);
  const base = flavorSeriesName(option);
  if (!code) return base;
  const lowerCode = code.toLowerCase();
  const baseText = String(base || "");
  if (baseText.toLowerCase().endsWith(lowerCode)) {
    return baseText.replace(new RegExp(`${code}$`, "i"), lowerCode);
  }
  return `${baseText}${lowerCode}`;
}

function flavorFamilySortKey(name = "") {
  const text = String(name);
  const match = text.match(/([a-z]+)(\d+)/i);
  if (!match) return [9, 0, text];
  const series = match[1].toLowerCase();
  const version = Number(match[2] || 0);
  const seriesOrder = { s: 0, g: 0, m: 1, c: 2 };
  return [seriesOrder[series] ?? 8, -version, text];
}

function sortFlavorFamilies(values = []) {
  return [...values].sort((a, b) => {
    const ak = flavorFamilySortKey(a);
    const bk = flavorFamilySortKey(b);
    return ak[0] - bk[0] || ak[1] - bk[1] || String(ak[2]).localeCompare(String(bk[2]), "zh-CN");
  });
}

function flavorCategory(option) {
  const meta = option.meta || {};
  const text = [
    option.label,
    meta.specName,
    meta.flavorName,
    meta.flavorType,
    meta.flavor_type,
    meta.seriesName,
    meta.familyName,
    meta.flavorFamilyName,
    meta.flavorSeriesName,
    meta.category,
    meta.categoryName,
    meta.series,
    meta.gpuName,
    meta.gpuType,
  ].filter(Boolean).join(" ").toLowerCase();
  if (meta.gpuName || meta.gpuType || /gpu|ai|npu|cuda|vgn|graphics|acceler|图像|加速/i.test(text)) return "GPU加速/AI加速型";
  if (/通用|general|standard/.test(text)) return "通用型";
  if (/计算型|计算增强|compute\s*optimized|^c\d/.test(text)) return "计算型";
  if (/内存型|内存优化|memory\s*optimized/.test(text)) return "内存型";
  return "通用型";
}

function supportedCreateFlavor(option) {
  if (flavorArchitecture(option) !== "X86计算") return false;
  if (flavorCategory(option) !== "通用型") return false;
  return true;
}

function uniqueSorted(values, numeric = false) {
  const list = [...new Set(values.filter((value) => value !== undefined && value !== null && value !== ""))];
  return list.sort((a, b) => numeric ? Number(a) - Number(b) : String(a).localeCompare(String(b), "zh-CN"));
}

function groupFlavorOptions(options = []) {
  const flat = ensureFlatOptions(options);
  return flat.sort((a, b) => {
    const familyCompare = flavorFamily(a).localeCompare(flavorFamily(b), "zh-CN");
    if (familyCompare) return familyCompare;
    const cpuCompare = Number(flavorCpu(a) || 0) - Number(flavorCpu(b) || 0);
    if (cpuCompare) return cpuCompare;
    const memCompare = Number(flavorMem(a) || 0) - Number(flavorMem(b) || 0);
    if (memCompare) return memCompare;
    return String(a.label || "").localeCompare(String(b.label || ""), "zh-CN");
  });
}

function filterFlavorOptions(options = [], filters = {}) {
  const flat = ensureFlatOptions(options).filter(supportedCreateFlavor);
  const query = String(filters.name || "").trim().toLowerCase();
  return flat.filter((option) => {
    if (filters.arch && flavorArchitecture(option) !== filters.arch) return false;
    if (filters.cpu && String(flavorCpu(option)) !== String(filters.cpu)) return false;
    if (filters.mem && String(flavorMem(option)) !== String(filters.mem)) return false;
    if (filters.category && flavorCategory(option) !== filters.category) return false;
    if (filters.family && flavorFamily(option) !== filters.family) return false;
    return !query || optionFilterText(option).includes(query);
  });
}

function flavorFilterPanelHtml(field, options = [], filters = {}) {
  const flat = ensureFlatOptions(options).filter(supportedCreateFlavor);
  const scoped = flat.filter((option) => (
    (!filters.arch || flavorArchitecture(option) === filters.arch)
    && (!filters.category || flavorCategory(option) === filters.category)
  ));
  const cpus = uniqueSorted(scoped.map(flavorCpu), true);
  const mems = uniqueSorted(scoped.map(flavorMem), true);
  const families = sortFlavorFamilies(uniqueSorted(scoped
    .filter((option) => !filters.category || flavorCategory(option) === filters.category)
    .map(flavorFamily)));
  const selectHtml = (key, label, values, suffix = "") => `
    <label>${label}<select data-grouped-filter="${field.name}" data-filter-key="${key}">
      <option value="">全部</option>
      ${values.map((value) => `<option value="${escapeHtml(value)}" ${String(filters[key] || "") === String(value) ? "selected" : ""}>${escapeHtml(value)}${suffix}</option>`).join("")}
    </select></label>
  `;
  const chips = (key, values, allLabel = "") => `
    <div class="filter-row">
      <span>${key === "arch" ? "架构" : key === "category" ? "分类" : "规格族"}</span>
      <div class="filter-chips">
        ${allLabel ? `<button type="button" class="${filters[key] ? "" : "active"}" data-grouped-filter="${field.name}" data-filter-key="${key}" data-filter-value="">${allLabel}</button>` : ""}
        ${values.map((value) => `<button type="button" class="${String(filters[key] || "") === String(value) ? "active" : ""}" data-grouped-filter="${field.name}" data-filter-key="${key}" data-filter-value="${escapeHtml(value)}">${escapeHtml(value)}</button>`).join("")}
      </div>
    </div>
  `;
  return `
    <div class="flavor-filter-panel">
      <div class="filter-grid">
        ${selectHtml("cpu", "vCPU（核）", cpus)}
        ${selectHtml("mem", "内存", mems, "GB")}
        <label>规格名称<input type="search" data-grouped-filter="${field.name}" data-filter-key="name" value="${escapeHtml(filters.name || "")}" placeholder="搜索规格名称"></label>
      </div>
      ${chips("family", families, "全部实例类型")}
    </div>
  `;
}

function imageVisibility(option) {
  const meta = option.meta || {};
  return String(meta.visibility ?? meta.imageVisibilityCode ?? meta.imageVisibility ?? "").toLowerCase();
}

function imageTypeText(option) {
  const meta = option.meta || {};
  return String(
    meta.imageType
    ?? meta.image_type
    ?? meta.type
    ?? meta.visibility
    ?? meta.imageVisibility
    ?? meta.imageVisibilityCode
    ?? ""
  ).toLowerCase();
}

function imageSourceText(option) {
  const meta = option.meta || {};
  return [
    option.label,
    meta.imageName,
    meta.name,
    meta.imageType,
    meta.image_type,
    meta.type,
    meta.category,
    meta.catalog,
  ].filter(Boolean).join(" ").toLowerCase();
}

function publicImageDistro(option) {
  const meta = option.meta || {};
  const text = [
    option.label,
    meta.osDistro,
    meta.osType,
    meta.osVersion,
    meta.imageDisplayName,
    meta.imageName,
    meta.name,
  ].filter(Boolean).join(" ").toLowerCase();
  if (/ctyun\s*os|ctyunos|天翼云\s*os/.test(text)) return "CTyunOS";
  if (/ubuntu/.test(text)) return "Ubuntu";
  if (/debian/.test(text)) return "Debian";
  if (/centos|cent\s*os/.test(text)) return "CentOS";
  return "";
}

function imageCreateType(option) {
  const visibility = imageVisibility(option);
  const typeText = imageTypeText(option);
  const meta = option.meta || {};
  if (imageTypes.shared.values.includes(visibility) || /shared|share|共享/.test(typeText) || meta.source_user || meta.sourceUser || meta.sourceAccountID) return "共享镜像";
  if (imageTypes.private.values.includes(visibility) || /private|personal|私有/.test(typeText)) return "私有镜像";
  if (["1", "public"].includes(visibility) || /standard|public|system|common|公共|系统|ctyunos/.test(typeText)) return "公共镜像";
  return "公共镜像";
}

function allowedCreateImage(option) {
  const type = imageCreateType(option);
  if (!["公共镜像", "私有镜像", "共享镜像"].includes(type)) return false;
  if (type === "公共镜像" && !publicImageDistro(option)) return false;
  const typeText = imageTypeText(option);
  const sourceText = imageSourceText(option);
  if (/safe|security|market|app|application/.test(typeText)) return false;
  return !/安全产品镜像|应用镜像|市场镜像|market|security product|application image|app image/.test(sourceText);
}

function filterImageOptions(options = [], filters = {}, values = {}) {
  const flat = flattenOptions(options).filter(allowedCreateImage);
  const query = String(filters.name || "").trim().toLowerCase();
  return flat.filter((option) => {
    if (filters.type && imageCreateType(option) !== filters.type) return false;
    if (!imageMatchesSelectedFlavor(option, values)) return false;
    return !query || optionFilterText(option).includes(query);
  });
}

function imageFilterPanelHtml(field, options = [], filters = {}) {
  const types = ["公共镜像", "私有镜像", "共享镜像"];
  return `
    <div class="image-filter-panel">
      <div class="filter-row">
        <span>镜像类型</span>
        <div class="filter-chips">
          ${types.map((type) => `<button type="button" class="${String(filters.type || "公共镜像") === type ? "active" : ""}" data-grouped-filter="${field.name}" data-filter-key="type" data-filter-value="${escapeHtml(type)}">${escapeHtml(type)}</button>`).join("")}
        </div>
      </div>
      <label>镜像<input type="search" data-grouped-filter="${field.name}" data-filter-key="name" value="${escapeHtml(filters.name || "")}" placeholder="搜索镜像名称或系统"></label>
    </div>
  `;
}

function filterGroupedOptions(options = [], query = "") {
  const needle = String(query || "").trim().toLowerCase();
  if (!needle) return options;
  return options.map((option) => {
    if (!Array.isArray(option.options)) {
      return optionFilterText(option).includes(needle) ? option : null;
    }
    if (String(option.label || "").toLowerCase().includes(needle)) return option;
    const children = filterGroupedOptions(option.options, needle);
    return children.length ? { ...option, options: children } : null;
  }).filter(Boolean);
}

function groupedOptionButtons(options = [], selectedValue = "", forceOpen = false) {
  if (!options.length) return `<div class="grouped-select-empty">没有可选项</div>`;
  return options.map((option) => {
    if (Array.isArray(option.options)) {
      const count = flattenOptions(option.options).length;
      return `<details class="grouped-select-group" ${forceOpen ? "open" : ""}><summary><span>${escapeHtml(option.label)}</span><em>${count}</em></summary><div>${groupedOptionButtons(option.options, selectedValue, forceOpen)}</div></details>`;
    }
    const selected = String(option.value) === String(selectedValue) ? "selected" : "";
    const disabled = option.disabled ? "disabled" : "";
    const disabledClass = option.disabled ? "disabled" : "";
    return `<button type="button" class="${selected} ${disabledClass}" data-grouped-option="${escapeHtml(option.value)}" ${disabled}>${escapeHtml(option.label)}</button>`;
  }).join("");
}

function groupedSelectMenuHtml(field, options = [], selectedValue = "", query = "") {
  if (field.filterType === "flavor") {
    const filters = state.groupedSelectFilters.get(field.name) || {};
    const filtered = filterFlavorOptions(options, filters);
    return `
      ${flavorFilterPanelHtml(field, options, filters)}
      <div data-grouped-results="${field.name}">${groupedOptionButtons(filtered, selectedValue, false)}</div>
    `;
  }
  if (field.filterType === "image") {
    const filters = { type: "公共镜像", ...(state.groupedSelectFilters.get(field.name) || {}) };
    const filtered = groupImageOptions(filterImageOptions(options, filters, fieldFormValues()));
    return `
      ${imageFilterPanelHtml(field, options, filters)}
      <div data-grouped-results="${field.name}">${groupedOptionButtons(filtered, selectedValue, false)}</div>
    `;
  }
  const filtered = filterGroupedOptions(options, query);
  const search = field.searchableGroups ? `
    <div class="grouped-select-search">
      <input type="search" data-grouped-search="${field.name}" placeholder="搜索资源池、省份或 regionID" value="${escapeHtml(query)}">
      <button type="button" data-grouped-search-btn="${field.name}">搜索</button>
    </div>
    <div data-grouped-results="${field.name}">${groupedOptionButtons(filtered, selectedValue, Boolean(query))}</div>
  ` : groupedOptionButtons(filtered, selectedValue, Boolean(query));
  return search;
}

function fieldGroupedOptions(field) {
  return state.pendingFieldGroupedOptions.get(field.name) || field.options || [];
}

function defaultGroupedSelectFilter(field) {
  if (field.filterType === "image") return { type: "公共镜像" };
  if (field.filterType === "flavor") return { arch: "X86计算", category: "通用型" };
  return {};
}

function defaultOptionScore(option) {
  const meta = option.meta || {};
  const text = [
    option.label,
    option.value,
    meta.name,
    meta.vpcName,
    meta.subnetName,
    meta.securityGroupName,
    meta.origin,
    meta.type,
  ].filter(Boolean).join(" ").toLowerCase();
  if (meta.default === true || meta.isDefault === true || meta.origin === "default") return 3;
  if (/默认|default_network|default_subnet|^default(\b|_|\s|-)/i.test(text)) return 2;
  if (/default/i.test(text)) return 1;
  return 0;
}

function regionResourceScore(option) {
  const count = Number(option.meta?.resourceCount ?? option.meta?.vpcCount ?? 0);
  if (count > 0) return count;
  const match = String(option.label || "").match(/（(\d+)\s*VPC）/i);
  return match ? Number(match[1]) : 0;
}

function preferredAutoOption(field, flatOptions = []) {
  if (!flatOptions.length) return null;
  const candidates = flatOptions.filter((option) => !option.disabled);
  const pool = candidates.length ? candidates : flatOptions;
  if (!field.preferDefaultOption) return pool[0];
  return [...pool].sort((a, b) => defaultOptionScore(b) - defaultOptionScore(a))[0] || pool[0];
}

function groupedSelectLabel(field, value = "") {
  const options = state.pendingFieldOptions.get(field.name) || [];
  const selected = options.find((option) => String(option.value) === String(value));
  return selected?.label || field.emptyLabel || (field.required ? "请选择" : "不选择");
}

function renderGroupedSelect(field, options = []) {
  if (!field.collapsibleGroups) return;
  const form = $("#fieldsForm");
  const select = form.elements[field.name];
  const wrapper = document.querySelector(`[data-field-wrap="${field.name}"]`);
  if (!select || !wrapper) return;
  const trigger = wrapper.querySelector(`[data-grouped-trigger="${field.name}"]`);
  const menu = wrapper.querySelector(`[data-grouped-menu="${field.name}"]`);
  if (!trigger || !menu) return;
  const value = select.value || "";
  trigger.textContent = groupedSelectLabel(field, value);
  trigger.classList.toggle("placeholder", !value);
  menu.onclick = (event) => event.stopPropagation();
  const bindOptionClicks = () => {
    menu.querySelectorAll("[data-grouped-option]").forEach((button) => {
      button.onclick = (event) => {
        event.preventDefault();
        event.stopPropagation();
        select.value = button.dataset.groupedOption || "";
        trigger.textContent = groupedSelectLabel(field, select.value);
        trigger.classList.toggle("placeholder", !select.value);
        menu.classList.add("hidden");
        select.dispatchEvent(new Event("change", { bubbles: true }));
      };
    });
  };
  const renderMenu = (query = "") => {
    menu.innerHTML = groupedSelectMenuHtml(field, options, select.value || "", query);
    bindOptionClicks();
    menu.querySelectorAll(`[data-grouped-filter="${field.name}"]`).forEach((control) => {
      const applyFilter = (event = null) => {
        event?.preventDefault?.();
        event?.stopPropagation?.();
        const key = control.dataset.filterKey;
        if (!key) return;
        const next = { ...(state.groupedSelectFilters.get(field.name) || {}) };
        next[key] = control.dataset.filterValue !== undefined ? control.dataset.filterValue : control.value;
    if (["arch", "category"].includes(key)) next.family = "";
        state.groupedSelectFilters.set(field.name, next);
        renderMenu(query);
        const refocus = menu.querySelector(`[data-grouped-filter="${field.name}"][data-filter-key="${key}"]`);
        if (refocus && refocus.tagName === "INPUT") {
          refocus.focus();
          refocus.setSelectionRange?.(refocus.value.length, refocus.value.length);
        }
      };
      if (control.tagName === "BUTTON") {
        control.onclick = applyFilter;
      } else {
        control.onchange = applyFilter;
        if (control.type === "search") control.oninput = applyFilter;
      }
    });
    const searchInput = menu.querySelector(`[data-grouped-search="${field.name}"]`);
    const searchButton = menu.querySelector(`[data-grouped-search-btn="${field.name}"]`);
    if (searchInput && searchButton) {
      searchButton.onclick = () => renderMenu(searchInput.value);
      searchInput.oninput = () => renderMenu(searchInput.value);
      searchInput.onkeydown = (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          renderMenu(searchInput.value);
        }
      };
      if (!menu.classList.contains("hidden")) searchInput.focus();
    }
  };
  renderMenu();
  menu.classList.add("hidden");
  trigger.onclick = (event) => {
    event.preventDefault();
    event.stopPropagation();
    document.querySelectorAll(".grouped-select-menu").forEach((node) => {
      if (node !== menu) node.classList.add("hidden");
    });
    menu.classList.toggle("hidden");
    if (!menu.classList.contains("hidden")) menu.querySelector(`[data-grouped-search="${field.name}"]`)?.focus();
  };
}

function setGroupedSelectLoading(field, text) {
  if (!field.collapsibleGroups) return;
  const wrapper = document.querySelector(`[data-field-wrap="${field.name}"]`);
  const trigger = wrapper?.querySelector(`[data-grouped-trigger="${field.name}"]`);
  const menu = wrapper?.querySelector(`[data-grouped-menu="${field.name}"]`);
  if (trigger) trigger.textContent = text;
  if (menu) {
    menu.innerHTML = `<div class="grouped-select-empty">${escapeHtml(text)}</div>`;
    menu.classList.add("hidden");
  }
}

function setDynamicFieldOptions(field, options) {
  const select = $("#fieldsForm").elements[field.name];
  if (!select) return;
  const previousValue = select.value || "";
  const current = select.value || field.value || "";
  const flatOptions = flattenOptions(options);
  state.pendingFieldOptions.set(field.name, flatOptions);
  state.pendingFieldGroupedOptions.set(field.name, options);
  const emptyLabel = field.emptyLabel ?? (field.required ? "请选择" : "不选择");
  select.innerHTML = `<option value="">${escapeHtml(emptyLabel)}</option>` + fieldOptionsHtml(options, current);
  if (flatOptions.some((option) => String(option.value) === String(current))) {
    select.value = current;
  } else if (field.autoSelectFirst !== false && flatOptions.length) {
    select.value = String(preferredAutoOption(field, flatOptions)?.value || "");
  } else {
    select.value = "";
  }
  renderGroupedSelect(field, options);
  return previousValue !== (select.value || "");
}

function applyFieldVisibility() {
  const values = fieldFormValues();
  state.pendingFieldDefinitions.forEach((field) => {
    const input = $("#fieldsForm").elements[field.name];
    const wrapper = document.querySelector(`[data-field-wrap="${field.name}"]`);
    if (!input || !wrapper) return;
    const visible = field.visibleWhen ? Boolean(field.visibleWhen(values)) : true;
    const required = field.requiredWhen ? Boolean(field.requiredWhen(values)) : Boolean(field.required);
    wrapper.classList.toggle("hidden", !visible);
    input.disabled = !visible;
    input.required = visible && required;
  });
}

async function loadDynamicField(field) {
  if (!field.loadOptions) return;
  const values = fieldFormValues();
  const select = $("#fieldsForm").elements[field.name];
  const wrapper = document.querySelector(`[data-field-wrap="${field.name}"]`);
  const seq = (state.pendingFieldLoadSeq.get(field.name) || 0) + 1;
  state.pendingFieldLoadSeq.set(field.name, seq);
  const isStale = () => state.pendingFieldLoadSeq.get(field.name) !== seq;
  const dependencyNames = [...(field.dependsOn || []), ...(field.refreshOn || [])];
  const dependencySig = JSON.stringify(dependencyNames.map((name) => [name, values[name] || ""]));
  if (field.filterType) {
    const previousSig = state.pendingFieldDependencySig.get(field.name);
    if (previousSig !== undefined && previousSig !== dependencySig) {
      state.groupedSelectFilters.set(field.name, defaultGroupedSelectFilter(field));
    }
    state.pendingFieldDependencySig.set(field.name, dependencySig);
  }
  if (field.visibleWhen && !field.visibleWhen(values)) {
    wrapper?.classList.remove("field-loading", "field-error-state");
    return false;
  }
  if ((field.dependsOn || []).some((name) => !values[name])) {
    if (!isStale()) setDynamicFieldOptions(field, []);
    wrapper?.classList.remove("field-loading", "field-error-state");
    return false;
  }
  const previousOptions = state.pendingFieldOptions.get(field.name) || [];
  select.disabled = true;
  wrapper?.classList.add("field-loading");
  wrapper?.classList.remove("field-error-state");
  select.innerHTML = `<option value="">正在读取...</option>`;
  setGroupedSelectLoading(field, "正在读取...");
  try {
    const options = await field.loadOptions(values);
    if (isStale()) return false;
    const changed = setDynamicFieldOptions(field, options || []);
    wrapper?.classList.remove("field-error-state");
    return changed;
  } catch (error) {
    if (isStale()) return false;
    setDynamicFieldOptions(field, previousOptions);
    wrapper?.classList.add("field-error-state");
    $("#fieldsError").textContent = error.message;
    return false;
  } finally {
    if (!isStale()) {
      select.disabled = false;
      wrapper?.classList.remove("field-loading");
      applyFieldVisibility();
    }
  }
}

async function refreshDependentFields(changedName, visited = new Set()) {
  if (visited.has(changedName)) return;
  visited.add(changedName);
  const fieldsByName = new Map(state.pendingFieldDefinitions.map((field) => [field.name, field]));
  const affected = new Set();
  let changed = true;
  while (changed) {
    changed = false;
    state.pendingFieldDefinitions.forEach((field) => {
      if (!field.loadOptions || affected.has(field.name)) return;
      const deps = [...(field.dependsOn || []), ...(field.refreshOn || [])];
      if (deps.includes(changedName) || deps.some((name) => affected.has(name))) {
        affected.add(field.name);
        changed = true;
      }
    });
  }
  const remaining = new Set(affected);
  while (remaining.size) {
    const ready = [...remaining]
      .map((name) => fieldsByName.get(name))
      .filter(Boolean)
      .filter((field) => {
        const deps = [...(field.dependsOn || []), ...(field.refreshOn || [])];
        return deps.every((name) => !remaining.has(name));
      });
    const batch = ready.length ? ready : [...remaining].map((name) => fieldsByName.get(name)).filter(Boolean);
    await Promise.all(batch.map((field) => loadDynamicField(field)));
    batch.forEach((field) => remaining.delete(field.name));
  }
  applyFieldVisibility();
}

async function initializeDynamicFields() {
  const fieldsByName = new Map(state.pendingFieldDefinitions.map((field) => [field.name, field]));
  const remaining = new Set(state.pendingFieldDefinitions.filter((field) => field.loadOptions).map((field) => field.name));
  while (remaining.size) {
    const ready = [...remaining]
      .map((name) => fieldsByName.get(name))
      .filter(Boolean)
      .filter((field) => (field.dependsOn || []).every((name) => !remaining.has(name)));
    const batch = ready.length ? ready : [...remaining].map((name) => fieldsByName.get(name)).filter(Boolean);
    await Promise.all(batch.map((field) => loadDynamicField(field)));
    batch.forEach((field) => remaining.delete(field.name));
  }
  applyFieldVisibility();
}

function openFieldsDialog(title, hint, fields, onSubmit, submitLabel = "提交", onChange = null) {
  $("#fieldsTitle").textContent = title;
  $("#fieldsHint").textContent = hint || "";
  $("#fieldsError").textContent = "";
  $("#fieldsContent").innerHTML = fields.map((field) => {
    if (field.type === "note") {
      return `<div data-field-wrap="${field.name || ""}" class="field-note ${field.wide ? "wide" : ""}">${field.html || escapeHtml(field.text || "")}</div>`;
    }
    if (field.type === "hidden") {
      return `<input type="hidden" name="${field.name}" value="${escapeHtml(field.value || "")}">`;
    }
    if (field.type === "checkbox") {
      const checked = field.checked !== false && field.value !== "false";
      return `<label data-field-wrap="${field.name}" class="checkbox-field ${field.wide ? "wide" : ""}"><input type="checkbox" name="${field.name}" value="true" ${checked ? "checked" : ""}><span>${escapeHtml(field.label)}</span></label>`;
    }
    if (field.type === "select") {
      const options = field.options || [];
      state.pendingFieldOptions.set(field.name, flattenOptions(options));
      const grouped = field.collapsibleGroups ? `
        <button type="button" class="grouped-select-trigger placeholder" data-grouped-trigger="${field.name}">${escapeHtml(field.emptyLabel ?? (field.required ? "请选择" : "不选择"))}</button>
        <div class="grouped-select-menu hidden" data-grouped-menu="${field.name}">${groupedOptionButtons(options, field.value || "")}</div>
      ` : "";
      return `<label data-field-wrap="${field.name}" class="${field.wide ? "wide" : ""} ${field.collapsibleGroups ? "grouped-select-field" : ""} ${field.filterType ? "filtered-select-field" : ""}">${escapeHtml(field.label)}<select name="${field.name}" class="${field.collapsibleGroups ? "native-select-proxy" : ""}" ${field.required ? "required" : ""}><option value="">${escapeHtml(field.emptyLabel ?? (field.required ? "请选择" : "不选择"))}</option>${fieldOptionsHtml(options, field.value || "")}</select>${grouped}</label>`;
    }
    if (field.type === "textarea") {
      return `<label data-field-wrap="${field.name}" class="${field.wide ? "wide" : ""}">${escapeHtml(field.label)}<textarea name="${field.name}" placeholder="${escapeHtml(field.placeholder || "")}" ${field.required ? "required" : ""}>${escapeHtml(field.value || "")}</textarea></label>`;
    }
    return `<label data-field-wrap="${field.name}" class="${field.wide ? "wide" : ""}">${escapeHtml(field.label)}<input type="${escapeHtml(field.type || "text")}" name="${field.name}" value="${escapeHtml(field.value || "")}" placeholder="${escapeHtml(field.placeholder || "")}" ${field.step ? `step="${escapeHtml(field.step)}"` : ""} ${field.min ? `min="${escapeHtml(field.min)}"` : ""} ${field.max ? `max="${escapeHtml(field.max)}"` : ""} ${field.inputmode ? `inputmode="${escapeHtml(field.inputmode)}"` : ""} ${field.required ? "required" : ""}></label>`;
  }).join("");
  state.pendingFieldDefinitions = fields;
  state.pendingFieldOptions = new Map(fields.filter((field) => field.options).map((field) => [field.name, flattenOptions(field.options)]));
  state.pendingFieldGroupedOptions = new Map(fields.filter((field) => field.options).map((field) => [field.name, field.options]));
  state.pendingFieldLoadSeq = new Map();
  state.pendingFieldDependencySig = new Map();
  state.groupedSelectFilters = new Map(fields.filter((field) => field.filterType).map((field) => {
    if (field.filterType === "image") return [field.name, { type: "公共镜像" }];
    if (field.filterType === "flavor") return [field.name, { arch: "X86计算", category: "通用型" }];
    return [field.name, {}];
  }));
  state.pendingFieldsAction = onSubmit;
  $("#fieldsSubmitBtn").textContent = submitLabel;
  $("#fieldsDialog").showModal();
  let onChangeTimer = 0;
  const scheduleLiveChange = () => {
    if (!onChange) return;
    window.clearTimeout(onChangeTimer);
    onChangeTimer = window.setTimeout(() => onChange(fieldFormValues()), 350);
  };
  fields.forEach((field) => {
    const input = $("#fieldsForm").elements[field.name];
    if (!input || field.type === "hidden") return;
    const flatOptions = flattenOptions(field.options || []);
    if (field.type === "select" && !field.loadOptions && !field.value && field.autoSelectFirst !== false && flatOptions.length) {
      input.value = String(flatOptions[0].value);
    }
    renderGroupedSelect(field, fieldGroupedOptions(field));
    input.addEventListener("change", async () => {
      if (field.name === "regionID") {
        rememberEcsRegion(formAccountId(fieldFormValues()), input.value);
        distributeEcsRegionPrewarm(formAccountId(fieldFormValues()), [input.value], 1);
        postponeEcsRegionPrewarm(1200);
      }
      renderGroupedSelect(field, fieldGroupedOptions(field));
      await refreshDependentFields(field.name);
      scheduleLiveChange();
    });
    if (["bootDiskSize", "cycleCount", "bandwidth", "quantity"].includes(field.name)) {
      input.addEventListener("input", scheduleLiveChange);
    }
  });
  initializeDynamicFields().then(() => {
    scheduleLiveChange();
  });
}

async function optionApi(accountId, kind, params = {}) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") query.set(key, value);
  });
  const ttlByKind = {
    regions: 600000,
    zones: 600000,
    flavors: 21600000,
    images: 21600000,
    disk_types: 21600000,
    eip_lines: 600000,
    eip_cycle_types: 600000,
    eip_demand_billing_types: 600000,
  };
  return cachedApi(`/api/accounts/${accountId}/options/${kind}${query.size ? `?${query}` : ""}`, ttlByKind[kind] || 120000);
}

async function eipAssociationOptions(accountId, regionID) {
  const query = `?account_id=${accountId}`;
  const [ecsRows, vipRows] = await Promise.all([
    cachedApi(`/api/resources/ecs${query}`, 90000).catch(() => []),
    cachedApi(`/api/resources/vip${query}`, 90000).catch(() => []),
  ]);
  const sameRegion = (row) => Number(row.account_id) === Number(accountId) && String(row.region || "") === String(regionID || "");
  const ecsOptions = ecsRows.filter(sameRegion).map((row) => ({
    value: row.provider_id,
    label: `${row.name} · ${row.payload?.private_ip || row.payload?.privateIP || row.payload?.public_ip || "云主机"}`,
    meta: { ...(row.payload || {}), associationType: 1 },
  }));
  const vipOptions = vipRows.filter(sameRegion).map((row) => ({
    value: row.provider_id,
    label: `${row.payload?.ip || row.payload?.ipv4 || row.name || row.provider_id} · 虚拟IP`,
    meta: { ...(row.payload || {}), associationType: 2 },
  }));
  return [...ecsOptions, ...vipOptions];
}

async function submitAction(accountId, resourceType, action, resourceId, payload = {}, options = {}) {
  const result = await api(`/api/accounts/${accountId}/actions`, {
    method: "POST",
    body: JSON.stringify({ resource_type: resourceType, action, resource_id: resourceId, payload }),
  });
  if (options.optimistic !== false) applyOptimisticResourceAction(accountId, resourceType, action, resourceId, payload);
  if (options.clearCache !== false) clearActionCaches(resourceType, action);
  if (options.toastMessage !== false) toast(options.toastMessage || "操作已提交");
  return result;
}

async function runLimited(items, limit, worker) {
  const results = [];
  let next = 0;
  const runners = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (next < items.length) {
      const index = next++;
      const item = items[index];
      try {
        results[index] = { ok: true, item, value: await worker(item, index) };
      } catch (error) {
        results[index] = { ok: false, item, error };
      }
    }
  });
  await Promise.all(runners);
  return results;
}

async function syncTypesForAccounts(types, accountId = 0, silent = true) {
  const ids = accountId ? [Number(accountId)] : state.accounts.map((a) => a.id);
  if (!ids.length || document.hidden) return { ok: false, errors: [], skipped: true };
  const scopeKey = `${ids.join(",")}:${[...types].sort().join(",")}`;
  state.refreshing = true;
  updateRefreshBadge("刷新中...");
  const errors = [];
  try {
    for (let offset = 0; offset < ids.length; offset += 5) {
      const batch = ids.slice(offset, offset + 5);
      await Promise.all(batch.map(async (id) => {
        try {
          await api(`/api/accounts/${id}/sync-types`, { method: "POST", body: JSON.stringify({ types }) });
        } catch (error) {
          errors.push(`${accountName(id)}: ${error.message}`);
        }
      }));
    }
    state.lastRefreshAt = Date.now();
    state.lastSyncByScope.set(scopeKey, state.lastRefreshAt);
    clearResourceCaches(types);
    if (errors.length && !silent) toast(errors[0]);
    return { ok: !errors.length, errors };
  } finally {
    state.refreshing = false;
    updateRefreshBadge();
    if (state.refreshQueued && !document.hidden) {
      state.refreshQueued = false;
      window.setTimeout(() => refreshCurrentView(true).catch(() => {}), 1200);
    }
  }
}

function viewMatchesResourceTypes(types) {
  if (state.view === "dashboard") return true;
  const paths = viewResourcePaths(state.view);
  return types.some((type) => paths.some((path) => path.startsWith(`/api/resources/${type}`)));
}

function schedulePostActionSync(types, accountId, delays = [800, 2500, 6000, 12000, 25000, 45000]) {
  const normalizedTypes = [...new Set(types)];
  const key = `${accountId || 0}:${normalizedTypes.sort().join(",")}`;
  (state.postActionSyncTimers.get(key) || []).forEach((timer) => window.clearTimeout(timer));
  const timers = delays.map((delay, index) => window.setTimeout(async () => {
    try {
      clearResourceCaches(normalizedTypes);
      await syncTypesForAccounts(normalizedTypes, accountId, true);
      if (viewMatchesResourceTypes(normalizedTypes)) await render();
      if (index === delays.length - 1) state.postActionSyncTimers.delete(key);
    } catch (error) {
      if (!document.hidden) toast(error.message);
    }
  }, delay));
  state.postActionSyncTimers.set(key, timers);
}

function resourceTypesAfterAction(resourceType, action) {
  if (resourceType === "ecs") {
    if (action === "create_image") return ["image"];
    if (["change_private_ip", "change_vpc"].includes(action)) return ["ecs", "vpc", "subnet", "security_group", "vip"];
    return ["ecs"];
  }
  if (resourceType === "eip") return ["eip", "ecs", "vip"];
  if (resourceType === "vip") return ["vip", "ecs", "eip"];
  if (resourceType === "vpc" || resourceType === "subnet" || resourceType === "security_group") {
    return ["vpc", "subnet", "vip", "security_group"];
  }
  return [resourceType];
}

const pendingStatusByAction = {
  create: "creating",
  update: "updating",
  rename: "updating",
  delete: "deleting",
  start: "starting",
  stop: "stopping",
  reboot: "rebooting",
  release: "releasing",
  unsubscribe: "unsubscribing",
  bind: "binding",
  unbind: "unbinding",
  copy: "copying",
  share: "sharing",
  unshare: "unsharing",
  accept: "accepting",
  reject: "rejecting",
  resize: "resizing",
  rebuild: "rebuilding",
  reset_password: "resetting",
  auto_renew: "updating",
  deletion_protection: "updating",
  create_image: "imaging",
  create_subnet: "creating",
  create_rule: "updating",
  delete_rule: "updating",
  change_private_ip: "updating",
  change_vpc: "updating",
  bind_ecs: "binding",
  bind_eip: "binding",
};

function resourceOverrideKey(type, accountId, resourceId) {
  return `${type}:${Number(accountId) || 0}:${String(resourceId || "")}`;
}

function pruneResourceOverrides() {
  const now = Date.now();
  state.resourceOverrides.forEach((value, key) => {
    if (value.expiresAt <= now) state.resourceOverrides.delete(key);
  });
}

function applyOptimisticResourceAction(accountId, resourceType, action, resourceId, payload = {}) {
  if (!resourceId) return;
  pruneResourceOverrides();
  const statusValue = pendingStatusByAction[action] || "processing";
  state.resourceOverrides.set(resourceOverrideKey(resourceType, accountId, resourceId), {
    accountId: Number(accountId) || 0,
    resourceType,
    action,
    resourceId: String(resourceId),
    payload: { ...(payload || {}) },
    status: statusValue,
    label: `${actionDisplayLabel(resourceType, action)}中`,
    expiresAt: Date.now() + 8 * 60 * 1000,
  });
}

function applyResourceOverrides(type, rows = []) {
  pruneResourceOverrides();
  return rows.map((row) => {
    const override = state.resourceOverrides.get(resourceOverrideKey(type, row.account_id, row.provider_id));
    if (!override) return row;
    const cloned = { ...row, payload: { ...(row.payload || {}) } };
    cloned.status = override.status;
    cloned.payload.instanceStatus = override.status;
    cloned.payload.status = override.status;
    cloned.payload.pendingAction = override.action;
    cloned.payload.pendingActionLabel = override.label;
    if (["update", "rename"].includes(override.action)) {
      const nextName = override.payload.displayName || override.payload.name || override.payload.instanceName;
      if (nextName) cloned.name = nextName;
      if (override.payload.description !== undefined) cloned.payload.description = override.payload.description;
    }
    return cloned;
  });
}

async function finalizeSubmittedAction(accountId, resourceType, action, resourceId, options = {}) {
  const changedTypes = options.types || resourceTypesAfterAction(resourceType, action);
  clearResourceCaches(changedTypes);
  if (viewMatchesResourceTypes(changedTypes)) await render();
  schedulePostActionSync(changedTypes, accountId, options.delays);
  if (options.toastMessage !== false) {
    toast(options.toastMessage || `${actionDisplayLabel(resourceType, action)}已提交，列表已标记为处理中`);
  }
}

async function refreshCurrentView(silent = true) {
  if (state.initializing) return;
  if (Date.now() < state.viewSwitchUntil) return;
  if (state.manualSyncing || $("#fieldsDialog")?.open) return;
  if (state.refreshing) {
    state.refreshQueued = true;
    return;
  }
  if (state.view === "dashboard" || state.view === "image") {
    await render();
    return;
  }
  const typeMap = {
    dashboard: ["ecs", "eip", "vpc", "subnet", "vip", "image"],
    ecs: ["ecs"],
    eip: ["eip"],
    vpc: ["vpc", "subnet", "vip", "security_group"],
    image: ["image"],
  };
  const types = typeMap[state.view];
  if (!types) return;
  await syncTypesForAccounts(types, selectedAccountId(), silent);
  await render();
}

function autoRefreshInterval() {
  if (state.view === "ecs") return 30000;
  if (state.view === "eip" || state.view === "vpc") return 60000;
  if (state.view === "image") return 120000;
  if (state.view === "dashboard") return 300000;
  return 0;
}

function configureAutoRefresh(runSoon = false) {
  clearInterval(state.refreshTimer);
  const interval = autoRefreshInterval();
  if (!interval) return;
  state.refreshTimer = setInterval(() => {
    if (state.manualSyncing || Date.now() < state.viewSwitchUntil || $("#fieldsDialog")?.open) return;
    refreshCurrentView(true).catch(() => {});
  }, interval);
  if (runSoon && !state.initializing && !document.hidden && !state.manualSyncing && Date.now() >= state.viewSwitchUntil) {
    window.setTimeout(() => refreshCurrentView(true).catch(() => {}), 1000);
  }
}

function accountRegionHint(accountId) {
  return accountById(accountId)?.region?.split(/[\s,，;；]+/).filter(Boolean)[0] || "";
}

const provinceRegionGroups = [
  { label: "北京", keywords: ["北京", "beijing"] },
  { label: "天津", keywords: ["天津", "tianjin"] },
  { label: "河北", keywords: ["河北", "石家庄", "唐山", "秦皇岛", "邯郸", "邢台", "保定", "张家口", "承德", "沧州", "廊坊", "衡水", "雄安", "hebei", "shijiazhuang", "tangshan", "qinhuangdao", "handan", "xingtai", "baoding", "zhangjiakou", "chengde", "cangzhou", "langfang", "hengshui", "xiongan"] },
  { label: "山西", keywords: ["山西", "太原", "大同", "阳泉", "长治", "晋城", "朔州", "晋中", "运城", "忻州", "临汾", "吕梁", "shanxi", "taiyuan", "datong", "yangquan", "changzhi", "jincheng", "shuozhou", "jinzhong", "yuncheng", "xinzhou", "linfen", "lvliang"] },
  { label: "内蒙古", keywords: ["内蒙古", "内蒙", "呼和浩特", "包头", "乌海", "赤峰", "通辽", "鄂尔多斯", "呼伦贝尔", "巴彦淖尔", "乌兰察布", "兴安", "锡林郭勒", "阿拉善", "neimeng", "neimenggu", "inner mongolia", "hohhot", "huhehaote", "baotou", "wuhai", "chifeng", "tongliao", "ordos", "hulunbuir", "bayannur", "wulanchabu", "xingan", "xilingol", "alxa"] },
  { label: "辽宁", keywords: ["辽宁", "沈阳", "大连", "鞍山", "抚顺", "本溪", "丹东", "锦州", "营口", "阜新", "辽阳", "盘锦", "铁岭", "朝阳", "葫芦岛", "liaoning", "shenyang", "dalian", "anshan", "fushun", "benxi", "dandong", "jinzhou", "yingkou", "fuxin", "liaoyang", "panjin", "tieling", "chaoyang", "huludao"] },
  { label: "吉林", keywords: ["吉林", "长春", "四平", "辽源", "通化", "白山", "松原", "白城", "延边", "jilin", "changchun", "siping", "liaoyuan", "tonghua", "baishan", "songyuan", "baicheng", "yanbian"] },
  { label: "黑龙江", keywords: ["黑龙江", "哈尔滨", "齐齐哈尔", "牡丹江", "佳木斯", "大庆", "鸡西", "双鸭山", "伊春", "七台河", "黑河", "绥化", "大兴安岭", "heilongjiang", "haerbin", "harbin", "qiqihar", "mudanjiang", "jiamusi", "daqing", "jixi", "shuangyashan", "yichun", "qitaihe", "heihe", "suihua", "daxinganling"] },
  { label: "上海", keywords: ["上海", "shanghai"] },
  { label: "江苏", keywords: ["江苏", "南京", "无锡", "徐州", "常州", "苏州", "南通", "连云港", "淮安", "盐城", "扬州", "镇江", "泰州", "宿迁", "jiangsu", "nanjing", "wuxi", "xuzhou", "changzhou", "suzhou", "nantong", "lianyungang", "huaian", "yancheng", "yangzhou", "zhenjiang", "taizhou", "suqian"] },
  { label: "浙江", keywords: ["浙江", "杭州", "宁波", "温州", "嘉兴", "湖州", "绍兴", "金华", "衢州", "舟山", "台州", "丽水", "zhejiang", "hangzhou", "ningbo", "wenzhou", "jiaxing", "huzhou", "shaoxing", "jinhua", "quzhou", "zhoushan", "taizhou2", "lishui"] },
  { label: "安徽", keywords: ["安徽", "合肥", "芜湖", "蚌埠", "淮南", "马鞍山", "淮北", "铜陵", "安庆", "黄山", "滁州", "阜阳", "宿州", "六安", "亳州", "池州", "宣城", "anhui", "hefei", "wuhu", "bengbu", "huainan", "maanshan", "huaibei", "tongling", "anqing", "huangshan", "chuzhou", "fuyang", "suzhou2", "liuan", "bozhou", "chizhou", "xuancheng"] },
  { label: "福建", keywords: ["福建", "福州", "厦门", "莆田", "三明", "泉州", "漳州", "南平", "龙岩", "宁德", "fujian", "fuzhou", "xiamen", "putian", "sanming", "quanzhou", "zhangzhou", "nanping", "longyan", "ningde"] },
  { label: "江西", keywords: ["江西", "南昌", "景德镇", "萍乡", "九江", "新余", "鹰潭", "赣州", "吉安", "宜春", "抚州", "上饶", "jiangxi", "nanchang", "jingdezhen", "pingxiang", "jiujiang", "xinyu", "yingtan", "ganzhou", "jian", "yichun2", "fuzhou2", "shangrao"] },
  { label: "山东", keywords: ["山东", "济南", "青岛", "淄博", "枣庄", "东营", "烟台", "潍坊", "济宁", "泰安", "威海", "日照", "临沂", "德州", "聊城", "滨州", "菏泽", "shandong", "jinan", "qingdao", "zibo", "zaozhuang", "dongying", "yantai", "weifang", "jining", "taian", "weihai", "rizhao", "linyi", "dezhou", "liaocheng", "binzhou", "heze"] },
  { label: "河南", keywords: ["河南", "郑州", "开封", "洛阳", "平顶山", "安阳", "鹤壁", "新乡", "焦作", "濮阳", "许昌", "漯河", "三门峡", "南阳", "商丘", "信阳", "周口", "驻马店", "济源", "henan", "zhengzhou", "kaifeng", "luoyang", "pingdingshan", "anyang", "hebi", "xinxiang", "jiaozuo", "puyang", "xuchang", "luohe", "sanmenxia", "nanyang", "shangqiu", "xinyang", "zhoukou", "zhumadian", "jiyuan"] },
  { label: "湖北", keywords: ["湖北", "武汉", "黄石", "十堰", "宜昌", "襄阳", "鄂州", "荆门", "孝感", "荆州", "黄冈", "咸宁", "随州", "恩施", "仙桃", "潜江", "天门", "神农架", "hubei", "wuhan", "huangshi", "shiyan", "yichang", "xiangyang", "ezhou", "jingmen", "xiaogan", "jingzhou", "huanggang", "xianning", "suizhou", "enshi", "xiantao", "qianjiang", "tianmen", "shennongjia"] },
  { label: "湖南", keywords: ["湖南", "长沙", "株洲", "湘潭", "衡阳", "邵阳", "岳阳", "常德", "张家界", "益阳", "郴州", "永州", "怀化", "娄底", "湘西", "hunan", "changsha", "zhuzhou", "xiangtan", "hengyang", "shaoyang", "yueyang", "changde", "zhangjiajie", "yiyang", "chenzhou", "yongzhou", "huaihua", "loudi", "xiangxi"] },
  { label: "广东", keywords: ["广东", "广州", "深圳", "珠海", "汕头", "佛山", "韶关", "湛江", "肇庆", "江门", "茂名", "惠州", "梅州", "汕尾", "河源", "阳江", "清远", "东莞", "中山", "潮州", "揭阳", "云浮", "guangdong", "guangzhou", "shenzhen", "zhuhai", "shantou", "foshan", "shaoguan", "zhanjiang", "zhaoqing", "jiangmen", "maoming", "huizhou", "meizhou", "shanwei", "heyuan", "yangjiang", "qingyuan", "dongguan", "zhongshan", "chaozhou", "jieyang", "yunfu"] },
  { label: "广西", keywords: ["广西", "南宁", "柳州", "桂林", "梧州", "北海", "防城港", "钦州", "贵港", "玉林", "百色", "贺州", "河池", "来宾", "崇左", "guangxi", "nanning", "liuzhou", "guilin", "wuzhou", "beihai", "fangchenggang", "qinzhou", "guigang", "yulin", "baise", "hezhou", "hechi", "laibin", "chongzuo"] },
  { label: "海南", keywords: ["海南", "海口", "三亚", "三沙", "儋州", "hainan", "haikou", "sanya", "sansha", "danzhou"] },
  { label: "重庆", keywords: ["重庆", "chongqing"] },
  { label: "四川", keywords: ["四川", "成都", "自贡", "攀枝花", "泸州", "德阳", "绵阳", "广元", "遂宁", "内江", "乐山", "南充", "眉山", "宜宾", "广安", "达州", "雅安", "巴中", "资阳", "阿坝", "甘孜", "凉山", "sichuan", "chengdu", "zigong", "panzhihua", "luzhou", "deyang", "mianyang", "guangyuan", "suining", "neijiang", "leshan", "nanchong", "meishan", "yibin", "guangan", "dazhou", "yaan", "bazhong", "ziyang", "aba", "ganzi", "liangshan"] },
  { label: "贵州", keywords: ["贵州", "贵阳", "六盘水", "遵义", "安顺", "毕节", "铜仁", "黔西南", "黔东南", "黔南", "guizhou", "guiyang", "liupanshui", "zunyi", "anshun", "bijie", "tongren", "qianxinan", "qiandongnan", "qiannan"] },
  { label: "云南", keywords: ["云南", "昆明", "曲靖", "玉溪", "保山", "昭通", "丽江", "普洱", "临沧", "楚雄", "红河", "文山", "西双版纳", "大理", "德宏", "怒江", "迪庆", "yunnan", "kunming", "qujing", "yuxi", "baoshan", "zhaotong", "lijiang", "puer", "lincang", "chuxiong", "honghe", "wenshan", "xishuangbanna", "dali", "dehong", "nujiang", "diqing"] },
  { label: "西藏", keywords: ["西藏", "拉萨", "日喀则", "昌都", "林芝", "山南", "那曲", "阿里", "xizang", "tibet", "lasa", "lhasa", "rikaze", "shigatse", "changdu", "linzhi", "shannan", "naqu", "ali"] },
  { label: "陕西", keywords: ["陕西", "西安", "铜川", "宝鸡", "咸阳", "渭南", "延安", "汉中", "榆林", "安康", "商洛", "shaanxi", "shanxi3", "xian", "xi-an", "tongchuan", "baoji", "xianyang", "weinan", "yanan", "hanzhong", "yulin2", "ankang", "shangluo"] },
  { label: "甘肃", keywords: ["甘肃", "兰州", "嘉峪关", "金昌", "白银", "天水", "武威", "张掖", "平凉", "酒泉", "庆阳", "定西", "陇南", "临夏", "甘南", "gansu", "lanzhou", "jiayuguan", "jinchang", "baiyin", "tianshui", "wuwei", "zhangye", "pingliang", "jiuquan", "qingyang", "dingxi", "longnan", "linxia", "gannan"] },
  { label: "青海", keywords: ["青海", "西宁", "海东", "海北", "黄南", "果洛", "玉树", "海西", "qinghai", "xining", "haidong", "haibei", "huangnan", "guoluo", "yushu", "haixi"] },
  { label: "宁夏", keywords: ["宁夏", "银川", "石嘴山", "吴忠", "固原", "中卫", "ningxia", "yinchuan", "shizuishan", "wuzhong", "guyuan", "zhongwei"] },
  { label: "新疆", keywords: ["新疆", "乌鲁木齐", "克拉玛依", "吐鲁番", "哈密", "昌吉", "博尔塔拉", "巴音郭楞", "阿克苏", "克孜勒苏", "喀什", "和田", "伊犁", "塔城", "阿勒泰", "石河子", "阿拉尔", "图木舒克", "五家渠", "北屯", "铁门关", "双河", "可克达拉", "昆玉", "胡杨河", "xinjiang", "wulumuqi", "urumqi", "kelamayi", "karamay", "tulufan", "turpan", "hami", "changji", "boertala", "bayinguoleng", "aksu", "kezilesu", "kashi", "hetian", "ili", "tacheng", "aletai", "shihezi", "alaer", "tumushuke", "wujiaqu", "beitun", "tiemenguan", "shuanghe", "kokdala", "kunyu", "huyanghe"] },
];

const overseasRegionKeywords = ["海外", "境外", "香港", "澳门", "台湾", "新加坡", "日本", "韩国", "美国", "德国", "欧洲", "法兰克福", "泰国", "印尼", "马来西亚", "迪拜", "hongkong", "hong-kong", "macau", "macao", "taiwan", "singapore", "japan", "korea", "usa", "america", "germany", "europe", "frankfurt", "thailand", "indonesia", "malaysia", "dubai", "oversea", "overseas", "global", "international"];
const regionGroupOrder = provinceRegionGroups.map((group) => group.label).concat("海外");

function regionSearchText(option) {
  const meta = option.meta || {};
  return [
    option.label,
    option.value,
    meta.regionName,
    meta.regionID,
    meta.regionDisplayName,
    meta.regionAreaName,
    meta.regionArea,
    meta.regionGroup,
    meta.name,
    meta.id,
    meta.area,
    meta.areaName,
    meta.province,
    meta.provinceName,
    meta.city,
    meta.cityName,
    meta.productName,
    meta.resourcePoolType,
    meta.cloudType,
    meta.regionType,
    meta.zone,
  ].filter(Boolean).join(" ").toLowerCase();
}

function officialRegionGroup(option) {
  const text = regionSearchText(option);
  if (overseasRegionKeywords.some((keyword) => text.includes(keyword.toLowerCase()))) return "海外";
  return provinceRegionGroups.find((group) => group.keywords.some((keyword) => text.includes(keyword.toLowerCase())))?.label || "海外";
}

function unsupportedComputeRegion(option) {
  return /云桌面|桌面|cloud\s*desktop|clouddesktop|desktop|workspace|wks/.test(regionSearchText(option));
}

function filterRegionOptions(options = [], product = "compute") {
  if (product === "compute") return options.filter((option) => !unsupportedComputeRegion(option));
  return options;
}

function groupRegionOptions(options = [], product = "compute", allowedRegionIds = null, countsByRegion = new Map()) {
  const groups = new Map();
  filterRegionOptions(options, product).forEach((option) => {
    if (allowedRegionIds && !allowedRegionIds.has(String(option.value))) return;
    const count = countsByRegion.get(String(option.value)) || 0;
    const item = count ? { ...option, label: `${option.label}（${count} VPC）`, meta: { ...(option.meta || {}), resourceCount: count, vpcCount: count } } : option;
    const label = officialRegionGroup(option);
    if (!groups.has(label)) groups.set(label, []);
    groups.get(label).push(item);
  });
  return [...groups.entries()]
    .sort(([a], [b]) => regionGroupOrder.indexOf(a) - regionGroupOrder.indexOf(b))
    .map(([label, items]) => ({
      label,
      options: items.sort((a, b) => {
        const countDiff = (countsByRegion.get(String(b.value)) || 0) - (countsByRegion.get(String(a.value)) || 0);
        return countDiff || String(a.label).localeCompare(String(b.label), "zh-CN");
      }),
    }));
}

function accountField(value = 0) {
  return {
    name: "accountID",
    label: "账号",
    type: "select",
    value: value ? String(value) : String(selectedAccountId() || ""),
    required: true,
    wide: true,
    options: state.accounts.map((account) => ({ value: account.id, label: account.name })),
  };
}

function formAccountId(values = {}, fallback = 0) {
  return Number(values.accountID || fallback || selectedAccountId() || state.accounts[0]?.id || 0);
}

function regionField(accountId, value = "", product = "compute") {
  return {
    name: "regionID",
    label: "资源池",
    type: "select",
    value: value || accountRegionHint(accountId),
    required: true,
    wide: true,
    collapsibleGroups: true,
    searchableGroups: true,
    autoSelectFirst: true,
    loadOptions: async () => groupRegionOptions(await optionApi(accountId, "regions"), product),
  };
}

function accountRegionField(accountId = 0, value = "", product = "compute") {
  return {
    name: "regionID",
    label: "资源池",
    type: "select",
    value: value || accountRegionHint(accountId),
    required: true,
    wide: true,
    collapsibleGroups: true,
    searchableGroups: true,
    autoSelectFirst: true,
    dependsOn: ["accountID"],
    loadOptions: async (values) => groupRegionOptions(await optionApi(formAccountId(values, accountId), "regions"), product),
  };
}

function queueEcsRegionPrewarm(accountId, regionIds = []) {
  if (!accountId || document.hidden) return;
  const unique = [...new Set(regionIds.map((value) => String(value || "").trim()).filter(Boolean))];
  const fresh = unique.filter((regionID) => !state.ecsPrewarmKeys.has(`${accountId}:${regionID}`));
  if (!fresh.length) return;
  fresh.forEach((regionID) => state.ecsPrewarmKeys.add(`${accountId}:${regionID}`));
  const queued = state.ecsPrewarmQueue.get(Number(accountId)) || new Set();
  fresh.forEach((regionID) => queued.add(regionID));
  state.ecsPrewarmQueue.set(Number(accountId), queued);
  window.clearTimeout(state.ecsPrewarmTimer);
  state.ecsPrewarmTimer = window.setTimeout(flushEcsRegionPrewarm, 2500);
}

function rememberEcsRegion(accountId, regionID) {
  if (!accountId || !regionID) return;
  state.lastEcsRegionByAccount.set(String(accountId), String(regionID));
  localStorage.setItem("ctyun:lastEcsRegions", JSON.stringify([...state.lastEcsRegionByAccount.entries()].slice(-50)));
}

function distributedAccountIds(preferredAccountId = 0) {
  const ids = state.accounts.map((account) => Number(account.id)).filter(Boolean);
  const preferred = Number(preferredAccountId || 0);
  return preferred ? [preferred, ...ids.filter((id) => id !== preferred)] : ids;
}

function distributeEcsRegionPrewarm(preferredAccountId, regionIds = [], limit = 4) {
  const accounts = distributedAccountIds(preferredAccountId);
  if (!accounts.length) return;
  const unique = [...new Set(regionIds.map((value) => String(value || "").trim()).filter(Boolean))].slice(0, limit);
  unique.forEach((regionID, index) => queueEcsRegionPrewarm(accounts[index % accounts.length], [regionID]));
}

function postponeEcsRegionPrewarm(delay = 3000) {
  if (!state.ecsPrewarmQueue.size) return;
  window.clearTimeout(state.ecsPrewarmTimer);
  state.ecsPrewarmTimer = window.setTimeout(flushEcsRegionPrewarm, delay);
}

function flushEcsRegionPrewarm() {
  if ($("#fieldsDialog")?.open && ["regionID", "azName", "flavorID", "imageID", "bootDiskType"].some(fieldLoading)) {
    postponeEcsRegionPrewarm(2500);
    return;
  }
  const batches = [...state.ecsPrewarmQueue.entries()].map(([accountId, regions]) => [
    accountId,
    [...regions].slice(0, 4),
  ]);
  state.ecsPrewarmQueue.clear();
  batches.forEach(([accountId, regionIds]) => {
    api(`/api/accounts/${accountId}/options/prewarm`, {
      method: "POST",
      body: JSON.stringify({
        region_ids: regionIds,
        available_only: true,
        include_images: true,
        limit: 4,
      }),
    }).then(() => {
      regionIds.forEach((regionID) => {
        window.setTimeout(() => state.ecsPrewarmKeys.delete(`${accountId}:${regionID}`), 10 * 60 * 1000);
      });
    }).catch(() => {
      regionIds.forEach((regionID) => state.ecsPrewarmKeys.delete(`${accountId}:${regionID}`));
    });
  });
}

function scheduleEcsRegionPrewarm(accountId, groupedOptions = []) {
  const flat = flattenOptions(groupedOptions).filter((option) => option?.value);
  if (!flat.length) return;
  const hintedRegion = String(accountRegionHint(accountId) || "");
  const recentRegion = String(state.lastEcsRegionByAccount.get(String(accountId)) || "");
  const candidates = [...flat].sort((a, b) => {
    const hintedCompare = Number(String(b.value) === hintedRegion) - Number(String(a.value) === hintedRegion);
    if (hintedCompare) return hintedCompare;
    const recentCompare = Number(String(b.value) === recentRegion) - Number(String(a.value) === recentRegion);
    if (recentCompare) return recentCompare;
    const resourceCompare = regionResourceScore(b) - regionResourceScore(a);
    if (resourceCompare) return resourceCompare;
    return String(a.label || "").localeCompare(String(b.label || ""), "zh-CN");
  }).map((option) => String(option.value));
  distributeEcsRegionPrewarm(accountId, candidates, 4);
}

async function ecsRegionOptions(accountId, product = "compute") {
  const regions = await optionApi(accountId, "regions");
  const vpcs = await cachedApi(`/api/resources/vpc?account_id=${accountId}`, 90000).catch(() => []);
  const countsByRegion = new Map();
  vpcs.forEach((row) => {
    const region = String(row.region || row.payload?.regionID || row.payload?.region_id || "");
    if (!region) return;
    countsByRegion.set(region, (countsByRegion.get(region) || 0) + 1);
  });
  const grouped = groupRegionOptions(regions, product, null, countsByRegion);
  scheduleEcsRegionPrewarm(accountId, grouped);
  return grouped;
}

function ecsRegionField(accountId = 0, value = "") {
  return {
    ...accountRegionField(accountId, value, "compute"),
    emptyLabel: "请选择资源池",
    autoSelectFirst: false,
    loadOptions: async (values) => ecsRegionOptions(formAccountId(values, accountId), "compute"),
  };
}

function isFlavorSoldOutMessage(message = "") {
  const text = String(message || "");
  if (/SaleCheck\.UnknownError|SaleYacos\.AccessFailed|SaleFormats\.FormatError|售罄信息检查失败|查询售罄信息错误|查询售罄信息格式错误/.test(text)) return false;
  if (/EbsSoldOut|disk.*sold out|磁盘.*售罄|云硬盘.*售罄|系统盘.*售罄/i.test(text)) return false;
  return /FlavorSoldOut|flavor sold out|该规格.*云主机.*已售罄|该规格.*已售罄|云主机规格.*售罄/i.test(text);
}

function isDiskSoldOutMessage(message = "") {
  const text = String(message || "");
  return /EbsSoldOut|disk.*sold out|磁盘.*售罄|云硬盘.*售罄|系统盘.*售罄/i.test(text);
}

function isOnDemandForbiddenMessage(message = "") {
  return /(?:Unknown|Ecs)\.OrderCheck\.UserForbiddenOnDemand|user not allowed place ondemand order|用户不允许订购按需类订单|用户不允许创建按需订购资源|暂不支持按量计费|账户余额.*100/i.test(String(message || ""));
}

function isImageUnavailableMessage(message = "") {
  return /Image(?:\.ImageCheck)?\.NotFound|Nonexistent\/Inapplicable image|镜像不存在|镜像.*不适用|所提供信息.*镜像|image.*not.*exist|inapplicable image/i.test(String(message || ""));
}

async function updateEcsPricePreview(values = {}) {
  const node = $("#ecsPricePreview");
  const form = $("#fieldsForm");
  if (!node || !form) return;
  const selectedText = (name) => {
    const input = form.elements[name];
    return input?.selectedOptions?.[0]?.textContent || input?.value || "-";
  };
  const billing = values.onDemand === "false"
    ? `${selectedText("cycleType")} ${values.cycleCount || 1} 期`
    : "按量计费";
  const accountId = formAccountId(values);
  const summary = `${billing} / ${selectedText("flavorID")} / 系统盘 ${values.bootDiskSize || 40}GB / ${selectedText("bootDiskType")}`;
  const missing = [
    [accountId, "账号"],
    [values.regionID, "资源池"],
    [values.flavorID, "规格"],
    [values.imageID, "镜像"],
    [values.vpcID, "VPC"],
    [values.subnetID, "子网"],
  ].filter(([value]) => !value).map(([, label]) => label);
  if (missing.length) {
    node.textContent = `价格：请先选择${missing.join("、")}，价格以官方订单确认为准。`;
    return;
  }
  const seq = ++state.ecsPriceSeq;
  node.textContent = `价格：正在询价（${summary}）...`;
  const image = state.pendingFieldOptions.get("imageID")?.find((option) => String(option.value) === String(values.imageID))?.meta || {};
  const flavor = state.pendingFieldOptions.get("flavorID")?.find((option) => String(option.value) === String(values.flavorID))?.meta || {};
  const officialPriceFallback = () => officialFlavorPriceText(flavor, summary);
  try {
    const payload = {
      ...values,
      flavorName: flavor.specName || flavor.flavorName,
      imageType: image.imageVisibilityCode ?? image.imageVisibility ?? image.visibility ?? 1,
    };
    const result = await api(`/api/accounts/${accountId}/prices/ecs`, {
      method: "POST",
      body: JSON.stringify({ payload }),
    });
    if (seq !== state.ecsPriceSeq) return;
    const amount = priceAmount(result);
    node.textContent = amount === null || amount === undefined
      ? (officialPriceFallback() || `价格：${summary}。价格以官方订单确认为准。`)
      : `价格：¥ ${Number(amount).toFixed(2)}（${summary}）。价格以官方订单确认为准。`;
  } catch (error) {
    if (seq !== state.ecsPriceSeq) return;
    const message = error.message || "";
    const unknownSaleCheck = /SaleCheck\.UnknownError|SaleYacos\.AccessFailed|SaleFormats\.FormatError|售罄信息检查失败|查询售罄信息错误|查询售罄信息格式错误/.test(message);
    const soldOut = !unknownSaleCheck && isFlavorSoldOutMessage(message);
    const diskSoldOut = !unknownSaleCheck && isDiskSoldOutMessage(message);
    const onDemandForbidden = isOnDemandForbiddenMessage(message) || /用户详情信息不符预期/.test(message);
    if (soldOut) {
      await markSelectedFlavorSoldOut(values);
      toast("该规格已确认不可购买，已从当前列表移除");
    }
    node.textContent = soldOut
      ? `价格：当前规格在所选资源池暂不可购买或已售罄（${summary}）。`
      : diskSoldOut
        ? `价格：当前系统盘类型在所选资源池暂不可购买或已售罄（${summary}）。`
        : onDemandForbidden
          ? (values.onDemand !== "true" && officialPriceFallback()
            ? officialPriceFallback()
            : `价格：当前账号暂不支持按量计费或余额未满足按量开通条件，请切换为包年包月后再询价。${summary}，以官方订单确认为准。`)
          : (officialPriceFallback() || `价格：暂时无法询价${message ? `：${message}` : ""}。${summary}，以官方订单确认为准。`);
  }
}

function explicitFalse(value) {
  return value === false || value === 0 || ["0", "false", "no", "n", "off", "否"].includes(String(value).trim().toLowerCase());
}

function explicitTrue(value) {
  return value === true || value === 1 || ["1", "true", "yes", "y", "on", "是", "售罄", "已售罄"].includes(String(value).trim().toLowerCase());
}

function compactStatusKey(value) {
  return String(value || "").toLowerCase().replace(/[^a-z0-9]/g, "");
}

function optionSearchText(value) {
  const chunks = [];
  const stack = [value];
  while (stack.length && chunks.length < 200) {
    const item = stack.pop();
    if (Array.isArray(item)) {
      stack.push(...item);
    } else if (item && typeof item === "object") {
      stack.push(...Object.values(item));
    } else if (item !== undefined && item !== null && item !== "") {
      chunks.push(String(item));
    }
  }
  return chunks.join(" ").toLowerCase();
}

function flavorSoldOut(option) {
  const meta = option.meta || {};
  const remaining = String(meta.remainingStatus ?? meta.remainStatus ?? meta.stockRemainingStatus ?? "").trim().toLowerCase();
  if (["y", "yes", "true", "1"].includes(remaining)) return true;
  if (["n", "no", "false", "0"].includes(remaining)) return false;
  const text = `${option.label || ""} ${optionSearchText(meta)}`;
  if (/售罄|已售完|售完|售空|无货|无库存|库存不足|暂无库存|资源不足|不可售|不可购买|不可用|已下架|停售|停止售卖|暂不支持|sold\s*out|soldout|out[\s_-]*of[\s_-]*stock|no[\s_-]*stock|unavailable|disabled/.test(text)) return true;
  const soldOutKeys = new Set([
    "soldout", "issoldout", "soldoutflag", "sellout", "issellout", "selloutflag",
    "stockout", "isstockout", "nostock", "outofstock", "isoutofstock",
    "unavailable", "isunavailable", "disabled", "isdisabled",
  ]);
  const availableKeys = new Set([
    "available", "isavailable", "canorder", "canbuy", "cancreate", "canapply",
    "canpurchase", "canuse", "saleable", "sellable", "orderable", "support",
    "issupport", "supportorder", "supportcreate", "supportsale", "isonsale",
    "onsale", "insale", "saleenabled", "sellenabled", "enabled", "enable",
  ]);
  const stockKeys = new Set([
    "remain", "remaincount", "remainnum", "stock", "stockcount", "stocknum",
    "inventory", "inventorycount", "inventorynum", "availablecount", "availablenum",
    "availablestock", "left", "leftcount", "surplus", "surpluscount",
  ]);
  const statusKeys = new Set([
    "status", "statusname", "displaystatus", "sellstatus", "salestatus",
    "sellstate", "salestate", "stockstatus", "inventorystatus", "availablestatus",
    "productstatus", "flavorstatus", "specstatus",
  ]);
  const unavailableStatusValues = new Set([
    "soldout", "soldoutstatus", "outofstock", "nostock", "unavailable", "disabled",
    "offline", "stopped", "stop", "stopsale", "offsale", "notsale", "unsaleable",
  ]);
  const stack = [meta];
  while (stack.length) {
    const item = stack.pop();
    if (Array.isArray(item)) {
      stack.push(...item.filter((entry) => entry && typeof entry === "object"));
      continue;
    }
    if (!item || typeof item !== "object") continue;
    for (const [key, value] of Object.entries(item)) {
      const normalized = compactStatusKey(key);
      if (value && typeof value === "object") {
        stack.push(value);
      } else if (soldOutKeys.has(normalized) && explicitTrue(value)) {
        return true;
      } else if (availableKeys.has(normalized) && explicitFalse(value)) {
        return true;
      } else if (stockKeys.has(normalized) && Number(value) === 0) {
        return true;
      } else if (statusKeys.has(normalized) && unavailableStatusValues.has(compactStatusKey(value))) {
        return true;
      }
    }
  }
  return false;
}

async function flavorOptions(accountId, values) {
  const hideSoldOut = values.hideSoldOut !== "false";
  const options = await optionApi(accountId, "flavors", {
    region_id: values.regionID,
    az_name: values.azName === "random" ? "" : values.azName,
    available_only: hideSoldOut ? "1" : "",
  });
  scheduleFlavorStockRefresh(accountId, values, options);
  const supported = options.filter(supportedCreateFlavor);
  const marked = supported.map((option) => {
    const soldOut = flavorSoldOut(option)
      || Boolean(option.meta?.soldOut)
      || state.soldOutFlavorKeys.has(flavorSaleKey(accountId, values.regionID, option.value));
    if (!soldOut) return option;
    const label = /售罄|不可售|无库存/.test(String(option.label)) ? option.label : `${option.label}（已售罄）`;
    return { ...option, label, disabled: true, meta: { ...(option.meta || {}), soldOut: true } };
  });
  return groupFlavorOptions(hideSoldOut ? marked.filter((option) => !option.disabled && !flavorSoldOut(option)) : marked);
}

async function diskTypeOptions(accountId, values) {
  const hideSoldOut = values.hideSoldOut !== "false";
  const options = await optionApi(accountId, "disk_types", {
    region_id: values.regionID,
    az_name: values.azName === "random" ? "" : values.azName,
    available_only: hideSoldOut ? "1" : "",
  });
  return hideSoldOut ? options.filter((option) => !option.disabled) : options;
}

function flavorSaleKey(accountId, regionID, flavorID) {
  return `${accountId || ""}:${regionID || ""}:${flavorID || ""}`;
}

function optionOfficialStockPending(option) {
  const status = String(option?.meta?.officialStockStatus || "").toLowerCase();
  return ["stock_pending", "stock_error", "stock_empty", "console_stock_error"].includes(status);
}

function scheduleFlavorStockRefresh(accountId, values = {}, options = []) {
  const regionID = values.regionID || "";
  if (!accountId || !regionID || !options.some(optionOfficialStockPending)) return;
  const key = `${accountId}:${regionID}:${values.azName || ""}`;
  if (state.flavorStockRefreshKeys.has(key)) return;
  state.flavorStockRefreshKeys.add(key);
  window.setTimeout(async () => {
    state.flavorStockRefreshKeys.delete(key);
    if (!$("#fieldsDialog")?.open) return;
    const current = fieldFormValues();
    if (String(formAccountId(current, accountId)) !== String(accountId) || String(current.regionID || "") !== String(regionID)) return;
    clearApiCache(`/api/accounts/${accountId}/options/flavors`);
    const field = state.pendingFieldDefinitions.find((item) => item.name === "flavorID");
    if (!field) return;
    await loadDynamicField(field);
  }, 3200);
}

async function markSelectedFlavorSoldOut(values = {}) {
  const accountId = formAccountId(values);
  const regionID = values.regionID || "";
  const flavorID = values.flavorID || "";
  if (!accountId || !regionID || !flavorID) return;
  state.soldOutFlavorKeys.add(flavorSaleKey(accountId, regionID, flavorID));
  clearApiCache(`/api/accounts/${accountId}/options/flavors`);
  const field = state.pendingFieldDefinitions.find((item) => item.name === "flavorID");
  const form = $("#fieldsForm");
  if (!field || !form?.elements?.flavorID) return;
  const current = state.pendingFieldOptions.get("flavorID") || [];
  const selected = current.find((option) => String(option.value) === String(flavorID));
  const selectedFamily = selected ? flavorFamily(selected) : "";
  const next = current.filter((option) => String(option.value) !== String(flavorID));
  setDynamicFieldOptions(field, groupFlavorOptions(next));
  if (selectedFamily) {
    const replacement = next.find((option) => !option.disabled && !flavorSoldOut(option) && flavorFamily(option) === selectedFamily)
      || next.find((option) => !option.disabled && !flavorSoldOut(option));
    if (replacement) {
      form.elements.flavorID.value = String(replacement.value);
      renderGroupedSelect(field, fieldGroupedOptions(field));
      $("#ecsPricePreview").textContent = `当前规格已售罄，已保留同资源池并切换到 ${replacement.label}。请确认后再提交。`;
    }
  }
  await refreshDependentFields("flavorID");
}

async function markSelectedImageUnavailable(values = {}) {
  const accountId = formAccountId(values);
  const regionID = values.regionID || "";
  const imageID = values.imageID || "";
  const flavorID = values.flavorID || "";
  if (!accountId || !regionID || !imageID) return;
  state.unavailableImageKeys.add(imageUnavailableKey(accountId, regionID, imageID, flavorID));
  clearApiCache(`/api/accounts/${accountId}/options/images`);
  const field = state.pendingFieldDefinitions.find((item) => item.name === "imageID");
  const form = $("#fieldsForm");
  if (!field || !form?.elements?.imageID) return;
  const current = state.pendingFieldOptions.get("imageID") || [];
  const next = current.filter((option) => String(option.value) !== String(imageID));
  setDynamicFieldOptions(field, groupImageOptions(next));
}

function imageArchitecture(option) {
  const meta = option.meta || {};
  const text = [
    meta.architecture,
    meta.cpuType,
    meta.imageName,
    meta.name,
    option.label,
  ].filter(Boolean).join(" ").toLowerCase();
  if (/aarch|arm|kunpeng|feiteng|鲲鹏|飞腾/.test(text)) return "ARM计算";
  return "X86计算";
}

function imageMatchesSelectedFlavor(option, values = {}) {
  const flavorID = values.flavorID || "";
  if (!flavorID) return true;
  const flavor = (state.pendingFieldOptions.get("flavorID") || [])
    .find((item) => String(item.value) === String(flavorID));
  if (!flavor) return true;
  return imageArchitecture(option) === flavorArchitecture(flavor);
}

function imageUnavailableKey(accountId, regionID, imageID, flavorID = "") {
  return `${accountId || ""}:${regionID || ""}:${flavorID || ""}:${imageID || ""}`;
}

function imageOptionGroup(option) {
  const meta = option.meta || {};
  const visibility = imageVisibility(option);
  const typeText = imageTypeText(option);
  if (imageTypes.private.values.includes(visibility) || /private|personal|私有/.test(typeText)) return "私有镜像";
  if (imageTypes.shared.values.includes(visibility) || /shared|share|共享/.test(typeText) || meta.source_user || meta.sourceUser || meta.sourceAccountID) return "共享镜像";
  const distro = publicImageDistro(option);
  if (distro) return distro;
  const osText = [
    option.label,
    meta.os,
    meta.osType,
    meta.osDistro,
    meta.osVersion,
    meta.platform,
    meta.imageName,
    meta.name,
  ].filter(Boolean).join(" ").toLowerCase();
  if (/windows|win|server 20|server 19|server 16|server 12/.test(osText)) return "Windows";
  return "Linux";
}

function groupImageOptions(options = []) {
  const groups = new Map([
    ["CTyunOS", []],
    ["Ubuntu", []],
    ["Debian", []],
    ["CentOS", []],
    ["私有镜像", []],
    ["共享镜像", []],
  ]);
  options.forEach((option) => {
    const group = imageOptionGroup(option);
    if (groups.has(group)) groups.get(group).push(option);
  });
  return [...groups.entries()]
    .filter(([, items]) => items.length)
    .map(([label, options]) => ({ label, options }));
}

async function imageOptions(accountId, values) {
  const raw = await optionApi(accountId, "images", { region_id: values.regionID });
  const filtered = filterImageOptions(flattenOptions(raw), {}, values)
    .filter((option) => !state.unavailableImageKeys.has(imageUnavailableKey(accountId, values.regionID, option.value, values.flavorID || "")));
  return groupImageOptions(filtered);
}

function priceAmount(result) {
  const keys = ["finalPrice", "discountPrice", "totalPrice", "orderPrice", "payPrice", "actualPrice", "price", "amount", "totalAmount"];
  const seen = new Set();
  const visit = (value) => {
    if (!value || typeof value !== "object" || seen.has(value)) return null;
    seen.add(value);
    for (const key of keys) {
      if (value[key] !== undefined && value[key] !== null && value[key] !== "" && !Number.isNaN(Number(value[key]))) return Number(value[key]);
    }
    if (Array.isArray(value)) {
      for (const item of value) {
        const found = visit(item);
        if (found !== null) return found;
      }
      return null;
    }
    for (const key of ["returnObj", "data", "result", "results", "items", "priceInfo", "orderInfo"]) {
      const found = visit(value[key]);
      if (found !== null) return found;
    }
    return null;
  };
  return visit(result);
}

function officialFlavorMonthlyPrice(flavor = {}) {
  const stock = flavor.officialStock || flavor.stock || flavor;
  const keys = [
    "referencePrice", "reference_price", "referPrice", "refer_price",
    "monthPrice", "monthlyPrice", "monthly_price", "priceMonth",
    "price_month", "flavorPrice", "flavor_price", "price",
  ];
  const seen = new Set();
  const visit = (value) => {
    if (!value || typeof value !== "object" || seen.has(value)) return null;
    seen.add(value);
    for (const key of keys) {
      const raw = value[key];
      if (raw === undefined || raw === null || raw === "" || raw === "--") continue;
      const number = Number(String(raw).replace(/[^\d.]/g, ""));
      if (Number.isFinite(number) && number > 0) return number;
    }
    for (const item of Object.values(value)) {
      const found = visit(item);
      if (found !== null) return found;
    }
    return null;
  };
  return visit(stock);
}

function officialFlavorPriceText(flavor, summary) {
  const monthly = officialFlavorMonthlyPrice(flavor);
  if (monthly === null || monthly === undefined) return "";
  return `价格：规格参考价 ￥${Number(monthly).toFixed(2)}/月（${summary}）。系统盘、安全防护和最终折扣以官方订单确认为准。`;
}

async function updateEipPricePreview(values = {}) {
  const node = $("#eipPricePreview");
  const form = $("#fieldsForm");
  if (!node || !form) return;
  const selectedText = (name) => form.elements[name]?.selectedOptions?.[0]?.textContent || form.elements[name]?.value || "-";
  const billing = values.cycleType === "on_demand"
    ? `按需 / ${selectedText("demandBillingType")}`
    : `${selectedText("cycleType")} ${values.cycleCount || 1} 期`;
  const quantityText = Number(values.quantity || 1) > 1 ? ` / ${values.quantity} 个` : "";
  const accountId = formAccountId(values);
  if (!accountId || !values.regionID || !values.bandwidth) {
    node.textContent = "价格：选择账号、资源池和带宽后显示，价格以官方订单确认为准。";
    return;
  }
  const seq = ++state.eipPriceSeq;
  node.textContent = `价格：正在询价（${billing} / ${values.bandwidth}Mbps / ${selectedText("lineType")}${quantityText}）...`;
  try {
    const result = await api(`/api/accounts/${accountId}/prices/eip`, {
      method: "POST",
      body: JSON.stringify({ payload: values }),
    });
    if (seq !== state.eipPriceSeq) return;
    const amount = priceAmount(result);
    node.textContent = amount === null || amount === undefined
      ? `价格：${billing} / ${values.bandwidth}Mbps / ${selectedText("lineType")}${quantityText}。价格以官方订单确认为准。`
      : `价格：单个约 ¥ ${Number(amount).toFixed(2)}（${billing} / ${values.bandwidth}Mbps / ${selectedText("lineType")}${quantityText}）。价格以官方订单确认为准。`;
  } catch (error) {
    if (seq !== state.eipPriceSeq) return;
    node.textContent = `价格：暂时无法询价，${billing} / ${values.bandwidth || "-"}Mbps / ${selectedText("lineType")}${quantityText}，以官方订单确认为准。`;
  }
}

function fieldLoading(name) {
  return document.querySelector(`[data-field-wrap="${name}"]`)?.classList.contains("field-loading");
}

async function jumpToNetworkCreate(kind, accountId, regionID, vpcID = "") {
  if (!accountId || !regionID) return toast("请先选择账号和资源池");
  const dialog = $("#fieldsDialog");
  if (dialog?.open) dialog.close();
  const accountFilter = $("#accountFilter");
  if (accountFilter) accountFilter.value = String(accountId);
  state.view = "vpc";
  await renderWithOptionalLoading("正在打开 VPC 网络...");
  if (kind === "vpc") {
    openVpcCreateDialog(accountId, regionID);
    return;
  }
  if (kind === "subnet") {
    if (!vpcID) return toast("请先创建 VPC");
    openSubnetCreateDialog(accountId, regionID, vpcID);
    return;
  }
  if (kind === "security_group") {
    if (!vpcID) return toast("请先创建 VPC");
    openNetworkCreateDialog("security_group", accountId, regionID, vpcID);
  }
}

function updateEcsNetworkShortcuts(values = {}, fallbackAccountId = 0) {
  const node = $("#ecsNetworkShortcut");
  if (!node) return;
  const accountId = formAccountId(values, fallbackAccountId);
  const regionID = values.regionID || "";
  const vpcID = values.vpcID || "";
  const hints = [];
  const vpcOptions = state.pendingFieldOptions.get("vpcID") || [];
  const subnetOptions = state.pendingFieldOptions.get("subnetID") || [];
  const securityGroupOptions = state.pendingFieldOptions.get("secGroupList") || [];
  if (accountId && regionID && !fieldLoading("vpcID") && !vpcOptions.length) {
    hints.push({ kind: "vpc", text: "当前账号和资源池没有 VPC，需先创建 VPC。", label: "去创建 VPC" });
  } else if (accountId && regionID && vpcID && !fieldLoading("subnetID") && !subnetOptions.length) {
    hints.push({ kind: "subnet", text: "当前 VPC 没有子网，需先创建子网。", label: "去创建子网" });
  }
  if (accountId && regionID && vpcID && !fieldLoading("secGroupList") && !securityGroupOptions.length) {
    hints.push({ kind: "security_group", text: "当前 VPC 没有安全组，需先创建安全组。", label: "去创建安全组" });
  }
  if (!hints.length) {
    node.classList.add("hidden");
    node.innerHTML = "";
    return;
  }
  node.classList.remove("hidden");
  node.innerHTML = hints.map((hint) => `
    <div class="inline-notice-row">
      <span>${escapeHtml(hint.text)}</span>
      <button type="button" data-ecs-network-shortcut="${hint.kind}">${escapeHtml(hint.label)}</button>
    </div>
  `).join("");
  node.querySelectorAll("[data-ecs-network-shortcut]").forEach((button) => {
    button.onclick = () => jumpToNetworkCreate(button.dataset.ecsNetworkShortcut, accountId, regionID, vpcID);
  });
}

function openEcsCreateDialog(accountId, defaults = {}) {
  openFieldsDialog("申请云主机", "参数来自天翼云官方 POST /v4/ecs/create-instance。规格、镜像、VPC 和子网必须属于同一资源池。", [
    accountField(accountId),
    ecsRegionField(accountId, defaults.regionID),
    {
      name: "azName", label: "可用区", type: "select", value: "random", required: true,
      dependsOn: ["regionID"],
      loadOptions: async (values) => [{ value: "random", label: "自动分配" }, ...await optionApi(formAccountId(values, accountId), "zones", { region_id: values.regionID })],
    },
    { name: "instanceName", label: "计算机名称", value: defaults.instanceName || defaultResourceName("ecs"), required: true },
    { name: "quantity", label: "创建数量", type: "number", value: defaults.quantity || "1", min: "1", max: "50", required: true },
    { name: "hideSoldOut", label: "仅显示未售罄", type: "checkbox", checked: true },
    {
      name: "flavorID", label: "云主机规格", type: "select", required: true, wide: true,
      collapsibleGroups: true,
      filterType: "flavor",
      dependsOn: ["regionID", "azName", "hideSoldOut"],
      loadOptions: (values) => flavorOptions(formAccountId(values, accountId), values),
    },
    {
      name: "imageID", label: "系统镜像", type: "select", value: defaults.imageID || "", required: true, wide: true,
      collapsibleGroups: true,
      filterType: "image",
      emptyLabel: "请先选择规格",
      dependsOn: ["regionID", "flavorID"],
      loadOptions: (values) => imageOptions(formAccountId(values, accountId), values),
    },
    {
      name: "vpcID", label: "VPC 网络", type: "select", required: true, wide: true,
      dependsOn: ["regionID"],
      preferDefaultOption: true,
      loadOptions: (values) => optionApi(formAccountId(values, accountId), "vpcs", { region_id: values.regionID }),
    },
    {
      name: "subnetID", label: "子网", type: "select", required: true, wide: true,
      dependsOn: ["vpcID"],
      preferDefaultOption: true,
      loadOptions: (values) => optionApi(formAccountId(values, accountId), "subnets", { region_id: values.regionID, vpc_id: values.vpcID }),
    },
    {
      name: "secGroupList", label: "安全组", type: "select", wide: true,
      dependsOn: ["vpcID"],
      preferDefaultOption: true,
      loadOptions: (values) => optionApi(formAccountId(values, accountId), "security_groups", { region_id: values.regionID, vpc_id: values.vpcID }),
    },
    { name: "networkShortcut", type: "note", wide: true, html: `<div id="ecsNetworkShortcut" class="inline-notice hidden"></div>` },
    {
      name: "bootDiskType",
      label: "系统盘类型",
      type: "select",
      required: true,
      value: "SSD",
      dependsOn: ["regionID", "azName", "hideSoldOut"],
      loadOptions: (values) => diskTypeOptions(formAccountId(values, accountId), values),
    },
    { name: "bootDiskSize", label: "系统盘(GB)", type: "number", value: "40", min: "40", required: true },
    { name: "onDemand", label: "付费方式", type: "select", value: "false", required: true, options: [{ value: "false", label: "包年包月" }, { value: "true", label: "按量计费" }] },
    {
      name: "cycleType", label: "包周期类型", type: "select",
      options: [{ value: "MONTH", label: "包月" }, { value: "YEAR", label: "包年" }],
      requiredWhen: (values) => values.onDemand === "false",
      visibleWhen: (values) => values.onDemand === "false",
    },
    { name: "cycleCount", label: "购买周期", type: "number", value: "1", min: "1", visibleWhen: (values) => values.onDemand === "false" },
    { name: "extIP", label: "公网IP", type: "select", required: true, options: [{ value: "0", label: "不使用" }, { value: "1", label: "自动分配" }, { value: "2", label: "使用已有EIP" }] },
    { name: "bandwidth", label: "公网带宽(Mbps)", type: "number", value: "5", min: "1", visibleWhen: (values) => values.extIP === "1" },
    {
      name: "eipID", label: "已有弹性IP", type: "select", wide: true,
      dependsOn: ["regionID"],
      refreshOn: ["extIP"],
      requiredWhen: (values) => values.extIP === "2",
      visibleWhen: (values) => values.extIP === "2",
      loadOptions: (values) => optionApi(formAccountId(values, accountId), "eips", { region_id: values.regionID }),
    },
    { name: "loginMode", label: "登录凭证", type: "select", required: true, options: [{ value: "password", label: "密码" }, { value: "keypair", label: "密钥对" }] },
    { name: "userPassword", label: "系统密码", type: "password", required: true, wide: true, visibleWhen: (values) => values.loginMode === "password" },
    {
      name: "keyPairID", label: "密钥对", type: "select", wide: true,
      dependsOn: ["regionID"],
      refreshOn: ["loginMode"],
      requiredWhen: (values) => values.loginMode === "keypair",
      visibleWhen: (values) => values.loginMode === "keypair",
      loadOptions: (values) => optionApi(formAccountId(values, accountId), "keypairs", { region_id: values.regionID }),
    },
    {
      name: "securityProductEnabled",
      label: "开启主机安全防护",
      type: "select",
      value: "false",
      required: true,
      options: [{ value: "true", label: "开启" }, { value: "false", label: "关闭" }],
    },
    {
      name: "securityProduct",
      label: "主机安全版本",
      type: "select",
      value: "BasicEdition",
      requiredWhen: (values) => values.securityProductEnabled !== "false",
      visibleWhen: (values) => values.securityProductEnabled !== "false",
      options: [
        { value: "BasicEdition", label: "基础版（免费）" },
        { value: "EnterpriseEdition", label: "企业版" },
        { value: "UltimateEdition", label: "旗舰版" },
      ],
    },
    { name: "ecsPricePreview", type: "note", wide: true, html: `<div id="ecsPricePreview" class="price-preview">价格：选择配置后显示，价格以官方订单确认为准。</div>` },
  ], async (data, context) => {
    const targetAccountId = formAccountId(data, accountId);
    const selectedImage = context.selectedOptions.imageID;
    const selectedFlavor = context.selectedOptions.flavorID;
    if (!selectedFlavor) throw new Error("请选择当前资源池可购买的云主机规格。");
    if (!selectedImage) throw new Error("请选择当前资源池和规格可用的系统镜像。资源池或规格变更后需要重新选择镜像。");
    if (!imageMatchesSelectedFlavor(selectedImage, data)) throw new Error("当前镜像与所选规格架构不匹配，请重新选择系统镜像。");
    const image = selectedImage.meta || {};
    data.imageType = defaults.imageType || (image.imageVisibilityCode ?? image.imageVisibility ?? image.visibility ?? 1);
    data.onDemand = data.onDemand === "true";
    if (!data.onDemand) {
      data.cycleType = data.cycleType || "MONTH";
      data.cycleCount = data.cycleCount || "1";
    }
    const quantity = Math.max(1, Math.min(50, Number(data.quantity || 1)));
    const baseName = data.instanceName || defaultResourceName("ecs");
    delete data.accountID;
    delete data.loginMode;
    delete data.hideSoldOut;
    delete data.quantity;
    if (data.securityProductEnabled === "false") {
      data.securityProduct = "false";
    } else {
      data.securityProduct = data.securityProduct || "BasicEdition";
    }
    delete data.securityProductEnabled;
    const items = Array.from({ length: quantity }, (_, index) => ({
      accountId: targetAccountId,
      resourceId: "",
      regionId: data.regionID || "",
      name: quantity > 1 ? `${baseName}-${String(index + 1).padStart(2, "0")}` : baseName,
    }));
    const results = await withPageLoading(`正在提交 ${quantity} 台云主机创建订单...`, () => runLimited(items, 3, (item) => submitAction(
      targetAccountId,
      "ecs",
      "create",
      null,
      { ...data, instanceName: item.name },
      { clearCache: false, toastMessage: false },
    )));
    const failed = results.filter((result) => !result.ok);
    toast(failed.length ? `云主机创建：成功 ${quantity - failed.length} 台，失败 ${failed.length} 台：${failed[0].error?.message || "未知错误"}` : `云主机创建订单已提交 ${quantity} 台`);
    state.view = "ecs";
    await finalizeSubmittedAction(targetAccountId, "ecs", "create", "", { types: ["ecs"], toastMessage: false });
  }, "提交", (values) => {
    updateEcsPricePreview(values);
    updateEcsNetworkShortcuts(values, accountId);
  });
}

function openEipCreateDialog(accountId, bulk = false) {
  openFieldsDialog(bulk ? "批量申请弹性IP" : "申请弹性IP", "参数来自天翼云官方 POST /v4/eip/create；批量申请会按数量并发提交多个官方创建请求。", [
    accountField(accountId),
    accountRegionField(accountId, "", "compute"),
    { name: "name", label: "EIP 名称", value: defaultResourceName("eip"), required: true },
    { name: "quantity", label: "申请数量", type: "number", value: bulk ? "2" : "1", min: "1", max: "50", required: true },
    { name: "bandwidth", label: "带宽(Mbps)", type: "number", value: "5", min: "1", required: true },
    {
      name: "lineType", label: "线路", type: "select", required: true,
      dependsOn: ["accountID", "regionID"],
      loadOptions: (values) => optionApi(formAccountId(values, accountId), "eip_lines", { region_id: values.regionID }),
    },
    {
      name: "cycleType", label: "计费周期", type: "select", required: true,
      dependsOn: ["accountID", "regionID"],
      loadOptions: (values) => optionApi(formAccountId(values, accountId), "eip_cycle_types", { region_id: values.regionID }),
    },
    { name: "cycleCount", label: "周期数量", type: "number", value: "1", min: "1", requiredWhen: (values) => values.cycleType !== "on_demand", visibleWhen: (values) => values.cycleType !== "on_demand" },
    {
      name: "demandBillingType", label: "按需计费", type: "select", required: true,
      dependsOn: ["accountID", "regionID", "cycleType"],
      loadOptions: (values) => optionApi(formAccountId(values, accountId), "eip_demand_billing_types", { region_id: values.regionID }),
      visibleWhen: (values) => values.cycleType === "on_demand",
    },
    { name: "eipPricePreview", type: "note", wide: true, html: `<div id="eipPricePreview" class="price-preview">价格：选择账号、资源池和带宽后显示，价格以官方订单确认为准。</div>` },
  ], async (data) => {
    const targetAccountId = formAccountId(data, accountId);
    delete data.accountID;
    const quantity = Math.max(1, Math.min(50, Number(data.quantity || 1)));
    delete data.quantity;
    const baseName = data.name || defaultResourceName("eip");
    const items = Array.from({ length: quantity }, (_, index) => ({ accountId: targetAccountId, resourceId: "", regionId: data.regionID || "", name: quantity > 1 ? `${baseName}-${String(index + 1).padStart(2, "0")}` : baseName, index }));
    const results = await withPageLoading(`正在提交 ${quantity} 个弹性IP申请...`, () => runLimited(items, 3, (item) => submitAction(
      targetAccountId,
      "eip",
      "create",
      null,
      { ...data, name: item.name },
      { clearCache: false, toastMessage: false },
    )));
    const failed = results.filter((result) => !result.ok);
    clearResourceCaches(["eip"]);
    if (viewMatchesResourceTypes(["eip"])) await render();
    schedulePostActionSync(["eip"], targetAccountId);
    toast(failed.length ? `弹性IP申请：成功 ${quantity - failed.length} 个，失败 ${failed.length} 个：${failed[0].error?.message || "未知错误"}` : `弹性IP申请已提交 ${quantity} 个，后台刷新中`);
  }, "提交", updateEipPricePreview);
}

function openVpcCreateDialog(accountId = selectedAccountId(), regionID = "") {
  if (!state.accounts.length) return toast("请先添加账号");
  openFieldsDialog("创建 VPC", "VPC 网段建议使用 RFC1918 私网段。", [
    accountField(accountId),
    accountRegionField(accountId, regionID, "compute"),
    { name: "name", label: "VPC 名称", value: defaultResourceName("vpc"), required: true },
    { name: "CIDR", label: "VPC CIDR", value: "192.168.0.0/16", required: true },
    { name: "description", label: "描述", wide: true },
    { name: "enableIpv6", label: "IPv6", type: "select", options: [{ value: "false", label: "关闭" }, { value: "true", label: "开启" }] },
  ], async (data) => {
    const targetAccountId = formAccountId(data, accountId);
    delete data.accountID;
    await submitAction(targetAccountId, "vpc", "create", null, data);
    await finalizeSubmittedAction(targetAccountId, "vpc", "create", "", { types: ["vpc"], toastMessage: false });
  });
}

function openSubnetCreateDialog(accountId = selectedAccountId(), regionID = "", vpcID = "") {
  if (!state.accounts.length) return toast("请先添加账号");
  openFieldsDialog("创建子网", "子网 CIDR 必须位于所选 VPC 的网段内。", [
    accountField(accountId),
    accountRegionField(accountId, regionID, "compute"),
    { name: "name", label: "子网名称", value: defaultResourceName("subnet"), required: true },
    {
      name: "vpcID", label: "VPC", type: "select", value: vpcID, required: true, wide: true,
      dependsOn: ["accountID", "regionID"],
      preferDefaultOption: true,
      loadOptions: (values) => optionApi(formAccountId(values, accountId), "vpcs", { region_id: values.regionID }),
    },
    { name: "CIDR", label: "子网 CIDR", value: "192.168.1.0/24", required: true },
    { name: "subnetGatewayIP", label: "网关IP", placeholder: "可选" },
    { name: "dnsList", label: "DNS", placeholder: "多个用逗号分隔", wide: true },
    { name: "enableIpv6", label: "IPv6", type: "select", options: [{ value: "false", label: "关闭" }, { value: "true", label: "开启" }] },
  ], async (data) => {
    const targetAccountId = formAccountId(data, accountId);
    delete data.accountID;
    await submitAction(targetAccountId, "vpc", "create_subnet", null, data);
    await finalizeSubmittedAction(targetAccountId, "vpc", "create_subnet", "", { types: ["vpc", "subnet"], toastMessage: false });
  });
}

function openNetworkCreateDialog(resourceType, accountId = selectedAccountId(), regionID = "", vpcID = "") {
  if (!state.accounts.length) return toast("请先添加账号");
  const titles = { security_group: "创建安全组", route_table: "创建路由表", acl: "创建网络 ACL" };
  const fields = [
    accountField(accountId),
    accountRegionField(accountId, regionID, "compute"),
    {
      name: "vpcID", label: "VPC", type: "select", value: vpcID, required: true, wide: true,
      dependsOn: ["accountID", "regionID"],
      preferDefaultOption: true,
      loadOptions: (values) => optionApi(formAccountId(values, accountId), "vpcs", { region_id: values.regionID }),
    },
    { name: "name", label: "名称", value: defaultResourceName(resourceType === "security_group" ? "sg" : resourceType.replace("_", "-")), required: true },
    { name: "description", label: "描述", type: "textarea", wide: true },
  ];
  if (resourceType === "route_table") {
    fields.push({ name: "subnetLocalRouteEnabled", label: "子网本地路由", type: "select", options: [{ value: "0", label: "关闭" }, { value: "1", label: "开启" }] });
  }
  if (resourceType === "acl") {
    fields.push({ name: "applyToPublicLb", label: "管控公网负载均衡流量", type: "select", options: [{ value: "false", label: "关闭" }, { value: "true", label: "开启" }] });
  }
  openFieldsDialog(titles[resourceType], "", fields, async (data) => {
    const targetAccountId = formAccountId(data, accountId);
    delete data.accountID;
    await submitAction(targetAccountId, resourceType, "create", null, data);
    await finalizeSubmittedAction(targetAccountId, resourceType, "create", "", { types: [resourceType], toastMessage: false });
  });
}

function bindResourceActions() {
  document.querySelectorAll("[data-resource-action]").forEach((b) => b.onclick = async () => {
    const accountId = Number(b.dataset.accountId);
    const regionID = b.dataset.regionId || "";
    const resourceType = b.dataset.resourceType;
    const action = b.dataset.resourceAction;
    if (resourceType === "ecs" && action === "remote_login") {
      await openEcsRemoteAccess(accountId, b.dataset.resourceId, b.dataset.resourceName || "");
      return;
    }
    if (resourceType === "image" && action === "create_ecs") {
      openEcsCreateDialog(accountId, {
        imageID: b.dataset.resourceId,
        regionID,
        imageType: b.dataset.imageType || "1",
      });
      return;
    }
    if (resourceType === "image" && action === "copy") {
      openFieldsDialog("复制私有镜像", "目标镜像名称不能与已有私有镜像重复。", [
        { name: "imageName", label: "目标镜像名称", required: true, wide: true },
        { name: "description", label: "描述", wide: true },
      ], async (data) => {
        await submitAction(accountId, "image", "copy", b.dataset.resourceId, data);
        await finalizeSubmittedAction(accountId, "image", "copy", b.dataset.resourceId, { types: ["image"], toastMessage: false });
      });
      return;
    }
    if (resourceType === "ecs" && action === "update") {
      openFieldsDialog("编辑云主机", "可修改显示名称、主机名和描述。", [
        { name: "displayName", label: "显示名称", value: b.dataset.resourceName || "", required: true },
        { name: "instanceName", label: "主机名" },
        { name: "instanceDescription", label: "描述", type: "textarea", wide: true },
      ], async (data) => {
        await submitAction(accountId, "ecs", "update", b.dataset.resourceId, data);
        await finalizeSubmittedAction(accountId, "ecs", "update", b.dataset.resourceId, { types: ["ecs"], toastMessage: false });
      });
      return;
    }
    if (resourceType === "ecs" && action === "reset_password") {
      openFieldsDialog("重置云主机密码", "密码需符合天翼云官方复杂度规则。", [
        { name: "userName", label: "系统用户名", placeholder: "Linux 默认 root，Windows 默认 administrator", wide: true },
        { name: "newPassword", label: "新密码", type: "password", required: true, wide: true },
      ], async (data) => {
        await submitAction(accountId, "ecs", "reset_password", b.dataset.resourceId, data);
      });
      return;
    }
    if (resourceType === "ecs" && action === "resize") {
      openFieldsDialog("变更云主机规格", "部分规格变更要求云主机处于关机状态，提交后以官方订单结果为准。", [
        { name: "regionID", type: "hidden", value: regionID },
        {
          name: "flavorID", label: "目标规格", type: "select", required: true, wide: true,
          dependsOn: ["regionID"],
          loadOptions: (values) => optionApi(accountId, "flavors", { region_id: values.regionID }),
        },
      ], async (data) => {
        await submitAction(accountId, "ecs", "resize", b.dataset.resourceId, data);
        await finalizeSubmittedAction(accountId, "ecs", "resize", b.dataset.resourceId, { types: ["ecs"], toastMessage: false });
      });
      return;
    }
    if (resourceType === "ecs" && action === "change_private_ip") {
      openFieldsDialog("修改内网IP（试验）", "此功能依赖天翼云当前可用的网卡变更接口；若官方返回接口不可用，平台不会模拟成功。官方要求云主机处于关机状态，仅主网卡支持。", [
        { name: "regionID", type: "hidden", value: regionID },
        { name: "networkInterfaceID", label: "主网卡ID", value: b.dataset.nicId || "", required: true, wide: true, placeholder: "未自动识别时请从官方网卡详情复制" },
        {
          name: "subnetID", label: "目标子网", type: "select", value: b.dataset.subnetId || "", required: true, wide: true,
          dependsOn: ["regionID"],
          loadOptions: (values) => optionApi(accountId, "subnets", { region_id: values.regionID, vpc_id: b.dataset.vpcId || "" }),
        },
        { name: "privateIP", label: "目标内网IP", value: b.dataset.privateIp || "", placeholder: "留空则由官方自动分配", wide: true },
      ], async (data) => {
        await submitAction(accountId, "ecs", "change_private_ip", b.dataset.resourceId, data);
        await finalizeSubmittedAction(accountId, "ecs", "change_private_ip", b.dataset.resourceId, { types: ["ecs", "subnet", "vip"], toastMessage: false });
      }, "提交修改");
      return;
    }
    if (resourceType === "ecs" && action === "change_vpc") {
      openFieldsDialog("更换VPC网络（试验）", "此功能依赖天翼云当前可用的网卡变更接口；若官方返回接口不可用，平台不会模拟成功。官方要求云主机处于关机状态，仅主网卡支持。", [
        { name: "regionID", type: "hidden", value: regionID },
        { name: "networkInterfaceID", label: "主网卡ID", value: b.dataset.nicId || "", required: true, wide: true, placeholder: "未自动识别时请从官方网卡详情复制" },
        {
          name: "vpcID", label: "目标 VPC", type: "select", value: b.dataset.vpcId || "", required: true, wide: true,
          dependsOn: ["regionID"],
          loadOptions: (values) => optionApi(accountId, "vpcs", { region_id: values.regionID }),
        },
        {
          name: "subnetID", label: "目标子网", type: "select", value: b.dataset.subnetId || "", required: true, wide: true,
          dependsOn: ["vpcID"],
          loadOptions: (values) => optionApi(accountId, "subnets", { region_id: values.regionID, vpc_id: values.vpcID }),
        },
        {
          name: "securityGroupID", label: "目标安全组", type: "select", required: true, wide: true,
          dependsOn: ["vpcID"],
          loadOptions: (values) => optionApi(accountId, "security_groups", { region_id: values.regionID, vpc_id: values.vpcID }),
        },
        { name: "privateIP", label: "目标内网IP", placeholder: "留空则由官方自动分配", wide: true },
      ], async (data) => {
        await submitAction(accountId, "ecs", "change_vpc", b.dataset.resourceId, data);
        await finalizeSubmittedAction(accountId, "ecs", "change_vpc", b.dataset.resourceId, { types: ["ecs", "vpc", "subnet", "security_group", "vip"], toastMessage: false });
      }, "提交更换");
      return;
    }
    if (resourceType === "ecs" && action === "rebuild") {
      openFieldsDialog("重装云主机系统", "重装会替换系统盘。镜像和密钥对只列当前资源池可用项。", [
        { name: "regionID", type: "hidden", value: regionID },
        {
          name: "imageID", label: "目标镜像", type: "select", required: true, wide: true,
          collapsibleGroups: true,
          dependsOn: ["regionID"],
          loadOptions: (values) => imageOptions(accountId, values),
        },
        { name: "loginMode", label: "登录凭证", type: "select", required: true, options: [{ value: "password", label: "密码" }, { value: "keypair", label: "密钥对" }] },
        { name: "password", label: "系统密码", type: "password", required: true, wide: true, visibleWhen: (values) => values.loginMode === "password" },
        {
          name: "keyPairID", label: "密钥对", type: "select", wide: true,
          dependsOn: ["regionID"],
          requiredWhen: (values) => values.loginMode === "keypair",
          visibleWhen: (values) => values.loginMode === "keypair",
          loadOptions: (values) => optionApi(accountId, "keypairs", { region_id: values.regionID }),
        },
        { name: "userName", label: "系统用户名", placeholder: "可选" },
        { name: "monitorService", label: "详细监控", type: "select", options: [{ value: "true", label: "开启" }, { value: "false", label: "关闭" }] },
      ], async (data) => {
        delete data.loginMode;
        await submitAction(accountId, "ecs", "rebuild", b.dataset.resourceId, data);
        await finalizeSubmittedAction(accountId, "ecs", "rebuild", b.dataset.resourceId, { types: ["ecs"], toastMessage: false });
      });
      return;
    }
    if (resourceType === "ecs" && action === "deletion_protection") {
      openFieldsDialog("云主机删除保护", "开启后，需先关闭删除保护才能释放实例。", [
        { name: "deletionProtection", label: "删除保护", type: "select", required: true, options: [{ value: "true", label: "开启" }, { value: "false", label: "关闭" }] },
      ], async (data) => {
        await submitAction(accountId, "ecs", "deletion_protection", b.dataset.resourceId, data);
        await finalizeSubmittedAction(accountId, "ecs", "deletion_protection", b.dataset.resourceId, { types: ["ecs"], toastMessage: false });
      });
      return;
    }
    if (resourceType === "ecs" && action === "auto_renew") {
      openFieldsDialog("云主机自动续订", "仅包周期云主机支持自动续订。", [
        { name: "autoRenewStatus", label: "自动续订", type: "select", required: true, options: [{ value: "1", label: "开启" }, { value: "0", label: "关闭" }] },
        { name: "autoRenewCycleType", label: "续订周期", type: "select", options: [{ value: "MONTH", label: "按月" }, { value: "YEAR", label: "按年" }], visibleWhen: (values) => values.autoRenewStatus === "1" },
        { name: "autoRenewCycleCount", label: "周期数量", type: "number", value: "1", min: "1", visibleWhen: (values) => values.autoRenewStatus === "1" },
      ], async (data) => {
        await submitAction(accountId, "ecs", "auto_renew", b.dataset.resourceId, data);
      });
      return;
    }
    if (resourceType === "ecs" && action === "create_image") {
      openFieldsDialog("制作私有镜像", "云主机应满足官方镜像创建条件，通常建议先关机。", [
        { name: "imageName", label: "镜像名称", required: true, wide: true },
        { name: "description", label: "描述", wide: true },
      ], async (data) => {
        await submitAction(accountId, "ecs", "create_image", b.dataset.resourceId, data);
        await finalizeSubmittedAction(accountId, "ecs", "create_image", b.dataset.resourceId, { types: ["image"], toastMessage: false });
      });
      return;
    }
    if (resourceType === "eip" && action === "rename") {
      openFieldsDialog("修改弹性IP名称", "", [
        { name: "name", label: "新名称", value: b.dataset.resourceName || "", required: true, wide: true },
      ], async (data) => {
        await submitAction(accountId, "eip", "rename", b.dataset.resourceId, data);
        await finalizeSubmittedAction(accountId, "eip", "rename", b.dataset.resourceId, { types: ["eip"], toastMessage: false });
      });
      return;
    }
    if (resourceType === "eip" && action === "bind") {
      const targets = await eipAssociationOptions(accountId, regionID);
      if (!targets.length) return toast("当前资源池没有已同步的云主机或虚拟IP，请先同步资源");
      openFieldsDialog("绑定弹性IP", "只列出同一账号、同一资源池内可绑定的云主机和虚拟IP。", [
        { name: "associationID", label: "绑定目标", type: "select", required: true, wide: true, options: targets },
      ], async (data, context) => {
        data.associationType = context.selectedOptions.associationID?.meta?.associationType;
        await submitAction(accountId, "eip", "bind", b.dataset.resourceId, data);
        await finalizeSubmittedAction(accountId, "eip", "bind", b.dataset.resourceId, { types: ["eip", "ecs", "vip"], toastMessage: false });
        toast("弹性IP绑定已提交，后台刷新中");
      });
      return;
    }
    if (resourceType === "image" && ["share", "unshare"].includes(action)) {
      const row = resourceRowFromButton(b);
      const shareTargets = state.accounts
        .filter((account) => account.provider_account_id && account.id !== accountId)
        .map((account) => ({ value: account.provider_account_id, label: `${account.name} (${account.provider_account_id})` }));
      const unshareTargets = imageSharedTargetOptions(row);
      const targets = action === "share" ? shareTargets : unshareTargets;
      if (action === "unshare" && !targets.length) return toast("未识别到这张镜像当前共享给哪些账号，请先同步镜像后再试。");
      openFieldsDialog(action === "share" ? "共享私有镜像" : "取消镜像共享", action === "share" ? "接收方使用平台自动获取的天翼云账号ID。" : "仅列出该镜像当前已共享出去的目标账号。", [
        { name: "destinationAccountID", label: "接收方天翼云账号ID", type: "select", required: true, wide: true, options: targets.length ? targets : [{ value: "", label: "请先完成接收方账号的网页登录" }] },
      ], async (data) => {
        await submitAction(accountId, "image", action, b.dataset.resourceId, data);
        await finalizeSubmittedAction(accountId, "image", action, b.dataset.resourceId, { types: ["image"], toastMessage: false });
      });
      return;
    }
    if (resourceType === "image" && ["accept", "reject"].includes(action)) {
      confirmAction(`${b.textContent}共享镜像`, "接受共享时会使用当前接收方账号的 AK/SK 调用官方接口。", async () => {
        await submitAction(accountId, "image", action, b.dataset.resourceId);
        await finalizeSubmittedAction(accountId, "image", action, b.dataset.resourceId, { types: ["image"], toastMessage: false });
      });
      return;
    }
    const destructive = ["release", "unsubscribe", "delete"].includes(action);
    confirmAction(
      destructive ? "确认高风险操作" : "确认资源操作",
      `${b.textContent}资源 ${b.dataset.resourceName || ""}。${destructive ? "此操作可能不可逆。" : ""}`,
      async () => {
        await submitAction(accountId, resourceType, action, b.dataset.resourceId);
        const changedTypes = resourceTypesAfterAction(resourceType, action);
        await finalizeSubmittedAction(accountId, resourceType, action, b.dataset.resourceId, { types: changedTypes, toastMessage: false });
      }
    );
  });
  document.querySelectorAll("[data-create]").forEach((b) => b.onclick = () => {
    if (!state.accounts.length) return toast("请先添加账号");
    const id = selectedAccountId();
    if (b.dataset.create === "ecs") openEcsCreateDialog(id);
    if (b.dataset.create === "eip") openEipCreateDialog(id);
  });
}

function bindVpcActions({ subnets, ecs, eips, securityGroups }) {
  document.querySelectorAll("[data-edit-vpc]").forEach((button) => button.onclick = () => {
    openFieldsDialog("编辑 VPC", "", [
      { name: "name", label: "VPC 名称", value: button.dataset.name || "", required: true },
      { name: "description", label: "描述", type: "textarea", value: button.dataset.description || "", wide: true },
      { name: "dnsHostnamesEnabled", label: "DNS 主机名解析", type: "select", options: [{ value: "1", label: "开启" }, { value: "0", label: "关闭" }] },
    ], async (data) => {
      await submitAction(button.dataset.accountId, "vpc", "update", button.dataset.editVpc, data);
      await finalizeSubmittedAction(button.dataset.accountId, "vpc", "update", button.dataset.editVpc, { types: ["vpc"], toastMessage: false });
    });
  });
  document.querySelectorAll("[data-delete-vpc]").forEach((button) => button.onclick = () => {
    confirmAction("删除 VPC", "只能删除没有子网和关联资源的 VPC。", async () => {
      await submitAction(button.dataset.accountId, "vpc", "delete", button.dataset.deleteVpc, { regionID: button.dataset.regionId });
      await finalizeSubmittedAction(button.dataset.accountId, "vpc", "delete", button.dataset.deleteVpc, { types: ["vpc"], toastMessage: false });
    });
  });
  document.querySelectorAll("[data-edit-subnet]").forEach((button) => button.onclick = () => {
    openFieldsDialog("编辑子网", "", [
      { name: "name", label: "子网名称", value: button.dataset.name || "", required: true },
      { name: "description", label: "描述", type: "textarea", value: button.dataset.description || "", wide: true },
      { name: "dnsList", label: "DNS", value: button.dataset.dns || "", placeholder: "多个地址用逗号分隔", wide: true },
    ], async (data) => {
      await submitAction(button.dataset.accountId, "subnet", "update", button.dataset.editSubnet, data);
      await finalizeSubmittedAction(button.dataset.accountId, "subnet", "update", button.dataset.editSubnet, { types: ["subnet"], toastMessage: false });
    });
  });
  document.querySelectorAll("[data-delete-subnet]").forEach((button) => button.onclick = () => {
    confirmAction("删除子网", "只能删除没有云主机、虚拟IP等关联资源的子网。", async () => {
      await submitAction(button.dataset.accountId, "subnet", "delete", button.dataset.deleteSubnet, { regionID: button.dataset.regionId });
      await finalizeSubmittedAction(button.dataset.accountId, "subnet", "delete", button.dataset.deleteSubnet, { types: ["vpc", "subnet"], toastMessage: false });
    });
  });
  document.querySelectorAll("[data-create-vpc]").forEach((button) => button.onclick = () => {
    openVpcCreateDialog(selectedAccountId());
  });
  document.querySelectorAll("[data-create-subnet]").forEach((button) => button.onclick = () => {
    openSubnetCreateDialog(selectedAccountId());
  });
  document.querySelectorAll("[data-create-network]").forEach((button) => button.onclick = () => {
    openNetworkCreateDialog(button.dataset.createNetwork, selectedAccountId());
  });
  document.querySelectorAll("[data-network-action]").forEach((button) => button.onclick = () => {
    const resourceType = button.dataset.resourceType;
    const action = button.dataset.networkAction;
    if (action === "delete") {
      confirmAction("删除网络资源", "删除前请确保没有关联实例或子网。", async () => {
        await submitAction(button.dataset.accountId, resourceType, "delete", button.dataset.resourceId);
        await finalizeSubmittedAction(button.dataset.accountId, resourceType, "delete", button.dataset.resourceId, { types: [resourceType], toastMessage: false });
      });
      return;
    }
    const fields = [
      { name: "name", label: "名称", value: button.dataset.name || "", required: true },
      { name: "description", label: "描述", type: "textarea", value: button.dataset.description || "", wide: true },
    ];
    if (resourceType === "security_group") {
      fields.push({ name: "enabled", label: "状态", type: "select", options: [{ value: "true", label: "启用" }, { value: "false", label: "停用" }] });
    }
    if (resourceType === "route_table") {
      fields.push({ name: "subnetLocalRouteEnabled", label: "子网本地路由", type: "select", options: [{ value: "0", label: "关闭" }, { value: "1", label: "开启" }] });
    }
    if (resourceType === "acl") {
      fields.push({ name: "enabled", label: "状态", type: "select", options: [{ value: "enable", label: "启用" }, { value: "disable", label: "停用" }] });
    }
    openFieldsDialog("编辑网络资源", "", fields, async (data) => {
      await submitAction(button.dataset.accountId, resourceType, "update", button.dataset.resourceId, data);
      await finalizeSubmittedAction(button.dataset.accountId, resourceType, "update", button.dataset.resourceId, { types: [resourceType], toastMessage: false });
    });
  });
  document.querySelectorAll("[data-sg-rule-action]").forEach((button) => button.onclick = () => {
    const accountId = Number(button.dataset.accountId);
    const securityGroup = securityGroups.find((row) => row.account_id === accountId && row.provider_id === button.dataset.resourceId);
    if (button.dataset.sgRuleAction === "create") {
      openFieldsDialog("添加安全组规则", "规则会直接提交到天翼云安全组。", [
        { name: "direction", label: "方向", type: "select", required: true, options: [{ value: "ingress", label: "入方向" }, { value: "egress", label: "出方向" }] },
        { name: "ruleAction", label: "策略", type: "select", required: true, options: [{ value: "accept", label: "允许" }, { value: "drop", label: "拒绝" }] },
        { name: "protocol", label: "协议", type: "select", required: true, options: ["ANY", "TCP", "UDP", "ICMP", "ICMP6"].map((value) => ({ value, label: value })) },
        { name: "ethertype", label: "IP 类型", type: "select", required: true, options: [{ value: "IPv4", label: "IPv4" }, { value: "IPv6", label: "IPv6" }] },
        { name: "destCidrIp", label: "源/目标网段", value: "0.0.0.0/0", required: true },
        { name: "range", label: "端口范围", placeholder: "例如 22 或 1-65535；ANY 可留空" },
        { name: "priority", label: "优先级", type: "number", value: "100", min: "1", max: "100", required: true },
        { name: "description", label: "描述", wide: true },
      ], async (data) => {
        await submitAction(accountId, "security_group", "create_rule", button.dataset.resourceId, data);
        await finalizeSubmittedAction(accountId, "security_group", "create_rule", button.dataset.resourceId, { types: ["security_group"], toastMessage: false });
      });
      return;
    }
    const rules = securityGroup?.payload?.securityGroupRuleList || [];
    if (!rules.length) return toast("该安全组没有可删除的规则");
    const options = rules.map((rule) => ({
      value: rule.id,
      label: `${rule.direction === "egress" ? "出方向" : "入方向"} · ${rule.action || "accept"} · ${rule.protocol || "ANY"} · ${rule.range || "全部端口"} · ${rule.destCidrIp || "任意网段"}`,
      meta: rule,
    }));
    openFieldsDialog("删除安全组规则", "请选择要删除的规则。", [
      { name: "securityGroupRuleID", label: "安全组规则", type: "select", required: true, wide: true, options },
    ], async (data, context) => {
      data.direction = context.selectedOptions.securityGroupRuleID?.meta?.direction || "ingress";
      await submitAction(accountId, "security_group", "delete_rule", button.dataset.resourceId, data);
      await finalizeSubmittedAction(accountId, "security_group", "delete_rule", button.dataset.resourceId, { types: ["security_group"], toastMessage: false });
    });
  });
  document.querySelectorAll("[data-create-vip-subnet]").forEach((button) => button.onclick = () => {
    openFieldsDialog("配置虚拟IP", "不填 IP 地址时由天翼云自动分配。", [
      { name: "subnetID", type: "hidden", value: button.dataset.createVipSubnet },
      { name: "networkID", type: "hidden", value: button.dataset.vpcId || "" },
      { name: "regionID", type: "hidden", value: button.dataset.regionId },
      { name: "ipAddress", label: "指定虚拟IP地址", placeholder: "可选" },
    ], async (data) => {
      await submitAction(button.dataset.accountId, "vip", "create", null, data);
      await finalizeSubmittedAction(button.dataset.accountId, "vip", "create", "", { types: ["vip"], toastMessage: false });
    });
  });

  document.querySelectorAll("[data-vip-action]").forEach((button) => button.onclick = () => {
    const accountId = Number(button.dataset.accountId);
    const region = button.dataset.regionId;
    const action = button.dataset.vipAction;
    const sameRegionEcs = ecs.filter((row) => row.account_id === accountId && row.region === region);
    const sameRegionEips = eips.filter((row) => row.account_id === accountId && row.region === region);
    if (action === "delete") {
      confirmAction("删除虚拟IP", "删除虚拟IP可能影响已绑定云主机或弹性IP。", async () => {
        await submitAction(accountId, "vip", "delete", button.dataset.resourceId, { regionID: region });
        await finalizeSubmittedAction(accountId, "vip", "delete", button.dataset.resourceId, { types: ["vip"], toastMessage: false });
      });
      return;
    }
    if (action === "bind_ecs") {
      const bindableEcs = sameRegionEcs.filter((row) => row.payload?.network_card_id || row.payload?.networkCardList?.[0]?.networkCardID);
      if (!bindableEcs.length) return toast("当前资源池没有同步到带主网卡信息的云主机");
      openFieldsDialog("虚拟IP绑定云主机", "只列出已同步到主网卡信息的云主机。", [
        { name: "instanceID", label: "云主机", type: "select", required: true, wide: true, options: bindableEcs.map((row) => ({ value: row.provider_id, label: `${row.name} · ${row.payload.private_ip || "无私网IP"}` })) },
      ], async (data) => {
        const selected = sameRegionEcs.find((row) => row.provider_id === data.instanceID);
        data.regionID = region;
        data.networkInterfaceID = selected?.payload?.network_card_id || selected?.payload?.networkCardList?.[0]?.networkCardID || "";
        await submitAction(accountId, "vip", "bind_ecs", button.dataset.resourceId, data);
        await finalizeSubmittedAction(accountId, "vip", "bind_ecs", button.dataset.resourceId, { types: ["vip", "ecs"], toastMessage: false });
        toast("虚拟IP绑定云主机已提交，后台刷新中");
      });
      return;
    }
    if (action === "bind_eip") {
      openFieldsDialog("虚拟IP绑定弹性IP", "只会列出当前账号、当前资源池下已同步的弹性IP。", [
        { name: "floatingID", label: "弹性IP", type: "select", required: true, wide: true, options: sameRegionEips.map((row) => ({ value: row.provider_id, label: `${row.payload.ip || row.name} · ${row.name}` })) },
      ], async (data) => {
        data.regionID = region;
        await submitAction(accountId, "vip", "bind_eip", button.dataset.resourceId, data);
        await finalizeSubmittedAction(accountId, "vip", "bind_eip", button.dataset.resourceId, { types: ["vip", "eip"], toastMessage: false });
        toast("虚拟IP绑定弹性IP已提交，后台刷新中");
      });
    }
  });
}

function confirmAction(title, text, callback) {
  $("#actionTitle").textContent = title;
  $("#actionText").textContent = text;
  state.pendingAction = callback;
  $("#actionDialog").showModal();
}

async function syncAccount(id, options = {}) {
  const {
    showPageLoading = false,
    renderAfter = true,
    clearCache = true,
    quiet = false,
    throwError = false,
  } = options;
  if (!quiet) toast(`正在同步 ${accountName(id)}...`);
  if (showPageLoading) setPageLoading(true, `正在同步 ${accountName(id)}...`);
  state.refreshing = true;
  updateRefreshBadge("同步中...");
  try {
    const result = await api(`/api/accounts/${id}/sync`, { method: "POST" });
    const total = Object.values(result.counts).reduce((a, b) => a + b, 0);
    const failed = Object.keys(result.errors || {}).length;
    const skipped = Object.keys(result.skipped || {}).length;
    if (!quiet) toast(`同步完成：${total} 项${failed ? `，${failed} 个模块失败` : ""}${skipped ? `，${skipped} 个模块跳过` : ""}`);
    state.lastRefreshAt = Date.now();
    if (clearCache) clearResourceCaches();
    updateRefreshBadge();
    if (renderAfter) await render();
    return result;
  } catch (error) {
    if (String(error.message || "").includes("同步任务正在运行")) {
      schedulePostActionSync(resourceCacheTypes, id, [1200, 4000, 10000, 20000]);
      if (!quiet) toast("该账号已有后台同步任务，稍后会自动刷新");
      return { ok: true, counts: {}, skipped: { sync: "running" } };
    }
    if (!quiet) toast(error.message);
    if (throwError) throw error;
    return { ok: false, error: error.message };
  } finally {
    state.refreshing = false;
    updateRefreshBadge();
    if (showPageLoading) setPageLoading(false);
  }
}

function openManagedWindow(message = "正在打开官方页面...") {
  const win = window.open("about:blank", "_blank", "popup=yes,width=1280,height=860");
  if (!win) return null;
  try {
    win.document.title = "正在打开天翼云官方页面";
    win.document.body.innerHTML = `<p style="font:14px system-ui,'Microsoft YaHei',sans-serif;padding:18px;color:#26313b">${escapeHtml(message)}</p>`;
    win.focus();
  } catch {}
  return win;
}

function navigateManagedWindow(win, url) {
  if (win && !win.closed) {
    try {
      win.location.href = url;
      win.focus();
      return true;
    } catch {}
  }
  const fallback = window.open(url, "_blank", "popup=yes,width=1280,height=860");
  if (fallback) {
    try { fallback.focus(); } catch {}
    return true;
  }
  return false;
}

function prepareBrowserDialog(mode) {
  const dialog = $("#rechargeDialog");
  state.rechargeMode = mode;
  dialog.classList.toggle("payment-dialog", mode === "payment");
  dialog.classList.toggle("remote-dialog", mode === "console");
  dialog.classList.remove("fullscreen-mode");
  $("#cancelRechargeBtn").classList.toggle("hidden", mode !== "payment");
  $("#paymentFullscreenBtn").classList.toggle("hidden", mode !== "console");
  $("#openPaymentWindow").textContent = mode === "payment" ? "新窗口打开收银台" : "新窗口打开官方控制台";
  $("#openPaymentWindow").removeAttribute("href");
  $("#openPaymentWindow").classList.add("hidden");
}

async function closeRechargeBackend(accountId, quiet = true) {
  if (!accountId || state.rechargeClosing) return;
  state.rechargeClosing = true;
  try {
    const result = await api(`/api/accounts/${accountId}/recharge/close`, { method: "POST", keepalive: true });
    if (!quiet) toast(result.message || "充值页面已关闭");
  } catch (error) {
    if (!quiet) toast(error.message || "关闭充值页面失败");
  } finally {
    state.rechargeClosing = false;
  }
}

function resetBrowserDialog() {
  stopRechargeProgress();
  $("#rechargeFrame").src = "about:blank";
  $("#rechargeDialog").classList.remove("remote-dialog", "payment-dialog", "fullscreen-mode");
  $("#paymentMethods").classList.add("hidden");
  $("#openPaymentWindow").classList.add("hidden");
  $("#openPaymentWindow").removeAttribute("href");
  $("#cancelRechargeBtn").classList.add("hidden");
  $("#paymentFullscreenBtn").classList.add("hidden");
  state.rechargeAccountId = 0;
  state.paymentUrl = "";
  state.rechargeMode = "";
  state.rechargeToken += 1;
}

function closeRechargeDialog({ quiet = true } = {}) {
  const accountId = state.rechargeAccountId;
  const mode = state.rechargeMode;
  const dialog = $("#rechargeDialog");
  if (document.fullscreenElement === dialog) document.exitFullscreen().catch(() => {});
  if (dialog.open) dialog.close();
  resetBrowserDialog();
  if (mode === "payment" && accountId) closeRechargeBackend(accountId, quiet);
}

async function openEcsRemoteAccess(accountId, resourceId, resourceName = "") {
  const remoteWindow = openManagedWindow(resourceName ? `正在获取 ${resourceName} 的官方远程登录地址...` : "正在获取天翼云官方远程登录地址...");
  try {
    const result = await api(`/api/accounts/${accountId}/ecs/${encodeURIComponent(resourceId)}/remote-login`, { method: "POST" });
    if (result.url) {
      const opened = navigateManagedWindow(remoteWindow, result.url);
      if (!opened) toast("浏览器拦截了新窗口，请允许弹出窗口后重试");
    } else {
      if (remoteWindow) remoteWindow.close();
    }
    toast(result.status === "ready" ? "官方远程登录入口已打开" : result.message || "请在官方页面手动处理");
  } catch (error) {
    if (remoteWindow) remoteWindow.close();
    toast(error.message);
  }
}

async function openOfficialConsole(accountId = selectedAccountId()) {
  const id = Number(accountId || 0);
  if (!id) return toast("请先在顶部选择一个云账号");
  prepareBrowserDialog("console");
  $("#rechargeTitle").textContent = `${accountName(id)} - 官方控制台`;
  $("#rechargeHint").textContent = "正在打开天翼云官方控制台...";
  showPaymentStatus("正在连接官方控制台...");
  $("#rechargeDialog").showModal();
  try {
    const result = await api(`/api/accounts/${id}/console/open`, { method: "POST" });
    const viewerUrl = result.viewer_url || "/static/vnc.html";
    $("#rechargeHint").textContent = result.message || "官方控制台已打开";
    $("#openPaymentWindow").href = `${viewerUrl}${viewerUrl.includes("?") ? "&" : "?"}mode=console&t=${Date.now()}`;
    $("#openPaymentWindow").textContent = "新窗口打开官方控制台";
    $("#openPaymentWindow").classList.remove("hidden");
    $("#rechargeFrame").src = $("#openPaymentWindow").href;
    toast(result.status === "ready" ? "官方控制台已打开" : result.message || "请在官方页面手动处理");
  } catch (error) {
    $("#rechargeHint").textContent = error.message;
    showPaymentStatus(error.message, true);
    toast(error.message);
  }
}

async function openRecharge(id) {
  openFieldsDialog(
    "创建充值订单",
    `账号：${accountName(id)}。确认后将创建真实订单并直接加载所选渠道的收款信息。`,
    [
      {
        name: "amount",
        label: "充值金额（元）",
        type: "number",
        value: "100",
        min: "0.01",
        max: "99999999.99",
        step: "0.01",
        inputmode: "decimal",
        required: true,
        wide: true,
      },
      {
        name: "payment_method",
        label: "支付方式",
        type: "select",
        required: true,
        wide: true,
        options: [
          { value: "wechat", label: "微信支付" },
          { value: "alipay", label: "支付宝" },
          { value: "bestpay", label: "翼支付" },
        ],
      },
    ],
    async (data) => {
      state.rechargeAccountId = id;
      prepareBrowserDialog("payment");
      state.rechargeToken += 1;
      const rechargeToken = state.rechargeToken;
      state.paymentUrl = "";
      $("#fieldsDialog").close();
      $("#rechargeTitle").textContent = `${accountName(id)} - 正在创建充值订单`;
      $("#paymentMethods").classList.add("hidden");
      startRechargeProgress(id, data.payment_method);
      $("#rechargeDialog").showModal();
      toast(`正在创建 ${accountName(id)} 的 ¥${data.amount} 充值订单...`);
      try {
        const result = await api(`/api/accounts/${id}/recharge/order`, {
          method: "POST",
          body: JSON.stringify({ amount: data.amount, payment_method: data.payment_method }),
        });
        if (state.rechargeToken !== rechargeToken || state.rechargeMode !== "payment" || state.rechargeAccountId !== id) return;
        stopRechargeProgress();
        state.paymentUrl = result.url || "";
        $("#rechargeTitle").textContent = `${accountName(id)} - 支付中心`;
        $("#rechargeHint").textContent = result.payment_message || result.message || "收款信息已加载";
        $("#paymentMethods").classList.toggle("hidden", result.payment_status === "ready");
        setActivePaymentMethod(result.payment_method || data.payment_method);
        showPaymentResult(id, result);
        if (state.paymentUrl) {
          $("#openPaymentWindow").href = state.paymentUrl;
          $("#openPaymentWindow").classList.remove("hidden");
        }
        toast(result.payment_status === "ready" ? "收款信息已加载" : "订单已创建，可重新选择支付方式");
        await loadAccounts();
      } catch (error) {
        if (state.rechargeToken !== rechargeToken || state.rechargeMode !== "payment" || state.rechargeAccountId !== id) return;
        stopRechargeProgress();
        $("#rechargeTitle").textContent = `${accountName(id)} - 充值失败`;
        $("#rechargeHint").textContent = error.message;
        showPaymentStatus(error.message, true);
        toast(error.message);
      }
    },
    "创建订单并显示收款码",
  );
}

function setActivePaymentMethod(method) {
  document.querySelectorAll("[data-payment-method]").forEach((button) => {
    button.classList.toggle("active", button.dataset.paymentMethod === method);
  });
}

function paymentMethodLabel(method) {
  return ({ wechat: "微信", alipay: "支付宝", bestpay: "翼支付" }[method] || "所选渠道");
}

function startRechargeProgress(accountId, method = "") {
  const methodLabel = paymentMethodLabel(method);
  const steps = [
    `正在检查 ${accountName(accountId)} 的天翼云登录态...`,
    "正在复用后台预热的官方充值页...",
    "正在创建官方充值订单...",
    `正在生成${methodLabel}收款码...`,
    "官方接口响应较慢，仍在等待收款码...",
  ];
  let index = 0;
  stopRechargeProgress();
  const update = () => {
    const message = steps[Math.min(index, steps.length - 1)];
    $("#rechargeHint").textContent = message;
    showPaymentStatus(message);
    index += 1;
  };
  update();
  state.rechargeProgressTimer = window.setInterval(update, 3500);
}

function stopRechargeProgress() {
  if (!state.rechargeProgressTimer) return;
  window.clearInterval(state.rechargeProgressTimer);
  state.rechargeProgressTimer = 0;
}

function showPaymentStatus(message, isError = false, mode = "") {
  const params = new URLSearchParams({
    message,
    mode: mode || (isError ? "error" : "loading"),
  });
  $("#rechargeFrame").src = `/static/payment-status.html?${params}`;
}

function showPaymentResult(accountId, result) {
  if (result.qr_available) {
    const method = encodeURIComponent(result.payment_method || "wechat");
    $("#rechargeFrame").src = `/static/qr.html?account_id=${accountId}&payment_method=${method}&t=${Date.now()}`;
  } else {
    $("#rechargeFrame").src = "/static/vnc.html?mode=payment";
  }
}

document.querySelectorAll("[data-payment-method]").forEach((button) => {
  button.onclick = async () => {
    if (!state.rechargeAccountId) return;
    const method = button.dataset.paymentMethod;
    const methodLabel = paymentMethodLabel(method);
    $("#rechargeHint").textContent = `正在切换到${methodLabel}...`;
    showPaymentStatus(`正在生成${methodLabel}收款码...`);
    document.querySelectorAll("[data-payment-method]").forEach((item) => { item.disabled = true; });
    try {
      const result = await api(`/api/accounts/${state.rechargeAccountId}/recharge/payment`, {
        method: "POST",
        body: JSON.stringify({ payment_method: method }),
      });
      setActivePaymentMethod(method);
      showPaymentResult(state.rechargeAccountId, result);
      $("#rechargeHint").textContent = result.message || "收款信息已加载";
      toast(result.message || "收款信息已加载");
    } catch (error) {
      $("#rechargeHint").textContent = error.message;
      showPaymentStatus(error.message, true);
      toast(error.message);
    } finally {
      document.querySelectorAll("[data-payment-method]").forEach((item) => { item.disabled = false; });
    }
  };
});

window.addEventListener("message", (event) => {
  if (event.origin !== location.origin) return;
  if (event.data?.type === "ctyun-close-recharge") {
    closeRechargeDialog({ quiet: false });
    return;
  }
  if (event.data?.type === "ctyun-recharge-status") {
    const message = event.data.message || "官方支付状态已更新";
    $("#rechargeHint").textContent = message;
    if (["paid", "success", "completed"].includes(event.data.status)) {
      $("#rechargeTitle").textContent = `${accountName(state.rechargeAccountId)} - 支付成功`;
      showPaymentStatus(`${message}，正在刷新余额...`, false, "success");
      toast(message);
      clearApiCache();
      loadAccounts().then(() => render()).catch(() => {});
    }
    return;
  }
  if (event.data?.type !== "ctyun-qr-fallback") return;
  $("#rechargeFrame").src = "/static/vnc.html?mode=payment";
  $("#rechargeHint").textContent = event.data.message || "二维码无法直接显示，已切换到 VNC";
});

$("#paymentFullscreenBtn").onclick = async () => {
  const dialog = $("#rechargeDialog");
  if (!document.fullscreenElement && dialog.classList.contains("fullscreen-mode")) {
    dialog.classList.remove("fullscreen-mode");
    return;
  }
  try {
    if (document.fullscreenElement) {
      await document.exitFullscreen();
      dialog.classList.remove("fullscreen-mode");
    } else if (dialog.requestFullscreen) {
      dialog.classList.add("fullscreen-mode");
      await dialog.requestFullscreen();
    } else {
      dialog.classList.toggle("fullscreen-mode");
    }
  } catch {
    dialog.classList.add("fullscreen-mode");
  }
};

document.addEventListener("fullscreenchange", () => {
  const dialog = $("#rechargeDialog");
  dialog.classList.toggle("fullscreen-mode", document.fullscreenElement === dialog);
});

$("#cancelRechargeBtn").onclick = () => closeRechargeDialog({ quiet: false });

$("#loginForm").onsubmit = async (event) => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(event.target));
  try {
    await api("/api/login", { method: "POST", body: JSON.stringify(data) });
    showApp(); await loadAccounts(); triggerRechargePrewarm(); await withPageLoading("正在加载控制台...", render);
  } catch (error) { $("#loginError").textContent = error.message; }
};

$("#accountForm").onsubmit = async (event) => {
  event.preventDefault();
  $("#accountError").textContent = "";
  const data = Object.fromEntries(new FormData(event.target));
  try {
    const id = data.id;
    delete data.id;
    await api(id ? `/api/accounts/${id}` : "/api/accounts", { method: id ? "PUT" : "POST", body: JSON.stringify(data) });
    event.target.reset(); $("#accountDialog").close();
    await loadAccounts(); triggerRechargePrewarm(); state.view = "accounts"; await withPageLoading("正在刷新账号...", render); toast(id ? "账号已更新" : "账号资料已加密保存");
  } catch (error) { $("#accountError").textContent = error.message; }
};

$("#ikuaiForm").onsubmit = async (event) => {
  event.preventDefault();
  $("#ikuaiError").textContent = "";
  const data = Object.fromEntries(new FormData(event.target));
  try {
    const id = data.id;
    delete data.id;
    await api(id ? `/api/ikuai/gateways/${id}` : "/api/ikuai/gateways", {
      method: id ? "PUT" : "POST",
      body: JSON.stringify(data),
    });
    event.target.reset();
    $("#ikuaiDialog").close();
    clearApiCache("/api/ikuai");
    state.view = "ikuai";
    await withPageLoading("正在刷新爱快网关...", render);
    toast(id ? "爱快网关已更新" : "爱快网关已加密保存");
  } catch (error) {
    $("#ikuaiError").textContent = error.message;
  }
};

function openAccountDialog(id = 0) {
  const form = $("#accountForm");
  form.reset();
  $("#accountError").textContent = "";
  const account = id ? state.accounts.find((a) => a.id === id) : null;
  form.elements.id.value = account?.id || "";
  form.elements.name.value = account?.name || "";
  form.elements.provider_account_id.value = account?.provider_account_id || "";
  form.elements.region.value = account?.region || "";
  form.elements.notes.value = account?.notes || "";
  const editing = Boolean(account);
  $("#accountDialogTitle").textContent = editing ? "编辑天翼云账号" : "添加天翼云账号";
  $("#accountDialogHint").textContent = editing ? "敏感字段留空表示保持原值，填写则覆盖加密保存" : "一次性录入 API 与网页登录所需资料";
  $("#saveAccountBtn").textContent = editing ? "保存修改" : "加密保存账号";
  ["username", "password", "totp_secret", "ak", "sk"].forEach((name) => {
    form.elements[name].required = !editing;
    form.elements[name].placeholder = editing ? "留空则不修改" : form.elements[name].placeholder;
  });
  $("#accountDialog").showModal();
}

$("#addAccountBtn").onclick = () => openAccountDialog();
$("#consoleBtn").onclick = () => openOfficialConsole(selectedAccountId());
$("#logoutBtn").onclick = async () => { clearInterval(state.refreshTimer); await api("/api/logout", { method: "POST" }); showLogin(); };
$("#syncBtn").onclick = async () => {
  const button = $("#syncBtn");
  if (state.manualSyncing || state.refreshing) return toast("同步正在进行，请稍后");
  const ids = selectedAccountId() ? [selectedAccountId()] : state.accounts.map((a) => a.id);
  if (!ids.length) return toast("请先添加账号");
  state.manualSyncing = true;
  state.refreshing = true;
  button.disabled = true;
  const text = button.textContent;
  const errors = [];
  const totals = [];
  try {
    for (let index = 0; index < ids.length; index += 1) {
      const id = ids[index];
      const label = `同步 ${index + 1}/${ids.length}`;
      button.textContent = label;
      updateRefreshBadge(`${label}: ${accountName(id)}`);
      toast(`正在同步 ${accountName(id)}...`);
      const result = await syncAccount(id, {
        showPageLoading: false,
        renderAfter: false,
        clearCache: false,
        quiet: true,
        throwError: false,
      });
      if (result?.error) {
        errors.push(`${accountName(id)}: ${result.error}`);
      } else {
        totals.push(Object.values(result?.counts || {}).reduce((a, b) => a + b, 0));
      }
      await nextFrame();
    }
    state.lastRefreshAt = Date.now();
    clearResourceCaches();
    updateRefreshBadge();
    toast(errors.length ? `同步完成，${errors.length} 个账号失败：${errors[0]}` : `同步完成：${totals.reduce((a, b) => a + b, 0)} 项`);
    resetPageLoading();
    await render();
  } finally {
    state.manualSyncing = false;
    state.refreshing = false;
    button.disabled = false;
    button.textContent = text;
    updateRefreshBadge();
  }
};
$("#fieldsForm").onsubmit = async (event) => {
  event.preventDefault();
  $("#fieldsError").textContent = "";
  const submitBtn = $("#fieldsSubmitBtn");
  const submitText = submitBtn.textContent;
  submitBtn.disabled = true;
  submitBtn.textContent = "处理中...";
  const data = fieldFormValues();
  try {
    const callback = state.pendingFieldsAction;
    const selectedOptions = {};
    state.pendingFieldDefinitions.forEach((field) => {
      const input = event.target.elements[field.name];
      const options = state.pendingFieldOptions.get(field.name) || [];
      selectedOptions[field.name] = options.find((option) => String(option.value) === String(input?.value || "")) || null;
    });
    if (callback) await callback(data, { selectedOptions });
    state.pendingFieldsAction = null;
    state.pendingFieldDefinitions = [];
    state.pendingFieldOptions = new Map();
    state.pendingFieldGroupedOptions = new Map();
    event.target.reset();
    $("#fieldsDialog").close();
  } catch (error) {
    if (isFlavorSoldOutMessage(error.message || "") && data.flavorID) {
      await markSelectedFlavorSoldOut(data);
      toast("该规格已确认不可购买，已从当前列表移除");
    }
    if (isImageUnavailableMessage(error.message || "") && data.imageID) {
      await markSelectedImageUnavailable(data);
      toast("该镜像不适用于当前资源池或规格，已从当前列表移除");
    }
    $("#fieldsError").textContent = isOnDemandForbiddenMessage(error.message || "")
      ? "当前账号暂不支持按量计费或余额未满足按量开通条件，请切换为包年包月后再提交。"
      : error.message;
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = submitText;
  }
};
$("#accountFilter").onchange = async () => {
  state.viewSwitchUntil = Date.now() + 1800;
  clearInterval(state.refreshTimer);
  resetPageLoading();
  try {
    await render();
    window.setTimeout(prefetchResourceViews, 80);
  } catch (error) {
    toast(error.message);
  } finally {
    resetPageLoading();
    configureAutoRefresh();
  }
};
$("#nav").onclick = async (event) => {
  const button = event.target.closest("[data-view]");
  if (!button) return;
  if (state.view === button.dataset.view) return;
  state.view = button.dataset.view;
  state.viewSwitchUntil = Date.now() + 1800;
  clearInterval(state.refreshTimer);
  resetPageLoading();
  updateRefreshBadge("正在切换...");
  try {
    await render();
    window.setTimeout(prefetchResourceViews, 80);
  } catch (error) {
    toast(error.message);
  } finally {
    resetPageLoading();
    updateRefreshBadge();
    configureAutoRefresh();
  }
};
document.querySelectorAll("[data-close]").forEach((b) => b.onclick = () => {
  const dialog = b.closest("dialog");
  if (dialog.id === "rechargeDialog") {
    closeRechargeDialog({ quiet: true });
    return;
  }
  dialog.close();
});
$("#rechargeDialog").addEventListener("cancel", (event) => {
  event.preventDefault();
  closeRechargeDialog({ quiet: true });
});
document.addEventListener("click", (event) => {
  if (event.target.closest(".grouped-select-field")) return;
  document.querySelectorAll(".grouped-select-menu").forEach((menu) => menu.classList.add("hidden"));
});
$("#confirmActionBtn").onclick = async () => {
  const button = $("#confirmActionBtn");
  const text = button.textContent;
  const callback = state.pendingAction; state.pendingAction = null; $("#actionDialog").close();
  button.disabled = true;
  button.textContent = "处理中...";
  if (callback) try { await callback(); } catch (error) { toast(error.message); }
  button.disabled = false;
  button.textContent = text;
};
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    clearInterval(state.refreshTimer);
    updateRefreshBadge("已暂停");
  } else {
    configureAutoRefresh(true);
  }
});
window.addEventListener("focus", () => {
  if (!state.initializing && !document.hidden) refreshCurrentView(true).catch(() => {});
});

(async function init() {
  try {
    const version = await api("/api/version");
    state.version = version.version || "";
    state.encryptionKeyStatus = version.encryption_key_status || "";
    renderVersion();
  } catch {
    state.version = "";
    renderVersion();
  }
  try {
    const me = await api("/api/me");
    state.mode = me.ctyun_mode || "openapi";
    state.encryptionKeyStatus = me.encryption_key_status || "";
    showApp();
    await loadAccounts();
    triggerRechargePrewarm();
    await withPageLoading("正在加载控制台...", render);
    state.initializing = false;
    configureAutoRefresh();
    updateRefreshBadge();
  }
  catch { state.initializing = false; showLogin(); }
})();

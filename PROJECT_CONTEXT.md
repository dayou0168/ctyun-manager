# Codex 项目上下文

更新时间：2026-06-18 23:51 Asia/Shanghai

## 项目目标

本项目是一个天翼云多账号管理平台。核心目标是把天翼云账号资源、余额充值、官方控制台访问，以及爱快网关 Web 管理统一到一个平台里，避免人工反复登录不同后台。

后续工作将在本项目目录继续：

```text
C:\Users\Administrator\Documents\Codex\2026-06-08\ip\outputs\ctyun-manager
```

## 已完成事项

- 天翼云多账号管理、资源同步、资源操作、余额缓存。
- 天翼云官方网页登录集成，包含 TOTP/MFA 自动登录。
- 官方控制台/VNC 访问。
- 天翼云充值模块：
  - 支持微信、支付宝、翼支付。
  - 官方二维码提取为 PNG，避免 VNC 截图模糊。
  - 二维码过期后调用官方刷新逻辑。
  - 支付状态通过官方 `/unifyapi/upayquery` 查询。
  - 后台预热官方充值页。
  - 关闭充值弹窗时保留充值页，只关闭收银台页。
  - 首冲/下单流程有分阶段提示。
  - 支付成功后显示平台成功页，并强制刷新余额。
  - 手动刷新余额强制调用官方余额接口，不再优先读取页面旧 DOM。
- 爱快网关模块：
  - 平台侧左侧菜单区分“天翼云”和“爱快”。
  - 爱快主菜单按 3.7+ UI 参考补齐大量功能映射。
  - AC 管理已按用户要求移除。
  - 多数列表具备编辑入口，英文操作已改为中文。
- 本地版本：
  - 当前构建：`2026.06.18.2351`
  - 本地验证地址：`http://127.0.0.1:8000/`

## 关键文件结构

```text
app/
  main.py                         FastAPI 主入口、API 路由、后台任务
  config.py                       环境变量配置
  db.py                           SQLite 表结构迁移
  security.py                     加密、会话、密码处理
  services/
    ctyun_client.py               天翼云 OpenAPI 客户端
    browser_automation.py         天翼云网页登录、充值、VNC、余额读取
    ikuai_client.py               爱快 Web API 封装和菜单映射
  static/
    index.html                    主页面
    app.js                        主前端逻辑
    styles.css                    主样式
    qr.html / qr.js               充值二维码页
    vnc.html / vnc.js             noVNC 页面
    payment-status.html           支付加载/错误/成功状态页

API_REFERENCE.md                  内部接口记录
PROJECT_CONTEXT.md                Codex 续接上下文
README.md                         项目说明
install.sh/start.sh/restart.sh    Linux 部署脚本
requirements.txt                  Python 依赖
```

## 关键决策

- 天翼云充值使用官方网页登录态，不使用 AK/SK OpenAPI 创建充值。
- 余额接口是天翼云官网登录后网页内部接口：
  - `GET /gw/account/giftcard/QueryBookSumm`
  - 字段：`cashPoints`、`accountId`
- 余额刷新必须优先调用官方接口，并添加 cache busting：
  - URL 增加 `_t=timestamp`
  - fetch 使用 `cache: "no-store"`
  - header 使用 `Cache-Control: no-cache`
- 官方页面 DOM 余额只作为兜底，不应作为手动刷新优先来源。
- 支付状态以官方 `/unifyapi/upayquery` 为准。
- 快速下单路径曾出现官方重定向回充值页，因此必须验证收银台控件真实出现，不能只看 URL。
- 包内和 GitHub 仓库禁止包含数据库、密钥、浏览器状态、日志。

## 当前待办

- 将本项目推送到用户 GitHub。
  - 当前本机没有 `git` 和 `gh` 命令。
  - 当前环境没有发现 `GITHUB_TOKEN`。
  - 需要用户提供 GitHub 仓库地址或 Personal Access Token，或先安装并登录 Git/GitHub CLI。
- 公网 `http://43.119.30.80:8000/` 需要部署最新包后才会更新。
  - 最近一次本地版本：`2026.06.18.2351`
  - 之前公网曾停留在旧版本，部署后应先检查 `/api/version`。
- 继续实测支付成功后：
  - 平台弹窗是否显示成功页。
  - 点击刷新余额是否立即读取最新 `cashPoints`。
  - `/api/accounts/{id}/balance` 返回 message 是否为“余额已从天翼云官方接口读取”。
- 爱快模块仍需后续用真实不同版本网关继续补齐数据映射。

## 续接建议

新对话开始时，可以这样提示 Codex：

```text
请先阅读 README.md、PROJECT_CONTEXT.md、API_REFERENCE.md，然后继续这个天翼云/爱快管理平台项目。
当前重点是验证充值成功后的余额刷新和 GitHub 仓库同步。
```

## GitHub 推送准备

推荐仓库名：

```text
ctyun-manager
```

推荐首次提交说明：

```text
Initial ctyun manager platform with recharge and ikuai modules
```

推送前必须确认以下文件未被提交：

```text
.env
master.key
ctyun-manager.db
ctyun-manager.db-shm
ctyun-manager.db-wal
data/
.venv/
.playwright/
*.log
```

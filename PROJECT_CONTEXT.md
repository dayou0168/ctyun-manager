# Codex 项目上下文

更新时间：2026-06-19 Asia/Shanghai

## 项目目标

本项目是一个天翼云多账号管理平台。核心目标是把天翼云账号资源、余额充值、官方控制台访问，以及爱快网关 Web 管理统一到一个平台里，避免人工反复登录不同后台。

后续工作将在本项目目录继续：

```text
C:\Users\Administrator\Documents\Codex\2026-06-08\ip\outputs\ctyun-manager
```

## 协作规则

- 以后 Codex 对代码、配置、脚本或项目文档做有效更新后，需要同步提交并推送到 GitHub。
- 默认远程仓库：`https://github.com/dayou0168/ctyun-manager`
- 默认分支：`main`
- 每次更新流程：
  - 先检查 `git status`，确认没有误提交数据库、密钥、日志、缓存等运行态文件。
  - 完成必要验证后执行 `git add`、`git commit`。
  - 推送到 `origin/main`。
- 除非用户明确要求，不要提交 `.env`、`master.key`、`ctyun-manager.db*`、`data/`、`.playwright/`、`.venv/`、日志文件。

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
- RustDesk 定制工具：
  - 左侧 `应用工具 / RustDesk 定制`。
  - 后端接口：`/api/tools/rustdesk/jobs`。
  - 使用 classic PAT 临时访问用户公开 GitHub 仓库，token 不保存数据库，不写日志。
  - token 至少需要 `workflow`；建议 `public_repo + workflow`。如果 GitHub UI 选择 `workflow` 时自动勾选 `repo`，平台允许继续，但仍强制目标仓库必须公开。
  - 校验官方 RustDesk tag，例如 `1.4.7`。
  - 拉取官方源码、递归子模块、本地化子模块、应用源码补丁、推送到目标仓库。
  - 采用 1.4.7 成功方案：服务器信息写源码常量，不使用 `res/local_custom_client.json`。
- 本地版本：
  - 当前构建：`2026.06.19.2319`
  - 本地验证地址：`http://127.0.0.1:8000/`
- 部署方式：
  - Linux 服务器直装远程入口：`install-linux.sh`
  - Docker Compose 远程入口：`install-compose.sh`
  - Docker Compose 直接部署文件：`docker-compose.deploy.yml`
  - 本地源码直装：`sudo ./install.sh`
  - 本地源码 Docker Compose：`sudo ./install-docker.sh`
  - 两种方式默认端口都是 `8000`，可通过 `CTYUN_MANAGER_PORT=8080` 改端口。

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
    rustdesk_customizer.py        RustDesk 定制源码生成和 GitHub 写入
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
install-linux.sh                  GitHub curl 直装入口
install-compose.sh                GitHub curl Docker Compose 入口
install.sh/start.sh/restart.sh    本地源码 Linux 部署脚本
install-docker.sh                 本地源码 Docker Compose 部署脚本
Dockerfile/docker-compose.yml      本地源码 Docker Compose 构建文件
docker-compose.deploy.yml          发布版 Docker Compose 直接部署文件
.github/workflows/docker-image.yml GHCR 镜像发布 workflow
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
- 用户想要的 Linux 一键脚本是直接 curl GitHub 上的 `.sh`，然后自动下载项目并完成安装；主入口为 `install-linux.sh`。
- GitHub 仓库当前是 Private，免 token 的 `raw.githubusercontent.com` curl 命令需要仓库改 Public；Private 状态下需要用 `GITHUB_TOKEN` 通过 GitHub API 读取脚本和源码包。
- 直装方式使用 systemd 运行 `start.sh`，脚本负责启动 Xvfb、fluxbox、x11vnc 和 FastAPI。
- `docker-compose.deploy.yml` 直接拉取 `ghcr.io/dayou0168/ctyun-manager:latest`，不需要服务器有完整源码。
- GHCR 镜像通过 `.github/workflows/docker-image.yml` 在推送 `main` 后自动构建发布。
- 2026-06-19 首次触发 GHCR workflow 失败，GitHub 返回：账号 recent account payments failed 或 spending limit 需要调整。修复 GitHub Billing 后重新运行 workflow，直接 yaml 部署才有可拉取镜像。
- 发布版 Docker Compose 使用命名卷 `ctyun-manager-data` 持久化 `/app/data`，迁移时必须备份该 volume 内的 `master.key` 和 SQLite 数据库。
- 本地源码 Docker Compose 仍保留 `docker-compose.yml`，它使用 `build:` 从当前源码构建镜像，并把本地 `./data` 挂载到容器 `/app/data`。
- RustDesk 定制不使用数据库保存 token。任务状态只保存在进程内存，服务重启后历史任务会丢失。若用户只能创建带 `repo` 权限的 classic token，建议任务完成后立即删除该 token。
- RustDesk GitHub 写入使用临时 authenticated HTTPS remote，不依赖 `GIT_ASKPASS`，避免服务器 `/tmp` 权限或 noexec 导致 `git push` 无法读取用户名。
- RustDesk 1.4.7 方案：
  - `libs/hbb_common/src/config.rs` 写 `RENDEZVOUS_SERVERS` 和 `RS_PUB_KEY`。
  - `src/common.rs` 在 `load_custom_client()` 中写入 `config::HARD_SETTINGS`。
  - 删除 `res/local_custom_client.json` 和 `.github/scripts/apply-bundled-server-settings.py`。
  - workflows 删除 server secrets 相关行，并把 checkout submodules 设为 false。

## 当前待办

- 公网 `http://43.119.30.80:8000/` 需要部署最新包后才会更新。
  - 最近一次本地版本：`2026.06.19.2319`
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
当前重点是验证充值成功后的余额刷新、爱快真实网关映射，以及部署脚本实机安装效果。
```

## GitHub 仓库

仓库地址：

```text
https://github.com/dayou0168/ctyun-manager
```

默认分支：

```text
main
```

提交前必须确认以下文件未被提交：

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

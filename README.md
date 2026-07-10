# 天翼云多账号管理台

一个面向个人/小团队的天翼云多账号管理平台，已集成天翼云资源管理、余额/充值、官方后台 cookie/API 能力、爱快网关管理，以及 Linux 服务器 SSH 管理模块。

## 当前能力

- 天翼云账号资料加密保存，支持网页登录态和 OpenAPI 凭证。
- 云主机、弹性 IP、VPC、子网、安全组、镜像等资源通过天翼云 OpenAPI 同步、展示和常用操作，资源列表不依赖 VNC。
- 天翼云官方充值流程集成，支持微信、支付宝、翼支付二维码。
- 充值二维码刷新、支付状态轮询、支付成功后余额强制刷新。
- 爱快网关管理模块，按爱快 3.7+ UI 菜单映射主要功能。
- Linux 服务器 SSH 管理模块，支持多台服务器资料加密保存、连接测试、平台内 xterm.js SSH 会话、鼠标选择复制、键盘输入、粘贴和单次快速命令执行。
- RustDesk 定制工具：填写公开 GitHub 仓库、classic token、官方版本和服务器配置后，自动生成定制源码并推送到目标仓库。
- 服务端后台预热天翼云充值页，减少首冲打开等待。

## 项目硬约束

- 天翼云模块不再接受 VNC/noVNC 方案。
- 新功能、修复和兜底路径优先使用官方 OpenAPI。
- OpenAPI 未覆盖时，优先使用平台已保存 cookie 的后台 HTTP/API 调用。
- 如果 OpenAPI 和 cookie 后台接口都无法实现，应明确提示该能力不可用或需要重新抓取接口，不再通过 VNC/远程桌面实现。

## 重装系统 / 续接开发

重装系统前后的项目交接以 `REINSTALL_HANDOFF.md` 和 `PROJECT_CONTEXT.md` 为准。当前源码远程仓库：

```text
https://github.com/dayou0168/ctyun-manager.git
```

当前线上平台：

```text
http://43.119.30.80:8000/
```

线上目录为 `/www/wwwroot/ctyun-manager`，服务名为 `ctyun-manager`。重装本机后先 clone GitHub 仓库，再阅读：

- `REINSTALL_HANDOFF.md`
- `PROJECT_CONTEXT.md`
- `API_REFERENCE.md`

不要从 GitHub 恢复 `.env`、`master.key`、SQLite 数据库、浏览器 cookie、SSH 密码/私钥等运行数据；这些文件必须从线上服务器或备份中单独迁移。

## 重要安全说明

仓库不应提交以下文件：

- `.env`
- `master.key`
- `ctyun-manager.db*`
- `data/`
- Linux 服务器 SSH 密码和私钥
- `.playwright/`
- `.venv/`
- 日志文件

这些文件包含本地密钥、数据库、浏览器状态或运行日志，已经写入 `.gitignore`。

## 本地运行

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Windows 开发环境也可以直接运行：

```powershell
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

打开：

```text
http://127.0.0.1:8000/
```

## 部署方式一：Linux 服务器直装

适合直接部署到 Ubuntu/Debian 服务器。服务器只需要执行一条 curl 命令，脚本会自动下载项目到 `/opt/ctyun-manager`，更新系统包，安装 Python、Playwright/Chromium、字体等依赖，并创建 systemd 服务 `ctyun-manager`。历史脚本中可能仍保留 Xvfb/x11vnc/fluxbox 遗留依赖，但天翼云业务功能不再允许依赖 VNC。

仓库公开时可直接执行：

```bash
curl -fsSL https://raw.githubusercontent.com/dayou0168/ctyun-manager/main/install-linux.sh | sudo bash
```

当前仓库为 Private 时，需要先准备一个有 `repo` 权限的 GitHub Token：

```bash
export GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
curl -fsSL \
  -H "Authorization: Bearer $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github.raw" \
  https://api.github.com/repos/dayou0168/ctyun-manager/contents/install-linux.sh \
  | sudo GITHUB_TOKEN="$GITHUB_TOKEN" bash
```

默认访问地址：

```text
http://SERVER_IP:8000/
```

常用命令：

```bash
sudo ./restart.sh
journalctl -u ctyun-manager -f
```

如果不希望脚本执行系统包升级，可以这样运行：

```bash
curl -fsSL https://raw.githubusercontent.com/dayou0168/ctyun-manager/main/install-linux.sh \
  | sudo CTYUN_MANAGER_SKIP_SYSTEM_UPGRADE=1 bash
```

如果 8000 端口被占用，可以指定端口：

```bash
curl -fsSL https://raw.githubusercontent.com/dayou0168/ctyun-manager/main/install-linux.sh \
  | sudo CTYUN_MANAGER_PORT=8080 bash
```

如果已经手动下载了项目源码，也可以在项目目录内执行：

```bash
sudo ./install.sh
```

## 部署方式二：Docker Compose 直接部署

适合希望隔离运行环境的服务器。发布版 Compose 文件 `docker-compose.deploy.yml` 可以直接拉取镜像部署，不需要服务器上有完整源码。这个方式依赖镜像 `ghcr.io/dayou0168/ctyun-manager:latest` 已经由 GitHub Actions 构建发布。

如果服务器已经安装 Docker 和 Docker Compose，可直接下载 yaml 部署：

```bash
mkdir -p /opt/ctyun-manager
cd /opt/ctyun-manager
curl -fsSLO https://raw.githubusercontent.com/dayou0168/ctyun-manager/main/docker-compose.deploy.yml
cat >.env <<EOF
CTYUN_MANAGER_ADMIN_USER=admin
CTYUN_MANAGER_ADMIN_PASSWORD=$(openssl rand -hex 24)
CTYUN_MANAGER_SESSION_SECRET=$(openssl rand -hex 48)
CTYUN_MANAGER_PORT=8000
EOF
chmod 600 .env
docker compose -f docker-compose.deploy.yml up -d
```

如果服务器还没有 Docker，使用一键脚本安装 Docker、下载 yaml、生成 `.env` 并启动：

仓库公开时可直接执行：

```bash
curl -fsSL https://raw.githubusercontent.com/dayou0168/ctyun-manager/main/install-compose.sh | sudo bash
```

当前仓库为 Private 时，需要先准备一个有 `repo` 和 `read:packages` 权限的 GitHub Token：

```bash
export GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
curl -fsSL \
  -H "Authorization: Bearer $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github.raw" \
  https://api.github.com/repos/dayou0168/ctyun-manager/contents/install-compose.sh \
  | sudo GITHUB_TOKEN="$GITHUB_TOKEN" bash
```

如果 GHCR 镜像仍是 Private，直接使用 yaml 部署前还需要登录镜像仓库：

```bash
echo "$GITHUB_TOKEN" | docker login ghcr.io -u dayou0168 --password-stdin
```

默认访问地址：

```text
http://SERVER_IP:8000/
```

常用命令：

```bash
docker compose ps
docker compose logs -f
docker compose restart
```

如需换端口：

```bash
curl -fsSL https://raw.githubusercontent.com/dayou0168/ctyun-manager/main/install-compose.sh \
  | sudo CTYUN_MANAGER_PORT=8080 bash
```

`docker-compose.deploy.yml` 模式的数据保存在 Docker 命名卷 `ctyun-manager-data`，包括 SQLite 数据库和 `master.key`。迁移服务器时必须一起备份迁移该 volume，否则已加密的账号密码无法解密。

说明：`docker-compose.deploy.yml` 使用 GHCR 镜像直接部署；`docker-compose.yml` 保留给已经下载源码后的本地构建部署。

## 首次登录与安全

两种部署方式首次账号均为：

```text
用户名：admin
密码：change-me-now
```

安装后请编辑 `.env`，修改 `CTYUN_MANAGER_ADMIN_PASSWORD`，然后重启服务。

## RustDesk 定制工具

平台左侧进入 `应用工具 / RustDesk 定制`。

要求：

- 目标仓库必须是 GitHub 公开仓库。
- 使用 Personal access token classic。
- GitHub API 令牌使用 classic token，表单内可直接跳转到 GitHub 创建页面。令牌至少需要 `workflow`，公开仓库写入需要 `public_repo`；如果 GitHub 选择 `workflow` 时自动勾选 `repo`，可以继续使用，但建议任务完成后立即删除 token。
- token 只在本次后台任务中使用，不保存数据库，不写入日志。
- RustDesk 版本填写官方 tag，例如 `1.4.7`。
- 可选择需要编译的客户端，生成后的 GitHub Actions 手动运行页面会显示对应勾选项；平台会把勾选值传入实际编译 workflow，并默认生成 `rustdesk-版本-日期时间` 格式的 Release 名称。
- 可上传客户端 Logo，当前支持 PNG，建议 1024x1024。

流程：

1. 校验 GitHub 仓库、token 权限和 RustDesk 官方 tag。
2. 拉取官方 RustDesk 指定 tag。
3. 本地化子模块，尤其 `libs/hbb_common`。
4. 写入服务器、Key、默认密码等定制项到源码。
5. 删除 `res/local_custom_client.json` 方案，避免 UI 显示内置服务器值。
6. 写入 Actions 编译平台选择项、Release 发布权限、默认 Release 名称和可选 Logo 图标资源。
7. 推送到用户填写的公开仓库。
8. 用户到目标仓库 Actions 中编译客户端。

## Codex 续接

后续换对话窗口时，先让 Codex 阅读：

- `PROJECT_CONTEXT.md`
- `README.md`
- `API_REFERENCE.md`

然后再继续开发。`PROJECT_CONTEXT.md` 记录了目标、文件结构、关键决策、已完成事项和待办。

后续每次由 Codex 更新代码、配置、脚本或项目文档后，不要默认提交或推送到 GitHub。只有用户明确说“同步 GitHub / 提交 / 推送 / 发 PR”时，才执行对应 git 操作。同步前必须确认 `.env`、`master.key`、`ctyun-manager.db*`、`data/`、日志和运行态文件没有被误提交。

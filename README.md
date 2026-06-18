# 天翼云多账号管理台

一个面向个人/小团队的天翼云多账号管理平台，已集成天翼云资源管理、余额/充值、官方控制台/VNC 会话，以及爱快网关管理模块。

## 当前能力

- 天翼云账号资料加密保存，支持网页登录态和 OpenAPI 凭证。
- 云主机、弹性 IP、VPC、镜像等资源同步、展示和常用操作。
- 天翼云官方充值流程集成，支持微信、支付宝、翼支付二维码。
- 充值二维码刷新、支付状态轮询、支付成功后余额强制刷新。
- 爱快网关管理模块，按爱快 3.7+ UI 菜单映射主要功能。
- 服务端后台预热天翼云充值页，减少首冲打开等待。

## 重要安全说明

仓库不应提交以下文件：

- `.env`
- `master.key`
- `ctyun-manager.db*`
- `data/`
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

适合直接部署到 Ubuntu/Debian 服务器。服务器只需要执行一条 curl 命令，脚本会自动下载项目到 `/opt/ctyun-manager`，更新系统包，安装 Python、Playwright/Chromium、Xvfb、x11vnc、fluxbox、字体等依赖，并创建 systemd 服务 `ctyun-manager`。

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

适合希望隔离运行环境的服务器。发布版 Compose 文件 `docker-compose.deploy.yml` 可以直接拉取镜像部署，不需要服务器上有完整源码。

如果服务器已经安装 Docker 和 Docker Compose，可直接下载 yaml 部署：

```bash
mkdir -p /opt/ctyun-manager
cd /opt/ctyun-manager
curl -fsSLO https://raw.githubusercontent.com/dayou0168/ctyun-manager/main/docker-compose.deploy.yml
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

## Codex 续接

后续换对话窗口时，先让 Codex 阅读：

- `PROJECT_CONTEXT.md`
- `README.md`
- `API_REFERENCE.md`

然后再继续开发。`PROJECT_CONTEXT.md` 记录了目标、文件结构、关键决策、已完成事项和待办。

后续每次由 Codex 更新代码、配置、脚本或项目文档后，默认需要提交并推送到 GitHub 仓库 `dayou0168/ctyun-manager` 的 `main` 分支。

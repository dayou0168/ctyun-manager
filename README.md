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

适合直接部署到 Ubuntu/Debian 服务器。脚本会自动更新系统包、安装 Python、Playwright/Chromium、Xvfb、x11vnc、fluxbox、字体等依赖，并创建 systemd 服务 `ctyun-manager`。

```bash
sudo ./install.sh
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
sudo CTYUN_MANAGER_SKIP_SYSTEM_UPGRADE=1 ./install.sh
```

如果 8000 端口被占用，可以指定端口：

```bash
sudo CTYUN_MANAGER_PORT=8080 ./install.sh
```

## 部署方式二：Docker Compose 一键部署

适合希望隔离运行环境的服务器。脚本会自动安装 Docker Engine 和 Docker Compose 插件，生成 `.env`，构建镜像并启动服务。

```bash
sudo ./install-docker.sh
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
sudo CTYUN_MANAGER_PORT=8080 ./install-docker.sh
```

Docker Compose 模式的数据保存在项目目录的 `data/`，包括 SQLite 数据库和 `master.key`。迁移服务器时必须一起迁移 `data/`，否则已加密的账号密码无法解密。

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

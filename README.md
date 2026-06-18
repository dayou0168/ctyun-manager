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

## Linux 部署

项目自带安装脚本：

```bash
sudo ./install.sh
```

安装脚本会创建 systemd 服务 `ctyun-manager`，并使用 `start.sh` 启动 Xvfb、fluxbox、x11vnc 和 FastAPI 服务。

重启：

```bash
sudo ./restart.sh
```

查看日志：

```bash
journalctl -u ctyun-manager -f
```

## Codex 续接

后续换对话窗口时，先让 Codex 阅读：

- `PROJECT_CONTEXT.md`
- `README.md`
- `API_REFERENCE.md`

然后再继续开发。`PROJECT_CONTEXT.md` 记录了目标、文件结构、关键决策、已完成事项和待办。

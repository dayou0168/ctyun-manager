# 重装系统交接说明

更新时间：2026-07-10 Asia/Shanghai

本文件用于本机重装系统后继续开发 `ctyun-manager`，避免丢失当前项目进度和关键约定。

## GitHub 源码

- 仓库：`https://github.com/dayou0168/ctyun-manager.git`
- 分支：`main`
- 本次同步目标：把当前源码、部署脚本、L2TP 脚本、SSH 管理、RustDesk 定制、天翼云资源状态确认、充值后台 cookie 流程等最新进度提交到 GitHub。

重装系统后重新拉取：

```powershell
gh auth login
gh repo clone dayou0168/ctyun-manager
cd ctyun-manager
```

## 当前线上平台

- 访问地址：`http://43.119.30.80:8000/`
- 服务器目录：`/www/wwwroot/ctyun-manager`
- systemd 服务：`ctyun-manager`
- 启动文件：`/www/wwwroot/ctyun-manager/start.sh`
- 线上版本接口：`/api/version`
- 当前已验证版本：`2026.07.08.0002`

常用线上命令：

```bash
cd /www/wwwroot/ctyun-manager
sudo systemctl status ctyun-manager
sudo systemctl restart ctyun-manager
curl -fsS http://127.0.0.1:8000/api/version
```

## 不能依赖 GitHub 恢复的运行数据

这些文件不进仓库，重装本机不会影响线上服务器，但如果迁移线上平台必须单独备份：

- `.env`
- `master.key`
- `ctyun-manager.db*`
- `data/`
- Playwright/Chromium 用户状态
- 天翼云网页登录 cookie
- Linux SSH 服务器密码、私钥和指纹记录
- 运行日志、临时 zip、pid 文件

如果丢失 `master.key`，数据库里已加密的天翼云账号、爱快账号、SSH 密码/私钥将无法解密。

## 当前关键约束

- 天翼云模块不再接受 VNC/noVNC 方案。
- 能用官方 OpenAPI 的功能必须优先用 OpenAPI。
- OpenAPI 没覆盖时，优先用已保存 cookie 的后台 HTTP/API。
- 无法通过 OpenAPI 或 cookie 后台接口实现时，明确提示不可用或需要重新抓接口，不再用 VNC 兜底。
- 修 BUG 时先定位根因，再检查同类功能是否也存在同样问题；不能只修截图上的单点表现。
- 对代码、配置、脚本或文档做更新后，不要默认提交或推送；只有用户明确要求同步 GitHub 时才提交推送。

## 当前已完成重点

- 天翼云资源页支持云主机、EIP、VPC/子网/虚拟 IP/安全组、镜像等 OpenAPI 同步和常用操作。
- 资源操作提交后不阻塞 UI，后台按官方 API 状态确认完成、失败或未确认。
- 右上角账号切换和左侧菜单切换改为先显示本地缓存，降低卡顿。
- 增加“同步当前页”，只同步当前菜单对应资源类型。
- 安全组规则管理改为入站/出站分区表格。
- 充值二维码改为后台 cookie/API 获取，二维码和支付状态不再依赖 VNC。
- 新账号保存后会后台自动刷新登录态。
- 余额刷新优先走官方网页登录后的余额接口。
- Linux SSH 管理已接入 xterm.js、SFTP 文件管理和 L2TP 快捷命令。
- L2TP 服务端配置拆成 `/etc/l2tp-vpn/server.conf` 和 `/etc/l2tp-vpn/users.conf`。
- `install-l2tp-server.sh` 已更新并上传到平台服务器；公网下载入口为 `/install-l2tp-server.sh`。
- L2TP 最新脚本 SHA256：`c79c5281f65a2deced33a8476a463cb0174d4da1a62d365bdcee8499a371fba8`。
- RustDesk 定制工具支持官方 tag 校验、classic PAT、平台选择、图标上传、GitHub Actions 生成和 Node 24 action 版本修正。
- 项目部署脚本支持 Linux 直装和 Docker Compose 一键部署。

## 重装后继续开发建议

1. 先阅读 `README.md`、`PROJECT_CONTEXT.md`、`API_REFERENCE.md` 和本文件。
2. 执行 `git status`，确认工作区干净。
3. 本地运行前先创建 `.env`，不要把线上 `.env` 或 `master.key` 提交到 GitHub。
4. 修改代码后先做本地语法检查：

```powershell
python -m py_compile app\main.py
node --check app\static\app.js
```

5. 需要更新线上时，先备份线上旧文件，再覆盖变更并重启 `ctyun-manager`。
6. 更新线上后用 `/api/version` 和实际页面行为确认，而不是只看代码。

## 当前优先待办

- 继续实测 L2TP 新脚本在多 VIP、多账号、不同共享数下的连接和 SNAT 表现。
- 继续清理历史 VNC/noVNC 入口，避免误用。
- 继续观察天翼云资源操作后的状态确认速度，遇到问题按“根因 + 同类功能排查”处理。
- 爱快网关模块还需要更多真实 3.7+ 网关版本继续补齐映射。

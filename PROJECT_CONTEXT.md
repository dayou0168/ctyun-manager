# Codex 项目上下文

更新时间：2026-07-10 Asia/Shanghai

## 项目目标

本项目是一个天翼云多账号管理平台。核心目标是把天翼云账号资源、余额充值、官方后台 cookie/API 能力，以及爱快网关 Web 管理统一到一个平台里，避免人工反复登录不同后台。

后续工作将在本项目目录继续：

```text
C:\Users\Administrator\Documents\Codex\2026-06-08\ip\outputs\ctyun-manager
```

重装系统后的续接说明已经固化在 `REINSTALL_HANDOFF.md`。新环境先从 GitHub clone 本仓库，再阅读 `README.md`、`PROJECT_CONTEXT.md`、`API_REFERENCE.md` 和 `REINSTALL_HANDOFF.md`。

## 协作规则

- 以后 Codex 对代码、配置、脚本或项目文档做有效更新后，不要默认提交或推送到 GitHub。
- 只有用户明确说“同步 GitHub / 提交 / 推送 / 发 PR”时，才执行 git add、git commit、git push 等同步动作。
- 默认远程仓库：`https://github.com/dayou0168/ctyun-manager`
- 默认分支：`main`
- 如果用户明确要求同步，先检查 `git status`，确认没有误提交数据库、密钥、日志、缓存等运行态文件。
- 除非用户明确要求，不要提交 `.env`、`master.key`、`ctyun-manager.db*`、`data/`、`.playwright/`、`.venv/`、日志文件。
- 天翼云模块后续不再接受 VNC/noVNC 方案。新功能、修复和兜底路径必须优先使用官方 OpenAPI；OpenAPI 不覆盖时，优先使用已保存 cookie 的后台 HTTP/API 调用；仍无法实现时明确提示该能力不可用或需要重新抓取接口，不允许再把 VNC/远程桌面作为业务方案。

## 已完成事项

- 天翼云多账号管理、资源同步、资源操作、余额缓存。
- 资源操作成功后会立即生成“官方状态确认任务”，按相关资源类型和区域做后台同步，减少创建、删除、开关机后列表长时间不更新的问题。
- 资源操作接口不会再等待“操作后同步”完成；后端把同步放入后台队列，前端用处理中状态和顶部“后台确认中：完成/失败/处理中”反馈业务结果，减少按钮点击后的卡顿。
- 资源操作状态显示已改为沿着官方 API 链路确认：动作接口返回只表示“已受理”，平台随后按相关列表/详情接口的官方状态字段、绑定字段、规则列表或资源是否出现/消失来更新当前状态；官方状态变化时页面立刻跟着变，到达终态时立即显示“完成/失败/未确认”，不再向用户展示固定 `x/y` 轮次。
- 连续操作不需要等待上一个任务完成；每次点击都会产生独立确认项，批量操作会按每个资源单独确认并汇总完成、失败、处理中数量。
- 自动刷新改为先重绘本地缓存，按节流规则后台同步当前视图；手动“同步资源”仍然执行真实全量同步。
- 顶部新增“同步当前页”：在云主机、弹性 IP、VPC 网络、镜像等页面只同步当前菜单需要的资源类型；如果右上角选中了某个账号，则只同步该账号，避免为了局部刷新触发全账号全类型同步。
- 自动后台同步只在右上角选中单个账号时运行；“全部账号”视图只读取本地 DB，不自动扫所有账号 OpenAPI，避免用户停在资源页时 uvicorn 持续高 CPU。需要全账号刷新时使用“同步当前页”或“同步资源”手动触发。
- 云主机、弹性 IP、VPC/子网/虚拟 IP/安全组、镜像页统一修正资源快照策略：普通切换菜单可用本地快照提速；一旦发生创建、删除、绑定、解绑、修改或同步返回，必须清掉旧资源快照并读取后端最新 DB，避免官方资源已变化但平台仍显示旧列表。
- 云主机、EIP、VPC 网络相关操作统一优化了乐观状态解除规则：官方同步回来的状态不再处于处理中，或绑定字段已经变化时，平台会解除“解绑中/绑定中/更新中”等临时状态。
- EIP 列表同步时会根据绑定证据归一化为 `bound/unbound`，避免官方泛状态导致平台长期显示处理中。
- EIP 同步会保留官方 `status` 到 `official_status`，`DELETING`、`UPDATING`、`BANDING_OR_UNBANGDING` 等官方阶段不会再被绑定归一化覆盖；绑定证据只作为 `binding_status` 和终态判断辅助。
- 顶部已移除“正式 OpenAPI”徽标，只保留刷新/后台进度提示。
- 安全组规则添加、编辑、删除入口已改成规则管理器：入站规则和出站规则分区显示完整列表，每条规则右侧直接编辑/删除；编辑仍采用“先创建新规则，再删除旧规则”，删除旧规则失败时会尝试回滚新规则，避免规则被清空。
- 子网创建/编辑会同时提交 `dnsList` 和 `dnsServers`，提高不同天翼云接口版本下 DNS 生效概率。
- 天翼云官方网页登录集成，包含 TOTP/MFA 自动登录，主要用于保存 cookie、充值、余额和官方后台接口调用。
- 历史官方控制台/VNC/noVNC 入口属于待回收遗留能力，不能再作为新功能或问题修复的实现路径。
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
  - 未明确适配写入接口的页面只保留查看，不再显示容易失败的通用编辑按钮。
- Linux 服务器 SSH 管理：
  - 左侧 `服务器 / SSH 管理`。
  - 后端接口：`/api/linux/servers`、`/api/linux/servers/{id}/test`、`/api/linux/servers/{id}/command`。
  - WebSocket SSH 会话：`/api/linux/servers/{id}/ssh`。
  - 支持多台服务器资料加密保存，密码和私钥使用现有 `master.key` 加密。
  - 支持连接测试、平台内 xterm.js SSH 会话、鼠标选择/复制/粘贴、键盘直连输入、文件管理和 L2TP 快捷命令。
  - SSH 主机指纹会在首次成功连接时记录；后续连接如发现指纹变化会阻断操作并提示，降低误连或中间人风险。
  - 远端 L2TP 临时脚本按 `700` 上传，远端配置临时文件按 `600` 上传。
  - L2TP “扫描内网IP”会先定位当前 SSH 服务器对应的 ECS，再取该 ECS 当前网卡所在子网 ID/CIDR；后端通过后台任务把临时扫描脚本上传到远端 `/tmp`，接口会立即返回一条短命令写入 SSH。命令会等待远端脚本出现后执行并删除，避免按钮点击后长时间没有命令显示。脚本只 ping 当前子网内资源映射到的公网 IP/EIP，并打印简化后的“本机 IPv4 / 路由表 / 内网 IP -> 公网 IP 在线结果”，不打印平台候选详情、排除统计或 L2TP 配置，不扫描其他账号、其他资源池或同资源池其他子网。
- L2TP 安装脚本会把服务器参数写入 `/etc/l2tp-vpn/server.conf`，和账号文件 `/etc/l2tp-vpn/users.conf` 放在同一目录；端口、MTU、MRU、DNS、IPsec、PSK、网卡、VIP 可在 `server.conf` 中修改，并通过 `/usr/local/sbin/l2tp-vpn-apply-config.sh` 重新应用。
- 平台 L2TP `保存并应用` 会把顶部端口、MTU、MRU、PSK/随机 PSK 输入作为服务端配置覆盖项，和下方账号文本一起写入远端配置并调用 `/usr/local/sbin/l2tp-vpn-apply-config.sh`；`仅应用` 只应用服务器上已经存在的配置文件。
  - `server.conf` 会记录当前默认网卡 `VPN_IFACE` 和这张网卡已有 IPv4 `VPN_IFACE_IPV4S`，例如主网卡 `192.168.0.101/24`；主网卡 IP 已经在系统中，不写入 `VPN_VIPS`。
  - 平台“安装/更新脚本”会先执行当前服务器子网扫描逻辑；扫描成功后只把结果中的 `resource_type=vip` 虚拟内网 IP 写入 `VPN_VIPS`，安装脚本会把这些额外 VIP 加到服务器网卡并持久化到 helper。
  - L2TP 安装脚本在有终端输入时默认进入交互式确认，会停下来让用户输入/确认服务端端口、MTU、MRU、VPN 客户端内网网段、服务端隧道 IP、客户端地址池、DNS、额外虚拟内网 IP、是否启用 IPsec/PSK；可用 `VPN_INTERACTIVE=0` 关闭交互。
- RustDesk 定制工具：
  - 左侧 `应用工具 / RustDesk 定制`。
  - 后端接口：`/api/tools/rustdesk/jobs`。
  - 使用 classic PAT 临时访问用户公开 GitHub 仓库，token 不保存数据库，不写日志。
  - GitHub API 令牌使用 classic token，表单提供创建令牌跳转链接；token 至少需要 `workflow`，建议 `public_repo + workflow`。如果 GitHub UI 选择 `workflow` 时自动勾选 `repo`，平台允许继续，但仍强制目标仓库必须公开。
  - 校验官方 RustDesk tag，例如 `1.4.7`。
  - 拉取官方源码、递归子模块、本地化子模块、应用源码补丁、推送到目标仓库。
  - 采用 1.4.7 成功方案：服务器信息写源码常量，不使用 `res/local_custom_client.json`。
  - 表单可选择编译客户端，生成后的 `Flutter Tag Build` 手动运行页会带 Windows/Android/macOS/iOS/Linux/AppImage/Flatpak 勾选项。
  - 表单可上传 PNG Logo，后端使用 Pillow 生成常见客户端图标资源。
  - RustDesk 定制任务会写入 `rustdesk_jobs` 表；平台重启后保留最近任务记录，运行中被中断的任务会标记为失败，PAT 不入库。
- 部署安全默认值：
  - `install.sh`、`install-docker.sh`、`install-compose.sh` 会自动生成后台登录密码和会话密钥。
  - `docker-compose.deploy.yml` 要求 `.env` 显式提供后台密码和会话密钥，README 已给出生成命令。
- 本地版本：
  - 当前构建：`2026.07.08.0002`
  - 当前线上平台：`http://43.119.30.80:8000/`
  - 当前线上目录：`/www/wwwroot/ctyun-manager`
  - 当前 systemd 服务：`ctyun-manager`
  - L2TP 最新脚本下载入口：`/install-l2tp-server.sh`
  - L2TP 最新脚本 SHA256：`c79c5281f65a2deced33a8476a463cb0174d4da1a62d365bdcee8499a371fba8`
  - VPC/子网/虚拟 IP/安全组/ECS/EIP 表格行已带稳定 `data-resource-key`，后台确认失败、未确认、删除完成优先做行级状态更新或移除，不再为了单个资源操作重绘整页。
  - 虚拟 IP 行会根据已同步绑定字段显示“绑定/解绑云主机”和“绑定/解绑弹性IP”；解绑优先复用已识别的云主机网卡 ID / floatingID，识别不到时再弹窗让用户选择。
  - 操作未确认状态已改成黄色“待官方确认/删除待确认”，失败状态为红色；状态悬停显示具体 API 错误或“官方列表仍存在”等原因。
  - 切换左侧菜单和右上角账号筛选已改成先显示本地缓存/骨架，再后台刷新；确认弹窗提交后立即释放 UI，允许连续删除、解绑、绑定多个资源。
  - 后端资源同步仍并发拉取 OpenAPI，但 SQLite 写入阶段加专门写锁；按资源池快速同步时只替换目标资源池数据，避免一个资源池操作影响同账号其它资源池列表。
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
    browser_automation.py         天翼云网页登录、cookie 保活、充值、余额读取和后台 HTTP/API 调用
    ikuai_client.py               爱快 Web API 封装和菜单映射
    rustdesk_customizer.py        RustDesk 定制源码生成和 GitHub 写入
    ssh_manager.py                Linux SSH 连接、测试、命令执行封装
  static/
    index.html                    主页面
    app.js                        主前端逻辑
    styles.css                    主样式
    qr.html / qr.js               充值二维码页
    vnc.html / vnc.js             历史 noVNC 页面，业务功能禁止继续依赖
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
- 天翼云资源列表和云主机开关机状态统一通过 AK/SK OpenAPI 同步，不依赖 VNC 或网页登录页面。
- 天翼云模块禁止新增 VNC/noVNC 依赖；已存在的 VNC 代码只能作为待清理历史代码看待，不能用于“兜底”。远程登录、续订、充值、余额、资源状态等能力必须走 OpenAPI 或 cookie 后台 HTTP/API。
- 资源操作提交后，前端会立即调用 `/api/accounts/{id}/sync-types` 按相关资源类型刷新，并把操作发生的 `regionID` 传给后端，避免快速同步只扫旧资源池而漏掉新创建资源。
- 创建类资源、删除类资源和云主机网络变更都有后续确认；确认逻辑不是固定步骤数，而是读取官方查询接口状态字段和绑定字段。官方列表显示资源消失、状态到运行/关机、绑定字段出现/消失、规则列表变化等即判定完成；官方状态返回失败即判定失败；超过内部最大等待仍未到终态才显示未确认完成。
- 创建/编辑子网时 DNS 以逗号字符串提交给天翼云 OpenAPI，避免数组格式被接口接受但不生效。
- 安全组规则支持添加、编辑、删除；规则 ID 兼容 `id`、`securityGroupRuleID`、`ruleID` 等返回字段。前端规则弹窗按“入站规则 / 出站规则”分区展示完整规则表，不再通过单个下拉框选择规则；编辑规则采用“创建新规则，再删除旧规则”的方式。
- 包内和 GitHub 仓库禁止包含数据库、密钥、浏览器状态、日志；现在也包含 Linux 服务器 SSH 密码和私钥，必须只保存在本地加密数据库里。
- 用户想要的 Linux 一键脚本是直接 curl GitHub 上的 `.sh`，然后自动下载项目并完成安装；主入口为 `install-linux.sh`。
- GitHub 仓库当前是 Private，免 token 的 `raw.githubusercontent.com` curl 命令需要仓库改 Public；Private 状态下需要用 `GITHUB_TOKEN` 通过 GitHub API 读取脚本和源码包。
- 直装方式使用 systemd 运行 `start.sh`。历史脚本中仍可能保留 Xvfb/fluxbox/x11vnc 相关启动逻辑，但业务能力不再允许依赖 VNC；后续应逐步移除这类遗留依赖。
- `docker-compose.deploy.yml` 直接拉取 `ghcr.io/dayou0168/ctyun-manager:latest`，不需要服务器有完整源码。
- GHCR 镜像通过 `.github/workflows/docker-image.yml` 在推送 `main` 后自动构建发布。
- 2026-06-19 首次触发 GHCR workflow 失败，GitHub 返回：账号 recent account payments failed 或 spending limit 需要调整。修复 GitHub Billing 后重新运行 workflow，直接 yaml 部署才有可拉取镜像。
- 发布版 Docker Compose 使用命名卷 `ctyun-manager-data` 持久化 `/app/data`，迁移时必须备份该 volume 内的 `master.key` 和 SQLite 数据库。
- 本地源码 Docker Compose 仍保留 `docker-compose.yml`，它使用 `build:` 从当前源码构建镜像，并把本地 `./data` 挂载到容器 `/app/data`。
- RustDesk 定制不使用数据库保存 token。任务状态只保存在进程内存，服务重启后历史任务会丢失。若用户只能创建带 `repo` 权限的 classic token，建议任务完成后立即删除该 token。
- RustDesk GitHub 写入使用临时 authenticated HTTPS remote，不依赖 `GIT_ASKPASS`，避免服务器 `/tmp` 权限或 noexec 导致 `git push` 无法读取用户名。
- RustDesk 写入目标仓库时使用 `git add -A --force`。原因是官方 `.gitignore` 会忽略 `*png`、`*svg`、`*jpg` 等资源，但这些文件本身是源码编译必需资源；普通 `git add -A` 会导致 Android、macOS、Linux 图标和 Windows portable 打包缺文件。
- RustDesk 目标仓库分支字段已从 UI 移除，后端固定写入目标仓库默认分支，避免和官方 RustDesk 版本/分支概念混淆。
- RustDesk Actions 平台选择写入：
  - `flutter-tag.yml` 增加 `workflow_dispatch.inputs`。
  - `flutter-tag.yml` 会把手动运行时的勾选值继续传给 `flutter-build.yml`。
  - `flutter-tag.yml` 手动运行页会生成 `release_tag`，默认格式为 `rustdesk-版本-年月日-时分`，避免 Releases 列表显示成 `main`。
  - `flutter-build.yml` 增加 `workflow_call.inputs` 并给主要构建 job 加 `if` 条件。
  - workflow 会补 `permissions: contents: write`，避免编译成功后发布 Release 时因 `GITHUB_TOKEN` 只有 read 权限报 403。
  - 对已有半成品 workflow 重复生成时，平台会替换已有 job 级 `if` 并清理重复 `if`，避免 GitHub 报 `'if' is already defined`。
  - workflow 会把 `actions/checkout`、`actions/cache`、`actions/github-script`、`actions/upload-artifact`、`actions/download-artifact`、`microsoft/setup-msbuild`、`softprops/action-gh-release` 升级到 Node 24 兼容版本；`rustdesk-org/run-on-arch-action` 必须保留官方 RustDesk fork，因为 Linux Sciter/Flatpak 构建依赖 fork 内置的专用 Dockerfile。
  - AppImage 打包前会固定 Python 打包工具版本，避免 GitHub runner 拉到不兼容的 `setuptools_scm` 新版本导致 `appimage-builder` metadata 生成失败。
  - 表单默认全选，用户可取消不需要的客户端。
- RustDesk 1.4.7 方案：
  - `libs/hbb_common/src/config.rs` 写 `RENDEZVOUS_SERVERS` 和 `RS_PUB_KEY`。
  - `src/common.rs` 在 `load_custom_client()` 中写入 `config::HARD_SETTINGS`。
  - 删除 `res/local_custom_client.json` 和 `.github/scripts/apply-bundled-server-settings.py`。
  - workflows 删除 server secrets 相关行，并把 checkout submodules 设为 false。
- SSH 管理 / L2TP：
  - Linux 页面按 FinalShell 类布局：上方 SSH 终端，下方 `文件/命令` 页签；文件区左目录树、右文件表格。
  - 目录树和文件表支持首字母/短前缀键盘导航，连续按同一个字母会从当前选中项继续向下循环。
  - 文件表单击选中后，下一次首字母导航从当前选中行向下查找，不再从上一次键盘匹配项或列表顶部重新开始。
  - 文件编辑改为弹窗编辑，不再占用主页面高度。
  - 文件管理通过 SFTP 读取/保存，不再向 SSH 终端输出 `[平台操作]` 辅助日志。
  - 云主机列表 `加入 SSH` 支持从当前资源行或按钮数据自动带入公网/私网 IP。
  - L2TP 的“安装/更新脚本、读取配置、扫描内网IP、仅应用、保存并应用”按钮只作为 SSH 快捷命令：点击后把命令写入 SSH 会话，不自动回车、不后台执行，用户在 SSH 窗口确认后手动回车。
  - L2TP 服务器配置文件为 `/etc/l2tp-vpn/server.conf`，账号配置文件为 `/etc/l2tp-vpn/users.conf`；`读取配置` 会同时打印两个文件，`仅应用` 优先调用 `/usr/local/sbin/l2tp-vpn-apply-config.sh`，旧安装则回退到 `/usr/local/sbin/l2tp-vpn-apply-users.sh`。
  - `users.conf` 首次生成模板和平台默认模板已改为中文说明，字段顺序仍为：账号、密码、出口虚拟内网IP、共享连接数、客户端内网IP或IP段、公网IP备注。
  - 多个出口账号只需要填写第 3 列出口虚拟内网 IP，例如 `DF31,...,192.168.0.101,254`、`DF32,...,192.168.0.102,20`；第 5 列客户端内网 IP 或 IP 段默认建议留空。留空时由 `xl2tpd` 从全局 `VPN_CLIENT_POOL` 分配客户端 IP，账号的出口由第 3 列和连接成功后的 per-session SNAT 决定；只有客户端能主动指定固定地址，或明确要限制某个账号可用地址时，才填写第 5 列。原因是 `xl2tpd` 在知道账号名前先分配远端 IP，`chap-secrets` 只能授权地址，不能默认按用户名自动挑选不同网段。持久 SNAT 规则会带 `ctyun-l2tp-managed-snat` 标记，每次 helper 启动都会先清掉旧标记规则再按当前配置重建；若系统不支持 `iptables -m iprange`，helper 只打印 warning，不再让 `l2tp-vip-egress-setup.service` 失败。
  - “扫描内网IP”平台候选来源包括已绑定当前 SSH 服务器对应云主机的 `resource_type=vip` 虚拟 IP，以及匹配当前 SSH 服务器的 ECS payload 中 `privateIP/private_ip/fixedIpList/networkCardList` 等私网 IP 字段；未匹配当前服务器的同资源池 VIP 只统计排除数量，不进入可插入候选。
  - “安装/更新脚本”会先调用 `/api/linux/servers/{id}/l2tp/vips` 走当前服务器子网扫描逻辑，扫描失败则取消生成安装命令；扫描成功后才根据扫描结果生成 `VPN_VIPS`。
  - L2TP “安装/更新脚本”快捷命令会先扫描当前服务器子网内的虚拟内网 IP；扫描成功后生成下载命令，并把当前页面端口、MTU、MRU、PSK/随机 PSK 作为安装脚本默认值，同时带上扫描出来的 `VPN_VIPS`。远端脚本在交互模式下仍会停下来让用户确认关键参数。
  - 公网在线探测只 ping 当前 ECS 所在子网内资源映射公网 IP/EIP，输出对应内网 IP；同一个公网 IP 对应多个内网 IP 时只 ping 一次，不扫描其他账号、其他资源池或同资源池其他子网。
  - 平台提供 `/install-l2tp-server.sh` 只读下载地址，供 SSH 快捷命令中的 `curl` 使用。

## 当前待办

- 重装系统后优先确认 GitHub clone 的代码和线上 `/api/version` 一致，当前线上已验证为 `2026.07.08.0002`。
- 继续实测 L2TP 新脚本在多 VIP、多账号、不同共享数下的连接、客户端地址分配和 SNAT 表现。
- 继续清理历史 VNC/noVNC 入口，避免未来误用。
- 继续观察天翼云资源操作后的状态确认速度；遇到问题按“根因 + 同类功能排查”处理。
- 爱快模块仍需后续用真实不同版本网关继续补齐数据映射。

## 续接建议

新对话开始时，可以这样提示 Codex：

```text
请先阅读 README.md、PROJECT_CONTEXT.md、API_REFERENCE.md，然后继续这个天翼云/爱快管理平台项目。
当前重点是：按 REINSTALL_HANDOFF.md 恢复项目上下文，确认线上版本，继续验证 L2TP 新脚本、多账号资源状态确认、爱快真实网关映射，以及部署脚本实机安装效果。
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

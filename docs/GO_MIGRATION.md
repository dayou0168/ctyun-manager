# Go 迁移设计

## 目标

将管理平台的主服务从 Python/FastAPI 渐进迁移到 Go，同时保持现有网页、API 路径、SQLite 数据和部署可用。需要浏览器的天翼云登录、Cookie、充值与二维码流程最终迁移到独立的 Node.js + Playwright 任务进程，服务器不再依赖 Python。

## 当前规模

- Python 代码约 13,735 行。
- `app/main.py` 约 5,229 行，共 69 个 HTTP/WebSocket 路由。
- `app/services/browser_automation.py` 约 4,253 行。
- 前端 `app/static/app.js` 约 8,912 行，本阶段保持不变。
- 当前数据存储为 SQLite，敏感字段使用 `master.key` 对应的 Fernet 密文。

## 不可破坏的兼容边界

1. 迁移期间不得修改或删除现有 SQLite 数据库、`master.key` 和密文字段。
2. 前端依赖的 API 路径、JSON 字段、Cookie 名称和错误状态码必须由契约测试固定。
3. Go 代码在具备完整 Fernet 兼容读取和备份验证前，不得写入任何敏感字段。
4. 新旧服务必须能使用不同端口并行运行；正式切换通过反向代理逐路由完成。
5. 后台任务必须迁移为持久化任务，支持租约、幂等、超时、重试和重启恢复。
6. 浏览器充值链路在新实现完成真实账号回归前继续由旧服务处理。

## 目标结构

```text
cmd/ctyun-manager-go       Go 主程序
internal/httpserver        HTTP 路由、中间件和兼容层
internal/config            环境变量及启动配置
internal/storage           SQLite/PostgreSQL 与迁移
internal/security          会话、密码和版本化密文
internal/jobs              持久化任务队列
internal/ctyun             天翼云 OpenAPI 与资源操作
internal/ikuai             爱快接口
internal/ssh               SSH、SFTP 和 WebSocket 终端
internal/l2tp              L2TP 配置和脚本编排
internal/rustdesk          RustDesk 工作流
browser-worker/            Node.js + Playwright 浏览器任务
```

当前已落地 Go 主服务、天翼云 OpenAPI、Linux SSH/L2TP、控制台桥接以及 Node.js 浏览器 worker 的主要能力。Go 候选服务默认监听 `127.0.0.1:18000`，不会占用线上 Python 的 `8000` 端口。充值下单/二维码已完成隔离模拟测试，但真实小额支付和临时云资源写回归仍是正式切换门禁。

## 分阶段迁移

### 阶段 1：骨架和契约

- Go 配置、结构化日志、优雅退出。
- `/healthz`、`/readyz`、`/api/version`。
- 复用现有静态网页和 L2TP 脚本下载路径。
- API、数据库和加密兼容清单。

### 阶段 2：认证和只读数据

- 兼容现有 PBKDF2 密码校验及签名 Cookie。
- 以只读方式打开 SQLite，迁移账号、资源、操作记录查询接口。
- 实现 Fernet 兼容解密测试，但仍禁止 Go 写入敏感数据。

### 阶段 3：OpenAPI 和持久化任务

- 天翼云签名客户端、资源同步和资源操作。
- 将内存队列迁移为 `jobs` 表和 Go worker。
- 操作状态确认、失败恢复和审计日志。

### 阶段 4：设备与运维

- 爱快客户端。
- Go SSH/SFTP/WebSocket 终端。
- L2TP 安装、配置、扫描和诊断。
- CTyunOS V4 的 x86_64、aarch64/arm64 与 armv7/armv8 安装；仓库缺少 `xl2tpd` 时校验并编译固定的官方源码版本。
- ARM 版本只编译 L2TP 服务必需的 `xl2tpd` 和 `xl2tpd-control`，不再因可选 `pfc` 工具强制依赖 CTyunOS ARM 仓库通常缺少的 `libpcap-devel`；下载包在编译前必须通过固定 SHA-256 校验。
- 平台直连安装接口会透传服务端隧道地址、客户端地址池、VPN 网段、用户配置和批量 VIP 扫描参数；默认客户端池为 `172.18.0.2-172.18.255.254`。
- RustDesk GitHub 工作流。

### 阶段 5：浏览器任务

- Node.js + 官方 Playwright worker。
- 账号级上下文隔离、Cookie 状态和充值流程。
- Go 与 worker 之间使用仅监听本机的受认证 HTTP/RPC 协议。
- 官方充值 Cookie 接口的订单、三种支付渠道、二维码生成/刷新和支付状态查询；支付会话只保存在 worker 内存中，关闭会话即清除。

### 阶段 6：切换

- 新旧接口镜像比对。
- 反向代理逐模块切流。
- 数据备份及回滚演练。
- 全部验收通过后删除 Python 运行时。

## 当前运行方式

```bash
go test ./...
go run ./cmd/ctyun-manager-go
```

可选配置：

```text
CTYUN_MANAGER_GO_HOST=127.0.0.1
CTYUN_MANAGER_GO_PORT=18000
CTYUN_MANAGER_ROOT=.
CTYUN_MANAGER_STATIC_DIR=./app/static
CTYUN_MANAGER_DB=./data/ctyun-manager.db
CTYUN_MANAGER_MASTER_KEY_FILE=./data/master.key
CTYUN_MANAGER_MASTER_KEY=
CTYUN_MANAGER_SESSION_SECRET=change-this-session-secret
CTYUN_MANAGER_PUBLIC_URL=http://127.0.0.1:8000
CTYUN_MANAGER_L2TP_SCRIPT=./install-l2tp-server.sh
```

当前候选服务实现了以下兼容接口：

- `POST /api/login`
- `POST /api/logout`
- `GET /api/me`
- `GET /api/accounts`
- `GET /api/finance`
- `GET /api/dashboard/summary`
- `GET /api/resources/{resource_type}`
- `GET /api/operations`
- `GET /api/runtime/status`
- `POST /api/recharge/prewarm`
- `/api/accounts/{account_id}/recharge/*`
- `/api/accounts/{account_id}/console/*`
- `GET /ctyun-console-bridge.zip`

候选环境默认以 `mode=ro` 打开 SQLite；生产容器必须显式开启可写模式。Go 已兼容 Python 的 PBKDF2 密码、签名 Session Cookie 和 Fernet 密文，并已实现账号、同步、操作审计及 Linux 服务器数据写入。数据库缺失或无法读取时，进程仍可提供 `/healthz`，但 `/readyz` 返回 503，认证接口不会伪装成可用。

`/api/accounts/{account_id}/balance` 通过本机受认证的 Node.js worker 读取官方页面登录态和余额；`/regions`、`/options/{kind}`、同步、价格及资源动作由 Go OpenAPI 客户端提供。对现有生产云资源只允许只读查询；写动作只能在明确创建的临时测试资源上回归。

## 双服务响应对照

先让 Python 监听 `127.0.0.1:8000`、Go 监听 `127.0.0.1:18000`，再运行：

```bash
export CTYUN_COMPARE_USERNAME='admin'
export CTYUN_COMPARE_PASSWORD='实际管理密码'
go run ./cmd/api-compare \
  -python-url http://127.0.0.1:8000 \
  -go-url http://127.0.0.1:18000 \
  -report ./comparison-report.json
```

工具只允许登录以及预定义的只读 GET 路径，拒绝账号动作、删除、同步等写接口。密码只能通过环境变量传入，不提供命令行密码参数。退出码 `0` 表示全部相同，`1` 表示存在差异或端点错误，`2` 表示配置或登录失败。版本构建信息以及运行队列等天然易变字段在比较前会被忽略。

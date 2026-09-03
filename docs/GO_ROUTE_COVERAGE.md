# Go 路由迁移覆盖清单

只有本表所有“必须保留”的能力完成契约测试和真实回归后，Go 镜像才允许替换线上 Python 镜像。`已验证`表示已有实现并完成相应回归；`已实现`表示代码已落地但仍缺少完整真实写操作回归；`待迁移`表示尚不能用于纯 Go 生产切换。

## 基础与只读查询

| 路由 | 状态 |
| --- | --- |
| `GET /`、`GET /static/*`、`GET /install-l2tp-server.sh` | 已完成 |
| `GET /healthz`、`GET /readyz`、`GET /api/version` | 已完成 |
| `POST /api/login`、`POST /api/logout`、`GET /api/me` | 已完成 |
| `GET /api/accounts` | 已完成 |
| `GET /api/finance`、`GET /api/dashboard/summary` | 已完成 |
| `GET /api/resources/{resource_type}` | 已完成 |
| `GET /api/operations`、`GET /api/runtime/status` | 已完成 |
| `GET /ctyun-console-bridge.zip` | 已实现并通过 ZIP/鉴权契约测试 |

## 天翼云账号、同步和资源操作

| 路由组 | 状态 |
| --- | --- |
| 账号新增、修改、删除 | 已实现，待完整写回归 |
| 账号全量/指定类型同步 | 已验证（全部资源类型只读同步） |
| 余额实时查询、地域、选项和预热 | 已验证；动态选项仍需持续契约回归 |
| 资源价格、ECS 续订价格/提交/订单状态 | 查询已验证；提交仅实现，禁止在现有资源上测试 |
| ECS、EIP、VPC、子网、VIP、安全组、路由表、ACL 和镜像动作 | 已实现，待临时资源写回归 |
| ECS 远程登录、TOTP | 已验证 |

## 浏览器与充值

| 路由组 | 状态 |
| --- | --- |
| 充值预热、打开、关闭 | 已实现于 Node.js Playwright worker |
| 充值下单、支付 | 已实现并通过隔离模拟测试；禁止用真实账号自动创建订单 |
| 二维码读取、刷新和支付状态 | 已实现并通过隔离模拟测试；待人工小额全链路验收 |
| 控制台打开 | 已实现于 Node.js Playwright worker |
| 浏览器桥接状态和扩展下载 | 已实现；登录态严格过滤为 `ctyun.cn` 域 |
| `WS /websockify`、VNC health | 按项目约束移除，不作为纯 Go 新架构依赖 |

## Linux、爱快与 RustDesk

| 路由组 | 状态 |
| --- | --- |
| Linux 服务器 CRUD、测试、命令、SFTP 文件操作 | 已验证 |
| Linux L2TP 配置、安装、应用和 VIP 查询 | 已实现；CTyunOS V4 ARM64 源码回退、客户端池参数透传已加入自动测试，待 ARM 实机回归 |
| `WS /api/linux/servers/{server_id}/ssh` | 已实现，待交互终端回归 |
| 爱快网关 CRUD、菜单、状态、分区动作和原始调用 | 按当前产品决定暂缓 |
| RustDesk 任务创建、列表、详情和后台执行 | 按当前产品决定暂缓 |

## 生产切换门禁

- 所有保留路由具备 Go/Node 实现，或者有明确产品决定删除。
- SQLite 迁移在数据库和 `master.key` 完整备份后演练成功。
- 写接口具备幂等、审计、并发控制和失败恢复测试。
- Python/Go 对照工具在真实脱敏数据库上通过全部只读端点。
- 天翼云动作、充值和 SSH 分别完成真实环境回归；爱快与 RustDesk 已由产品决定暂缓。
- Docker 镜像中不包含 Python、pip、uvicorn 或 Python Playwright。
- 安装脚本完成升级前备份、失败自动回滚和健康检查。

# 天翼云接口核对记录

核对时间：2026-06-08。

## 官方源码

- Go SDK：<https://github.com/ctyun-it/ctyun-sdk-go>
- Terraform Provider：<https://github.com/ctyun-it/terraform-provider-ctyun>
- SDK 核心签名：`ctyun-sdk-core/request.go`

程序使用 EOP HMAC-SHA256 签名，请求时间为 UTC，签名头包含：

- `ctyun-eop-request-id`
- `Eop-date`
- `Eop-Authorization`

## 资源 OpenAPI

| 功能 | 方法与路径 |
| --- | --- |
| 资源池 | `GET /v4/region/list-regions` |
| ECS 列表 | `POST /v4/ecs/list-instances` |
| ECS 创建 | `POST /v4/ecs/create-instance` |
| ECS 开/关/重启 | `POST /v4/ecs/start-instance`、`stop-instance`、`reboot-instance` |
| ECS 释放/退订 | `POST /v4/ecs/destroy-instance`、`unsubscribe-instance` |
| EIP 列表/创建 | `POST /v4/eip/list`、`POST /v4/eip/create` |
| EIP 绑定/解绑/删除 | `POST /v4/eip/associate`、`disassociate`、`delete` |
| VPC 列表/创建/删除 | `GET /v4/vpc/new-list`、`POST /v4/vpc/create`、`delete` |
| 子网列表/创建/删除 | `GET /v4/vpc/new-list-subnet`、`POST /v4/vpc/create-subnet`、`delete-subnet` |
| VIP 列表/创建 | `POST /v4/vpc/havip/list`、`POST /v4/vpc/havip/create` |
| VIP 绑定/解绑/删除 | `POST /v4/vpc/havip/bind`、`unbind`、`delete` |
| 镜像列表 | `GET /v4/image/list` |
| 制作/复制/删除镜像 | `POST /v4/image/create`、`copy`、`delete` |
| 共享/取消共享 | `POST /v4/image/shared-image/create`、`delete` |
| 接收/拒绝共享 | `POST /v4/image/shared-image/accept`、`reject` |
| 查询共享列表 | `GET /v4/image/show-shared-list` |

平台资源列表来自 AK/SK OpenAPI 同步后的本地缓存表，不通过 VNC 或网页登录页面抓取。资源操作后前端会调用内部接口 `POST /api/accounts/{account_id}/sync-types` 刷新相关资源类型；请求体支持：

```json
{
  "types": ["ecs", "vpc", "subnet"],
  "region_ids": ["cn-foshan-3"]
}
```

`region_ids` 用于优先同步操作发生的资源池，避免新建资源落在新资源池时被快速同步漏掉。

## 资源操作状态链路

天翼云多数资源操作是异步业务。动作接口返回 `statusCode=800` 只代表请求已受理，不代表资源已经到达最终状态。平台状态必须以对应列表/详情接口的官方状态字段为准，不能把固定刷新次数当作业务步骤。

| 资源 | 动作接口 | 状态查询接口 | 关键状态字段 | 平台终态判断 |
| --- | --- | --- | --- | --- |
| ECS | `/v4/ecs/start-instance`、`stop-instance`、`reboot-instance`、`destroy-instance`、`unsubscribe-instance` | `/v4/ecs/list-instances` | `instanceStatus` / `status` | 开机到 `running/active`；关机到 `stopped/shutoff`；释放/退订后列表中该实例消失。 |
| EIP | `/v4/eip/associate`、`disassociate`、`delete` | `/v4/eip/list` | `status`、`associationID`、`associationType` | 绑定后出现绑定字段；解绑后绑定字段为空；释放/退订后列表中该 EIP 消失。官方 EIP 状态包括 `ACTIVE`、`DOWN`、`ERROR`、`UPDATING`、`BANDING_OR_UNBANGDING`、`DELETING`、`DELETED`、`EXPIRED`。 |
| VPC | `/v4/vpc/create`、`update`、`delete` | `/v4/vpc/new-list` | `status` / `state` | 创建后列表出现；删除后列表消失；更新后官方状态不再是处理中。 |
| 子网 | `/v4/vpc/create-subnet`、`update-subnet`、`delete-subnet` | `/v4/vpc/new-list-subnet` | `status` / `state` | 创建后列表出现；删除后列表消失；更新后官方状态不再是处理中。 |
| 虚拟 IP | `/v4/vpc/havip/create`、`bind`、`unbind`、`delete` | `/v4/vpc/havip/list` | `status`、绑定实例/EIP 字段 | 绑定后出现对应绑定字段；解绑后绑定字段消失；删除后列表消失。 |
| 安全组 | `/v4/vpc/create-security-group`、`modify-security-group-attribute`、`delete-security-group`、`create-security-group-ingress/egress`、`revoke-security-group-ingress/egress` | `/v4/vpc/new-query-security-groups` | 安全组列表和规则列表 | 创建后列表出现；删除后列表消失；规则新增/删除后规则列表变化。 |
| 镜像 | `/v4/image/create`、`copy`、`delete`、共享相关接口 | `/v4/image/list`、`/v4/image/show-shared-list` | `imageStatus` / `status` | 制作/复制后列表出现并脱离处理中；删除后列表消失；共享/取消共享后共享目标列表变化。 |

前端资源状态展示按资源行更新：API 提交失败会在对应行显示失败原因；删除/释放类操作确认成功时只移除对应行，确认超时则显示“删除未确认/官方列表仍存在”，不重绘整页未操作区域。

平台前端按上表进行业务确认：每次动作提交后立即生成独立确认项并同步相关资源类型和资源池；如果同步到新的官方中间状态，页面直接显示官方状态；如果已经到达终态，立即停止后续确认并显示“完成”；如果官方状态返回失败，显示“失败”；超过内部最大等待时间仍未到终态，显示“未确认完成”，用户可继续点同步资源查看最新官方状态。创建类动作会记录提交前已有资源 ID，并结合名称、CIDR、子网、目标 IP 或接口返回 ID 匹配新资源，避免连续创建时把旧资源误判为新资源；批量和连续操作按每个资源独立确认，顶部只做完成/失败/处理中汇总。

镜像查询参数使用 `imageVisibilityCode`：

- `0` 私有镜像
- `1` 公共镜像
- `2` 共享镜像
- `3` 安全镜像
- `4` 社区镜像
- `5` 应用镜像
- `6` 市场镜像

共享镜像时 `destinationAccountID` 必须是接收方天翼云账号 ID。接收共享时使用接收方账号自己的 AK/SK，只提交 `imageID` 和 `regionID`。

VIP 绑定：

- 云主机：`resourceType=VM`，需要 `instanceID` 和 `networkInterfaceID`
- 弹性 IP：`resourceType=NETWORK`，需要 `floatingID`

## 官方费用页面登录态接口

以下路径来自 2026-06-08 当前天翼云官方费用前端资源
`/console/__micro__expense/js/fund.056b902f.js`，属于官方网页内部登录态接口，不是公开 AK/SK OpenAPI：

| 功能 | 方法与路径 | 字段 |
| --- | --- | --- |
| 余额和账号 ID | `GET /gw/account/giftcard/QueryBookSumm` | `cashPoints`、`accountId` |
| 欠费金额 | `GET /v1/bcc/bill/QueryOwe` | `realOwe` |
| 当前充值账号信息 | `GET /v2/bcc/basicData/getCurrentInfo` | 分销商状态等 |
| 官方充值页 | `/console/expense/fund/recharge` | 官方页面 |
| 官方收银台入口 | `GET /gw/account/cash/Recharge` | `amount` 为分，`frontUrl=/virtual/redirect/funddetail`，`platform=1`，返回 `nextUrl` |

用户在平台确认充值金额后，程序会优先复用官方登录态直调 `Recharge` 创建收银台流程，以减少页面点击等待；如果官方接口返回结构变化或拒绝请求，会自动回退到内嵌官方充值页面点击流程。

## Linux 服务器 SSH 管理接口

平台内部接口：

| 功能 | 方法与路径 |
| --- | --- |
| 服务器列表 | `GET /api/linux/servers` |
| 添加服务器 | `POST /api/linux/servers` |
| 更新服务器 | `PUT /api/linux/servers/{server_id}` |
| 删除服务器 | `DELETE /api/linux/servers/{server_id}` |
| 测试 SSH 连接 | `POST /api/linux/servers/{server_id}/test` |
| 执行单次命令 | `POST /api/linux/servers/{server_id}/command` |
| 平台内 SSH 会话 | `WS /api/linux/servers/{server_id}/ssh` |

关键规则：

- 服务器登录账号、密码、私钥和私钥口令使用现有 `master.key` 加密保存。
- 列表接口只返回脱敏账号、认证方式、最近连接状态和主机指纹摘要。
- 单次命令接口限制超时时间为 3-300 秒，输出超过平台限制会被截断。
- WebSocket SSH 会话使用当前平台登录 Cookie 鉴权，前端使用本地打包的 xterm.js，支持键盘直连输入、鼠标选择、复制、粘贴和终端尺寸同步；离开 SSH 管理页时前端会主动断开连接。

## RustDesk 定制工具接口

平台内部接口：

| 功能 | 方法与路径 |
| --- | --- |
| 创建定制任务 | `POST /api/tools/rustdesk/jobs` |
| 查询最近任务 | `GET /api/tools/rustdesk/jobs` |
| 查询任务详情 | `GET /api/tools/rustdesk/jobs/{job_id}` |

关键规则：

- 目标仓库必须是 GitHub 公开仓库。
- GitHub API 令牌使用 classic token，至少需要 `workflow`；公开仓库写入建议 `public_repo + workflow`。如果 GitHub UI 选择 `workflow` 时自动勾选 `repo`，平台允许继续，但仍要求目标仓库必须是公开仓库。
- token 只在后台线程内临时使用，不写数据库，不写日志。
- RustDesk 版本校验官方 tag：

```bash
git ls-remote --tags https://github.com/rustdesk/rustdesk.git refs/tags/1.4.7
```

源码写入策略：

- `libs/hbb_common/src/config.rs` 写入 `RENDEZVOUS_SERVERS` 和 `RS_PUB_KEY`。
- `src/common.rs` 写入 `config::HARD_SETTINGS`，包括 API fallback、默认密码、verification-method、allow-remote-config-modification、allow-hide-cm。
- 删除 `res/local_custom_client.json`。
- 删除 `.github/scripts/apply-bundled-server-settings.py`。
- workflow 中移除 `RENDEZVOUS_SERVER`、`RELAY_SERVER`、`API_SERVER`、`RS_PUB_KEY` secrets 及 bundled server settings 步骤。
- `build_targets` 控制生成后的 Actions 默认勾选项，支持 `windows_x64`、`windows_x86`、`android`、`macos`、`ios`、`linux`、`linux_sciter`、`appimage`、`flatpak`。
- Actions workflow 会自动补 Release 发布权限，并把手动运行页的勾选值传入实际编译 workflow；手动运行页会生成 `release_tag`，默认格式为 `rustdesk-版本-日期时间`；重复生成时会清理重复 job 级 `if`。
- `icon_data_url` 支持 PNG data URL，平台会生成常见客户端图标资源；公开任务状态不会回显图片 base64。

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

## RustDesk 定制工具接口

平台内部接口：

| 功能 | 方法与路径 |
| --- | --- |
| 创建定制任务 | `POST /api/tools/rustdesk/jobs` |
| 查询最近任务 | `GET /api/tools/rustdesk/jobs` |
| 查询任务详情 | `GET /api/tools/rustdesk/jobs/{job_id}` |

关键规则：

- 目标仓库必须是 GitHub 公开仓库。
- Personal access token classic 至少需要 `workflow`；公开仓库写入建议 `public_repo + workflow`。如果 GitHub UI 选择 `workflow` 时自动勾选 `repo`，平台允许继续，但仍要求目标仓库必须是公开仓库。
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

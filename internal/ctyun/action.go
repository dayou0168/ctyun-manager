package ctyun

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"strconv"
	"strings"

	"github.com/google/uuid"

	"github.com/dayou0168/ctyun-manager/internal/config"
	"github.com/dayou0168/ctyun-manager/internal/security"
	"github.com/dayou0168/ctyun-manager/internal/storage"
)

type Service struct {
	Config config.Config
	Keys   *security.Keyring
}

func (s Service) client(account storage.AccountRecord) (*Client, error) {
	ak, err := s.Keys.DecryptString(account.AKEncrypted)
	if err != nil {
		return nil, err
	}
	sk, err := s.Keys.DecryptString(account.SKEncrypted)
	if err != nil {
		return nil, err
	}
	return New(ak, sk, s.Config.OpenAPITimeout), nil
}

func (s Service) Price(ctx context.Context, account storage.AccountRecord, kind string, payload map[string]any) (map[string]any, error) {
	c, err := s.client(account)
	if err != nil {
		return nil, err
	}
	region, err := actionRegion(account, payload)
	if err != nil {
		return nil, err
	}
	switch kind {
	case "eip":
		body := compact(map[string]any{
			"clientToken": uuid.NewString(), "regionID": region, "projectID": valueOr(payload, "projectID", "0"),
			"cycleType": valueOr(payload, "cycleType", "on_demand"), "cycleCount": payload["cycleCount"],
			"name": valueOr2(payload, "name", "eipName", "eip-price-preview"), "bandwidth": intOr(payload["bandwidth"], 5),
			"bandwidthID": payload["bandwidthID"], "demandBillingType": valueOr(payload, "demandBillingType", "bandwidth"),
		})
		return tryPOST(ctx, c, s.Config.EIPEndpoint, []string{"/v4/eip/querycreateprice", "/v4/eip/query-create-price"}, body)
	case "ecs":
		body := ecsCreateBody(payload, region)
		flavor := first(payload["flavorName"], payload["specName"])
		image := first(payload["imageUUID"], payload["imageID"])
		if flavor != "" && image != "" {
			onDemand := boolOr(payload["onDemand"], true)
			newOrder := compact(map[string]any{
				"regionID": region, "resourceType": "VM", "count": intOr(payload["count"], 1), "onDemand": onDemand,
				"cycleType": when(!onDemand, payload["cycleType"]), "cycleCount": when(!onDemand, intOr(payload["cycleCount"], 1)),
				"flavorName": flavor, "imageUUID": image, "sysDiskType": valueOr2(payload, "sysDiskType", "bootDiskType", "SSD"),
				"sysDiskSize": intOr(firstAny(payload["sysDiskSize"], payload["bootDiskSize"]), 40),
				"bandwidth":   when(first(payload["extIP"], "0") == "1", intOr(payload["bandwidth"], 1)),
			})
			if result, requestErr := tryPOST(ctx, c, s.Config.ECSEndpoint, []string{"/v4/order/new-query-price", "/v4/new-order/query-price"}, newOrder); requestErr == nil {
				return result, nil
			}
		}
		return tryPOST(ctx, c, s.Config.ECSEndpoint, []string{"/v4/ecs/querycreateprice", "/v4/ecs/query-create-price", "/v4/ecs/create-instance-price", "/v4/ecs/query-price", "/v4/ecs/query-instance-price"}, body)
	default:
		return nil, errors.New("不支持的询价类型")
	}
}

func (s Service) Action(ctx context.Context, account storage.AccountRecord, kind, action string, payload map[string]any) (map[string]any, error) {
	c, err := s.client(account)
	if err != nil {
		return nil, err
	}
	region, err := actionRegion(account, payload)
	if err != nil {
		return nil, err
	}
	resourceID := first(payload["resource_id"])
	post := func(endpoint, path string, body map[string]any) (map[string]any, error) {
		return c.Request(ctx, endpoint, path, http.MethodPost, compact(body))
	}
	common := func(key, id string) map[string]any {
		return map[string]any{"regionID": region, key: id, "clientToken": uuid.NewString()}
	}
	switch kind {
	case "ecs":
		id := first(payload["instanceID"], payload["instanceId"], payload["instance_id"], resourceID)
		if action == "create" {
			body := ecsCreateBody(payload, region)
			if first(body["instanceName"]) == "" || first(body["flavorID"], body["flavorName"]) == "" || first(body["imageID"]) == "" || first(body["vpcID"]) == "" {
				return nil, errors.New("创建云主机至少需要 instanceName、flavorID/flavorName、imageID、vpcID")
			}
			return post(s.Config.ECSEndpoint, "/v4/ecs/create-instance", body)
		}
		if id == "" {
			return nil, errors.New("云主机操作缺少 instanceID")
		}
		body := common("instanceID", id)
		switch action {
		case "start":
			return post(s.Config.ECSEndpoint, "/v4/ecs/start-instance", body)
		case "stop":
			body["force"] = boolOr(payload["force"], false)
			return post(s.Config.ECSEndpoint, "/v4/ecs/stop-instance", body)
		case "reboot":
			return post(s.Config.ECSEndpoint, "/v4/ecs/reboot-instance", body)
		case "update":
			body["displayName"], body["instanceName"], body["instanceDescription"] = payload["displayName"], payload["instanceName"], payload["instanceDescription"]
			return post(s.Config.ECSEndpoint, "/v4/ecs/update-instance", body)
		case "reset_password":
			if first(payload["newPassword"]) == "" {
				return nil, errors.New("重置密码需要 newPassword")
			}
			body["newPassword"], body["userName"] = payload["newPassword"], payload["userName"]
			return post(s.Config.ECSEndpoint, "/v4/ecs/reset-password", body)
		case "rebuild":
			if first(payload["password"], payload["keyPairID"]) == "" {
				return nil, errors.New("重装系统需要密码或密钥对")
			}
			for _, k := range []string{"userName", "password", "keyPairID", "imageID", "instanceName", "monitorService", "payImage"} {
				body[k] = payload[k]
			}
			return post(s.Config.ECSEndpoint, "/v4/ecs/rebuild-instance", body)
		case "resize":
			if first(payload["flavorID"]) == "" {
				return nil, errors.New("变更规格需要 flavorID")
			}
			body["flavorID"], body["payVoucherPrice"] = payload["flavorID"], floatOr(payload["payVoucherPrice"], 0)
			return post(s.Config.ECSEndpoint, "/v4/ecs/update-flavor-spec", body)
		case "deletion_protection":
			body["deletionProtection"] = boolOr(payload["deletionProtection"], false)
			return post(s.Config.ECSEndpoint, "/v4/ecs/update-deletion-protection", body)
		case "auto_renew":
			return post(s.Config.ECSEndpoint, "/v4/ecs/update-auto-renew-config", map[string]any{"regionID": region, "instanceIDList": id, "autoRenewStatus": intOr(payload["autoRenewStatus"], 0), "autoRenewCycleType": payload["autoRenewCycleType"], "autoRenewCycleCount": intOr(payload["autoRenewCycleCount"], 0)})
		case "create_image":
			if first(payload["imageName"]) == "" {
				return nil, errors.New("制作私有镜像需要 imageName")
			}
			return post(s.Config.IMSEndpoint, "/v4/image/create", map[string]any{"regionID": region, "instanceID": id, "imageName": payload["imageName"], "description": payload["description"], "projectID": valueOr(payload, "projectID", "0"), "labels": []any{}})
		case "release":
			return post(s.Config.ECSEndpoint, "/v4/ecs/destroy-instance", body)
		case "unsubscribe":
			body["deleteVolume"], body["deleteEIP"] = boolOr(payload["deleteVolume"], false), boolOr(payload["deleteEIP"], false)
			return post(s.Config.ECSEndpoint, "/v4/ecs/unsubscribe-instance", body)
		}
	case "eip":
		id := first(payload["eipID"], resourceID)
		if action == "create" {
			body := map[string]any{"clientToken": uuid.NewString(), "regionID": region, "name": first(payload["name"], payload["eipName"]), "bandwidth": intOr(payload["bandwidth"], 5), "cycleType": valueOr(payload, "cycleType", "on_demand"), "cycleCount": payload["cycleCount"], "bandwidthID": payload["bandwidthID"], "demandBillingType": valueOr(payload, "demandBillingType", "bandwidth"), "lineType": valueOr(payload, "lineType", "163"), "payVoucherPrice": payload["payVoucherPrice"], "projectID": valueOr(payload, "projectID", "0")}
			if first(body["name"]) == "" {
				return nil, errors.New("创建弹性IP需要名称")
			}
			return post(s.Config.EIPEndpoint, "/v4/eip/create", body)
		}
		if id == "" {
			return nil, errors.New("弹性IP操作缺少 eipID")
		}
		body := common("eipID", id)
		switch action {
		case "bind":
			if first(payload["associationID"]) == "" {
				return nil, errors.New("绑定弹性IP需要 associationID")
			}
			body["associationID"], body["associationType"], body["projectID"] = payload["associationID"], intOr(payload["associationType"], 1), valueOr(payload, "projectID", "0")
			return post(s.Config.EIPEndpoint, "/v4/eip/associate", body)
		case "unbind":
			body["projectID"] = valueOr(payload, "projectID", "0")
			return post(s.Config.EIPEndpoint, "/v4/eip/disassociate", body)
		case "rename":
			if first(payload["name"]) == "" {
				return nil, errors.New("修改弹性IP名称需要 name")
			}
			body["name"], body["projectID"] = payload["name"], valueOr(payload, "projectID", "0")
			return post(s.Config.EIPEndpoint, "/v4/eip/change-name", body)
		case "release", "unsubscribe":
			return post(s.Config.EIPEndpoint, "/v4/eip/delete", body)
		}
	case "vpc":
		id := first(payload["vpcID"], resourceID)
		if action == "create" {
			if first(payload["name"]) == "" || first(payload["CIDR"], payload["cidr"]) == "" {
				return nil, errors.New("创建 VPC 需要 name 和 CIDR")
			}
			return post(s.Config.VPCEndpoint, "/v4/vpc/create", map[string]any{"regionID": region, "clientToken": uuid.NewString(), "name": payload["name"], "CIDR": first(payload["CIDR"], payload["cidr"]), "description": payload["description"], "enableIpv6": boolOr(payload["enableIpv6"], false), "projectID": valueOr(payload, "projectID", "0")})
		}
		if action == "create_subnet" {
			if first(payload["name"]) == "" || id == "" || first(payload["CIDR"], payload["cidr"]) == "" {
				return nil, errors.New("创建子网需要 name、vpcID 和 CIDR")
			}
			return post(s.Config.VPCEndpoint, "/v4/vpc/create-subnet", map[string]any{"regionID": region, "clientToken": uuid.NewString(), "name": payload["name"], "vpcID": id, "CIDR": first(payload["CIDR"], payload["cidr"]), "description": payload["description"], "projectID": valueOr(payload, "projectID", "0")})
		}
		if id == "" {
			return nil, errors.New("VPC 操作需要 vpcID")
		}
		if action == "update" {
			return post(s.Config.VPCEndpoint, "/v4/vpc/update", map[string]any{"regionID": region, "clientToken": uuid.NewString(), "vpcID": id, "name": payload["name"], "description": payload["description"], "dnsHostnamesEnabled": intOr(payload["dnsHostnamesEnabled"], 0), "projectID": valueOr(payload, "projectID", "0")})
		}
		if action == "delete" {
			return post(s.Config.VPCEndpoint, "/v4/vpc/delete", common("vpcID", id))
		}
	case "subnet":
		id := first(payload["subnetID"], resourceID)
		if id == "" {
			return nil, errors.New("子网操作需要 subnetID")
		}
		if action == "update" {
			return post(s.Config.SubnetEndpoint, "/v4/vpc/update-subnet", map[string]any{"regionID": region, "subnetID": id, "name": payload["name"], "description": payload["description"], "dnsList": payload["dnsList"], "dnsServers": payload["dnsServers"]})
		}
		if action == "delete" {
			return post(s.Config.SubnetEndpoint, "/v4/vpc/delete-subnet", common("subnetID", id))
		}
	case "security_group":
		id := first(payload["securityGroupID"], resourceID)
		base := map[string]any{"regionID": region, "clientToken": uuid.NewString(), "projectID": valueOr(payload, "projectID", "0")}
		if action == "create" {
			if first(payload["vpcID"]) == "" || first(payload["name"]) == "" {
				return nil, errors.New("创建安全组需要 vpcID 和 name")
			}
			base["vpcID"], base["name"], base["description"] = payload["vpcID"], payload["name"], valueOr(payload, "description", "")
			return post(s.Config.VPCEndpoint, "/v4/vpc/create-security-group", base)
		}
		if id == "" {
			return nil, errors.New("安全组操作需要 securityGroupID")
		}
		base["securityGroupID"] = id
		switch action {
		case "update":
			base["name"], base["description"], base["enabled"] = payload["name"], payload["description"], boolOr(payload["enabled"], true)
			return post(s.Config.VPCEndpoint, "/v4/vpc/modify-security-group-attribute", base)
		case "create_rule":
			direction := ruleDirection(payload["direction"])
			base["securityGroupRules"] = []any{securityRule(payload)}
			return post(s.Config.VPCEndpoint, "/v4/vpc/create-security-group-"+direction, base)
		case "delete_rule":
			ruleID := first(payload["securityGroupRuleID"], payload["ruleID"], payload["id"])
			if ruleID == "" {
				return nil, errors.New("删除安全组规则需要 securityGroupRuleID")
			}
			base["securityGroupRuleID"] = ruleID
			return post(s.Config.VPCEndpoint, "/v4/vpc/revoke-security-group-"+ruleDirection(payload["direction"]), base)
		case "delete":
			return post(s.Config.VPCEndpoint, "/v4/vpc/delete-security-group", base)
		}
	case "route_table":
		id := first(payload["routeTableID"], resourceID)
		base := map[string]any{"regionID": region, "clientToken": uuid.NewString()}
		if action == "create" {
			if first(payload["vpcID"]) == "" || first(payload["name"]) == "" {
				return nil, errors.New("创建路由表需要 vpcID 和 name")
			}
			base["vpcID"], base["name"], base["description"], base["projectID"], base["subnetLocalRouteEnabled"] = payload["vpcID"], payload["name"], payload["description"], valueOr(payload, "projectID", "0"), intOr(payload["subnetLocalRouteEnabled"], 0)
			return post(s.Config.VPCEndpoint, "/v4/vpc/route-table/create", base)
		}
		if id == "" {
			return nil, errors.New("路由表操作需要 routeTableID")
		}
		base["routeTableID"] = id
		if action == "update" {
			base["name"], base["description"], base["subnetLocalRouteEnabled"] = payload["name"], payload["description"], intOr(payload["subnetLocalRouteEnabled"], 0)
			return post(s.Config.VPCEndpoint, "/v4/vpc/route-table/modify", base)
		}
		if action == "delete" {
			return post(s.Config.VPCEndpoint, "/v4/vpc/route-table/delete", base)
		}
	case "acl":
		id := first(payload["aclID"], resourceID)
		base := map[string]any{"regionID": region, "clientToken": uuid.NewString()}
		if action == "create" {
			if first(payload["vpcID"]) == "" || first(payload["name"]) == "" {
				return nil, errors.New("创建 ACL 需要 vpcID 和 name")
			}
			base["vpcID"], base["name"], base["description"], base["projectID"], base["applyToPublicLb"] = payload["vpcID"], payload["name"], payload["description"], valueOr(payload, "projectID", "0"), boolOr(payload["applyToPublicLb"], false)
			return post(s.Config.VPCEndpoint, "/v4/acl/create", base)
		}
		if id == "" {
			return nil, errors.New("ACL 操作需要 aclID")
		}
		base["aclID"] = id
		if action == "update" {
			base["name"], base["description"], base["enabled"], base["projectID"] = payload["name"], payload["description"], payload["enabled"], valueOr(payload, "projectID", "0")
			return post(s.Config.VPCEndpoint, "/v4/acl/update", base)
		}
		if action == "delete" {
			return post(s.Config.VPCEndpoint, "/v4/acl/delete", base)
		}
	case "image":
		id := first(payload["imageID"], resourceID)
		if id == "" {
			return nil, errors.New("镜像操作需要 imageID")
		}
		body := map[string]any{"regionID": region, "imageID": id}
		switch action {
		case "accept":
			return post(s.Config.IMSEndpoint, "/v4/image/shared-image/accept", body)
		case "reject":
			return post(s.Config.IMSEndpoint, "/v4/image/shared-image/reject", body)
		case "delete":
			return post(s.Config.IMSEndpoint, "/v4/image/delete", body)
		case "share", "unshare":
			destination := first(payload["destinationAccountID"], payload["destinationUser"])
			if destination == "" {
				return nil, errors.New("共享镜像需要 destinationAccountID")
			}
			body["destinationAccountID"] = destination
			path := "/v4/image/shared-image/create"
			if action == "unshare" {
				path = "/v4/image/shared-image/delete"
			}
			return post(s.Config.IMSEndpoint, path, body)
		case "copy":
			if first(payload["imageName"]) == "" {
				return nil, errors.New("复制镜像需要 imageName")
			}
			body["imageName"], body["description"], body["projectID"], body["labels"] = payload["imageName"], payload["description"], valueOr(payload, "projectID", "0"), []any{}
			return post(s.Config.IMSEndpoint, "/v4/image/copy", body)
		}
	case "vip":
		id := first(payload["haVipID"], resourceID)
		if action == "create" {
			if first(payload["subnetID"]) == "" {
				return nil, errors.New("创建虚拟IP需要 subnetID")
			}
			return post(s.Config.VIPEndpoint, "/v4/vpc/havip/create", map[string]any{"regionID": region, "clientToken": uuid.NewString(), "subnetID": payload["subnetID"], "networkID": first(payload["networkID"], payload["vpcID"]), "ipAddress": payload["ipAddress"], "vipType": valueOr(payload, "vipType", "v4")})
		}
		if id == "" {
			return nil, errors.New("虚拟IP操作需要 haVipID")
		}
		if action == "delete" {
			return post(s.Config.VIPEndpoint, "/v4/vpc/havip/delete", common("haVipID", id))
		}
		if action == "bind_ecs" || action == "unbind_ecs" || action == "bind_eip" || action == "unbind_eip" {
			bind := strings.HasPrefix(action, "bind_")
			isEIP := strings.HasSuffix(action, "_eip")
			body := common("haVipID", id)
			body["resourceType"] = "VM"
			if isEIP {
				body["resourceType"] = "NETWORK"
				body["floatingID"] = first(payload["floatingID"], payload["eipID"])
				if first(body["floatingID"]) == "" {
					return nil, errors.New("绑定弹性IP需要 floatingID/eipID")
				}
			} else {
				body["instanceID"], body["networkInterfaceID"] = payload["instanceID"], payload["networkInterfaceID"]
				if first(body["instanceID"]) == "" || first(body["networkInterfaceID"]) == "" {
					return nil, errors.New("绑定云主机需要 instanceID 和 networkInterfaceID")
				}
			}
			path := "/v4/vpc/havip/unbind"
			if bind {
				path = "/v4/vpc/havip/bind"
			}
			return post(s.Config.VIPEndpoint, path, body)
		}
	}
	return nil, fmt.Errorf("%s.%s 的正式接口尚未配置", kind, action)
}

func (s Service) RemoteLogin(ctx context.Context, account storage.AccountRecord, payload map[string]any) (map[string]any, error) {
	c, err := s.client(account)
	if err != nil {
		return nil, err
	}
	region, err := actionRegion(account, payload)
	if err != nil {
		return nil, err
	}
	id := first(payload["instanceID"], payload["instanceId"], payload["resource_id"])
	if id == "" {
		return nil, errors.New("获取远程登录地址缺少 instanceID")
	}
	problems := []string{}
	for _, path := range []string{"/v4/ecs/vnc/details", "/v4/ecs/lite/vnc/details"} {
		data, e := c.Request(ctx, s.Config.ECSEndpoint, path, http.MethodGet, map[string]any{"regionID": region, "instanceID": id})
		if e != nil {
			problems = append(problems, e.Error())
			continue
		}
		obj, _ := data["returnObj"].(map[string]any)
		token := first(obj["token"], obj["url"], obj["vncUrl"], obj["vncURL"], data["token"])
		if token != "" {
			return map[string]any{"regionID": region, "instanceID": id, "path": path, "url": token, "raw": data}, nil
		}
		problems = append(problems, path+": 未返回 VNC 地址")
	}
	return nil, errors.New("官方 VNC 远程登录接口暂不可用：" + strings.Join(problems, "；"))
}

func (s Service) Options(ctx context.Context, account storage.AccountRecord, kind, region, azName, vpcID string, availableOnly bool) ([]map[string]any, error) {
	c, err := s.client(account)
	if err != nil {
		return nil, err
	}
	var endpoint, path, method string
	params := map[string]any{"regionID": region}
	switch kind {
	case "zones":
		endpoint, path, method = s.Config.ECSEndpoint, "/v4/region/get-zones", http.MethodGet
	case "flavors":
		endpoint, path, method = s.Config.ECSEndpoint, "/v4/common/get-ecs-flavors", http.MethodGet
		params["azName"] = azName
		if availableOnly {
			params["availableOnly"], params["onlyAvailable"], params["showSoldOut"], params["filterSoldOut"] = true, true, false, true
		}
	case "keypairs":
		endpoint, path, method = s.Config.ECSEndpoint, "/v4/ecs/keypair/details", http.MethodPost
		params["pageNo"], params["pageSize"], params["projectID"] = 1, 50, "0"
	case "security_groups":
		endpoint, path, method = s.Config.VPCEndpoint, "/v4/vpc/new-query-security-groups", http.MethodGet
		params["vpcID"], params["pageNo"], params["pageNumber"], params["pageSize"] = vpcID, 1, 1, 50
	default:
		return nil, errors.New("不支持的实时选项类型")
	}
	if region == "" {
		return nil, errors.New("请先选择资源池")
	}
	data, err := c.Request(ctx, endpoint, path, method, compact(params))
	if err != nil {
		return nil, err
	}
	return Items(data), nil
}

func actionRegion(account storage.AccountRecord, payload map[string]any) (string, error) {
	region := first(payload["regionID"], payload["region"], payload["region_id"])
	if region == "" {
		regions := ParseRegionIDs(account.Region)
		if len(regions) > 0 {
			region = regions[0]
		}
	}
	if region == "" {
		return "", errors.New("该操作缺少 regionID")
	}
	return region, nil
}

func ecsCreateBody(p map[string]any, region string) map[string]any {
	body := map[string]any{"clientToken": uuid.NewString(), "regionID": region, "azName": valueOr(p, "azName", "random"), "instanceName": valueOr(p, "instanceName", "ecs-price-preview"), "displayName": valueOr2(p, "displayName", "instanceName", "ecs-price-preview"), "flavorID": p["flavorID"], "flavorName": p["flavorName"], "imageType": intOr(p["imageType"], 1), "imageID": firstAny(p["imageID"], p["imageUUID"]), "bootDiskType": valueOr(p, "bootDiskType", "SSD"), "bootDiskSize": intOr(p["bootDiskSize"], 40), "vpcID": p["vpcID"], "onDemand": boolOr(p["onDemand"], true), "extIP": valueOr(p, "extIP", "0"), "bandwidth": intOr(p["bandwidth"], 1), "userPassword": p["userPassword"], "keyPairID": p["keyPairID"], "cycleCount": p["cycleCount"], "cycleType": p["cycleType"], "autoRenewStatus": intOr(p["autoRenewStatus"], 0), "projectID": valueOr(p, "projectID", "0"), "userData": p["userData"], "payVoucherPrice": floatOr(p["payVoucherPrice"], 0), "monitorService": boolOr(p["monitorService"], true), "securityProduct": p["securityProduct"], "demandBillingType": p["demandBillingType"], "eipID": p["eipID"]}
	if subnet := first(p["subnetID"]); subnet != "" {
		body["networkCardList"] = []any{map[string]any{"subnetID": subnet, "isMaster": true}}
	}
	if groups := csvValues(p["secGroupList"]); len(groups) > 0 {
		body["secGroupList"] = groups
	}
	return compact(body)
}

func tryPOST(ctx context.Context, c *Client, endpoint string, paths []string, body map[string]any) (map[string]any, error) {
	problems := []string{}
	for _, path := range paths {
		result, err := c.Request(ctx, endpoint, path, http.MethodPost, compact(body))
		if err == nil {
			return result, nil
		}
		problems = append(problems, path+": "+err.Error())
	}
	return nil, errors.New(strings.Join(problems, "；"))
}
func compact(value map[string]any) map[string]any {
	result := map[string]any{}
	for k, v := range value {
		if v != nil && first(v) != "" {
			result[k] = v
		}
	}
	return result
}
func valueOr(m map[string]any, key string, fallback any) any {
	if first(m[key]) != "" {
		return m[key]
	}
	return fallback
}
func valueOr2(m map[string]any, a, b string, fallback any) any {
	if first(m[a]) != "" {
		return m[a]
	}
	if first(m[b]) != "" {
		return m[b]
	}
	return fallback
}
func firstAny(values ...any) any {
	for _, v := range values {
		if first(v) != "" {
			return v
		}
	}
	return nil
}
func when(ok bool, value any) any {
	if ok {
		return value
	}
	return nil
}
func intOr(v any, fallback int) int {
	if n, err := strconv.Atoi(first(v)); err == nil {
		return n
	}
	return fallback
}
func floatOr(v any, fallback float64) float64 {
	if n, err := strconv.ParseFloat(first(v), 64); err == nil {
		return n
	}
	return fallback
}
func boolOr(v any, fallback bool) bool {
	if v == nil || first(v) == "" {
		return fallback
	}
	switch strings.ToLower(first(v)) {
	case "1", "true", "yes", "on":
		return true
	case "0", "false", "no", "off":
		return false
	}
	return fallback
}
func csvValues(v any) []string {
	text := first(v)
	for _, sep := range []string{"，", ";", "；"} {
		text = strings.ReplaceAll(text, sep, ",")
	}
	out := []string{}
	for _, part := range strings.Split(text, ",") {
		if part = strings.TrimSpace(part); part != "" {
			out = append(out, part)
		}
	}
	return out
}

func ruleDirection(value any) string {
	v := strings.ToLower(first(value))
	if v == "egress" || v == "out" || v == "outbound" {
		return "egress"
	}
	return "ingress"
}

func securityRule(p map[string]any) map[string]any {
	return compact(map[string]any{
		"direction": ruleDirection(p["direction"]), "protocol": valueOr(p, "protocol", "all"),
		"ethertype": valueOr(p, "ethertype", "IPv4"), "destCidrIp": firstAny(p["destCidrIp"], p["cidr"], p["remoteIpPrefix"]),
		"rangeMin": firstAny(p["rangeMin"], p["portMin"]), "rangeMax": firstAny(p["rangeMax"], p["portMax"]),
		"priority": intOr(p["priority"], 100), "description": p["description"],
	})
}

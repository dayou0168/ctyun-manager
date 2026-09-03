package ctyun

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"
)

var boundPattern = regexp.MustCompile(`(?i)binding|binded|bound|attach|associate|in[-_ ]?use|using|used|绑定|使用`)
var unboundPattern = regexp.MustCompile(`(?i)unbinding|detach|free|idle|unbound|unbind|未绑定|空闲`)

func Normalize(item map[string]any, kind, scanRegion string, regionNames map[string]string) map[string]any {
	result := map[string]any{}
	for k, v := range item {
		result[k] = v
	}
	region := first(item["regionID"], item["regionId"], item["region_id"], item["regionUUID"], item["regionUuid"], scanRegion)
	id := resourceID(item, kind)
	name := first(item["name"], item["displayName"], item["instanceName"], item["eipName"], item["vpcName"], item["imageName"], item["nameZh"], item["nameEn"], item["securityGroupName"], id)
	flavor := object(item["flavor"])
	osInfo := object(item["os"])
	cards := objects(item["networkCardList"])
	firstCard := map[string]any{}
	if len(cards) > 0 {
		firstCard = cards[0]
	}
	fixed := slice(item["fixedIpList"])
	instances := objects(item["instanceInfo"])
	networks := objects(item["networkInfo"])
	boundIDs, boundAddresses := []string{}, []string{}
	for _, network := range networks {
		if v := first(network["eipID"], network["eipId"], network["floatingID"], network["floatingId"], network["id"]); v != "" {
			boundIDs = append(boundIDs, v)
		}
		if v := first(network["eipAddress"], network["publicIP"], network["publicIp"], network["public_ip"], network["floatingIP"], network["floatingIp"], network["ip"]); v != "" {
			boundAddresses = append(boundAddresses, v)
		}
	}
	publicIP := first(item["public_ip"], item["publicIP"], item["floatingIP"], item["floatingIp"], item["eipAddress"], extractAddress(item, "public"))
	privateIP := first(item["private_ip"], item["privateIP"], item["privateIp"], firstCard["IPv4Address"])
	if privateIP == "" && len(fixed) > 0 {
		privateIP = first(fixed[0])
	}
	if privateIP == "" {
		privateIP = extractAddress(item, "private")
	}
	rawStatus := first(item["status"], item["state"], item["instanceStatus"], item["imageStatus"])
	status := rawStatus
	expiredTime := first(item["expiredTime"], item["expireTime"], item["expirationTime"], item["expiredAt"], item["expireAt"], item["endTime"])
	expired := kind == "ecs" && isExpired(expiredTime)
	if expired {
		status = "expired"
	}
	binding := ""
	if kind == "eip" {
		binding = eipBinding(item, instances)
		if status == "" || boundPattern.MatchString(status) || unboundPattern.MatchString(status) {
			status = binding
		}
	}
	visibility := item["imageVisibilityCode"]
	if empty(visibility) {
		visibility = item["imageVisibility"]
	}
	if empty(visibility) {
		visibility = item["visibility"]
	}
	if text, ok := visibility.(string); ok {
		switch strings.ToLower(text) {
		case "private":
			visibility = 0
		case "public", "standard":
			visibility = 1
		case "shared":
			visibility = 2
		}
	}
	instanceNames := []string{}
	for _, instance := range instances {
		if v := first(instance["instanceName"], instance["id"]); v != "" {
			instanceNames = append(instanceNames, v)
		}
	}
	boundEIPs := boundAddresses
	if len(boundEIPs) == 0 {
		boundEIPs = boundIDs
	}
	fields := map[string]any{"id": id, "name": name, "status": status, "official_status": rawStatus, "expired_time": expiredTime, "is_expired": expired, "release_time": first(item["releaseTime"], item["releasedTime"]), "binding_status": binding, "region": region, "region_name": region, "billing_mode": first(item["billingMode"], item["cycleType"], item["chargeType"]), "spec": first(item["spec"], item["flavorName"], flavor["flavorName"]), "private_ip": privateIP, "public_ip": publicIP, "ip": first(item["ip"], item["ipv4"], item["eipAddress"], item["floatingIP"]), "bandwidth_mbps": first(item["bandwidth_mbps"], item["bandwidth"]), "cidr": first(item["cidr"], item["CIDR"]), "vpc_id": first(item["vpcID"], item["vpcId"]), "subnet_id": first(item["subnetID"], item["subnetId"]), "network_card_id": first(firstCard["networkCardID"], item["networkInterfaceID"]), "bound_instances": strings.Join(instanceNames, ", "), "bound_eips": strings.Join(boundEIPs, ", "), "bound_eip_ids": strings.Join(boundIDs, ", "), "image_type": first(item["imageType"], item["image_type"]), "os": first(osInfo["nameZh"], osInfo["nameEn"], osInfo["osType"], osInfo["osName"], scalarOS(item["os"]), item["osType"], item["osDistro"], item["osVersion"]), "visibility": fmt.Sprint(valueOrEmpty(visibility)), "source_user": first(item["sourceAccountID"], item["sourceUser"]), "destination_user": first(item["destinationAccountID"], item["destinationUser"])}
	if name, ok := regionNames[region]; ok {
		fields["region_name"] = name
	}
	for k, v := range fields {
		result[k] = v
	}
	return result
}

func first(values ...any) string {
	for _, v := range values {
		if !empty(v) {
			return fmt.Sprint(v)
		}
	}
	return ""
}
func empty(v any) bool {
	if v == nil {
		return true
	}
	switch x := v.(type) {
	case string:
		return x == ""
	case bool:
		return !x
	case float64:
		return x == 0
	}
	return false
}
func valueOrEmpty(v any) any {
	if v == nil {
		return ""
	}
	return v
}
func object(v any) map[string]any {
	if m, ok := v.(map[string]any); ok {
		return m
	}
	return map[string]any{}
}
func objects(v any) []map[string]any {
	a := slice(v)
	r := []map[string]any{}
	for _, v := range a {
		if m, ok := v.(map[string]any); ok {
			r = append(r, m)
		}
	}
	return r
}
func slice(v any) []any {
	if a, ok := v.([]any); ok {
		return a
	}
	return nil
}
func scalarOS(v any) any {
	if _, ok := v.(map[string]any); ok {
		return ""
	}
	return v
}
func extractAddress(item map[string]any, wanted string) string {
	for _, group := range objects(item["addresses"]) {
		for _, address := range objects(group["addressList"]) {
			kind := strings.ToLower(first(address["type"]))
			if wanted == "public" && (kind == "floating" || kind == "public" || kind == "internet") {
				return first(address["addr"])
			}
			if wanted == "private" && (kind == "fixed" || kind == "private" || kind == "intranet") {
				return first(address["addr"])
			}
		}
	}
	return ""
}
func eipBinding(item map[string]any, instances []map[string]any) string {
	raw := strings.ToLower(first(item["bindStatus"], item["bindingStatus"], item["associateStatus"], item["associationStatus"], item["bind_status"], item["association_status"], item["status"], item["state"]))
	if unboundPattern.MatchString(raw) {
		return "unbound"
	}
	if boundPattern.MatchString(raw) {
		return "bound"
	}
	for _, key := range []string{"associationID", "associationId", "association_id", "associationType", "association_type", "instanceID", "instanceId", "instance_id", "instanceName", "serverID", "serverId", "deviceID", "deviceId", "bindID", "bindId"} {
		v := strings.TrimSpace(first(item[key]))
		if v != "" && v != "0" && v != "-" {
			return "bound"
		}
	}
	if len(instances) > 0 {
		return "bound"
	}
	return "unbound"
}
func isExpired(value string) bool {
	if value == "" || value == "0" {
		return false
	}
	if n, err := strconv.ParseFloat(value, 64); err == nil {
		if n > 1e10 {
			n /= 1000
		}
		return n <= float64(time.Now().Unix())
	}
	for _, layout := range []string{time.RFC3339, "2006-01-02 15:04:05", "2006-01-02T15:04:05"} {
		if parsed, err := time.Parse(layout, value); err == nil {
			return !parsed.After(time.Now())
		}
	}
	return false
}

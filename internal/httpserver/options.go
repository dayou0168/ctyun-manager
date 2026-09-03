package httpserver

import (
	"encoding/json"
	"net/http"
	"strings"

	"github.com/dayou0168/ctyun-manager/internal/ctyun"
	"github.com/dayou0168/ctyun-manager/internal/security"
)

func (s *Server) accountOptions(w http.ResponseWriter, r *http.Request, _ security.Session) {
	id, a, ok := s.actionAccount(w, r)
	if !ok {
		return
	}
	kind := r.PathValue("kind")
	region := r.URL.Query().Get("region_id")
	vpcID := r.URL.Query().Get("vpc_id")
	if kind == "regions" {
		s.accountRegions(w, r, security.Session{})
		return
	}
	static := map[string][]map[string]any{"disk_types": {{"value": "SATA", "label": "普通IO"}, {"value": "SAS", "label": "高IO"}, {"value": "SSD", "label": "超高IO"}, {"value": "FAST-SSD", "label": "极速型SSD"}, {"value": "XSSD", "label": "XSSD"}}, "eip_lines": {{"value": "163", "label": "电信"}}, "eip_cycle_types": {{"value": "on_demand", "label": "按量"}, {"value": "MONTH", "label": "包月"}, {"value": "YEAR", "label": "包年"}}, "eip_demand_billing_types": {{"value": "bandwidth", "label": "按带宽"}, {"value": "upflowc", "label": "按流量"}}}
	if values, exists := static[kind]; exists {
		writeJSON(w, 200, values)
		return
	}
	if kind == "zones" || kind == "flavors" || kind == "keypairs" || kind == "security_groups" {
		items, err := (ctyun.Service{Config: s.cfg, Keys: s.keys}).Options(r.Context(), a, kind, region, r.URL.Query().Get("az_name"), vpcID, r.URL.Query().Get("available_only") == "1" || r.URL.Query().Get("available_only") == "true")
		if err == nil {
			writeJSON(w, 200, liveOptions(kind, items))
			return
		}
		s.logger.Warn("live_options_failed", "kind", kind, "error", err)
	}
	types := map[string]string{"vpcs": "vpc", "subnets": "subnet", "images": "image", "eips": "eip", "ecs": "ecs", "security_groups": "security_group", "vips": "vip", "route_tables": "route_table", "acls": "acl"}
	resourceType := types[kind]
	if resourceType == "" {
		writeDetail(w, 404, "不支持的选项类型")
		return
	}
	rows, err := s.store.Resources(r.Context(), resourceType, &id)
	if err != nil {
		s.internalStoreError(w, "options_query_failed", err)
		return
	}
	out := []map[string]any{}
	for _, row := range rows {
		if region != "" && row.Region != region {
			continue
		}
		p := map[string]any{}
		_ = json.Unmarshal([]byte(row.PayloadJSON), &p)
		if vpcID != "" && firstText(p, "vpc_id", "vpcID", "vpcId") != vpcID {
			continue
		}
		out = append(out, map[string]any{"value": row.ProviderID, "label": optionLabel(resourceType, row.Name, p), "meta": p})
	}
	writeJSON(w, 200, out)
}
func (s *Server) prewarmOptions(w http.ResponseWriter, r *http.Request, _ security.Session) {
	_, _, ok := s.actionAccount(w, r)
	if !ok {
		return
	}
	writeJSON(w, 200, map[string]any{"ok": true, "queued": 0, "message": "Go 服务按请求实时加载并缓存资源，无需后台预热"})
}
func liveOptions(kind string, items []map[string]any) []map[string]any {
	out := []map[string]any{}
	for _, item := range items {
		value, label := "", ""
		switch kind {
		case "zones":
			value = firstText(item, "name", "azName")
			label = firstText(item, "azDisplayName", "name", "azName")
		case "flavors":
			value = firstText(item, "flavorID", "id")
			label = firstText(item, "flavorName", "specName", "name")
		case "keypairs":
			value = firstText(item, "keyPairID", "id")
			label = firstText(item, "keyPairName", "name", "keyPairID")
		case "security_groups":
			value = firstText(item, "securityGroupID", "id")
			label = firstText(item, "securityGroupName", "name")
		}
		if value != "" {
			if label == "" {
				label = value
			}
			out = append(out, map[string]any{"value": value, "label": label, "meta": item})
		}
	}
	return out
}
func optionLabel(kind, name string, p map[string]any) string {
	switch kind {
	case "vpc", "subnet":
		if cidr := firstText(p, "cidr", "CIDR"); cidr != "" {
			return name + " · " + cidr
		}
	case "eip":
		if ip := firstText(p, "ip", "eipAddress", "public_ip"); ip != "" {
			return ip + " · " + name
		}
	case "ecs":
		if ip := firstText(p, "private_ip", "privateIP"); ip != "" {
			return name + " · " + ip
		}
	case "vip":
		if ip := firstText(p, "ip", "ipv4"); ip != "" {
			return ip
		}
	case "image":
		if os := firstText(p, "os", "osDistro", "osVersion", "osType"); os != "" {
			return name + " · " + os
		}
	}
	return strings.TrimSpace(name)
}

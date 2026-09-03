package ctyun

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/dayou0168/ctyun-manager/internal/security"
	"github.com/dayou0168/ctyun-manager/internal/storage"
)

type ConsoleService struct {
	Keys *security.Keyring
	HTTP *http.Client
}

func (s ConsoleService) cookie(account storage.AccountRecord) (string, error) {
	raw, err := s.Keys.DecryptString(account.CookieEncrypted)
	if err != nil || raw == "" {
		return "", errors.New("账号没有保存网页登录状态，请先在平台完成一次天翼云网页登录")
	}
	var state struct {
		Cookies []struct {
			Name   string `json:"name"`
			Value  string `json:"value"`
			Domain string `json:"domain"`
		} `json:"cookies"`
	}
	if json.Unmarshal([]byte(raw), &state) != nil {
		return "", errors.New("账号网页登录状态格式异常，请重新登录天翼云")
	}
	pairs := []string{}
	for _, c := range state.Cookies {
		if c.Name != "" && strings.HasSuffix(c.Domain, "ctyun.cn") {
			pairs = append(pairs, c.Name+"="+c.Value)
		}
	}
	if len(pairs) == 0 {
		return "", errors.New("账号没有可用的天翼云 cookie，请重新登录天翼云")
	}
	return strings.Join(pairs, "; "), nil
}
func (s ConsoleService) request(ctx context.Context, account storage.AccountRecord, method, path string, query url.Values, body any) (map[string]any, error) {
	cookie, err := s.cookie(account)
	if err != nil {
		return nil, err
	}
	target := "https://www.ctyun.cn" + path
	if len(query) > 0 {
		target += "?" + query.Encode()
	}
	var data []byte
	if body != nil {
		data, err = json.Marshal(body)
		if err != nil {
			return nil, err
		}
	}
	req, err := http.NewRequestWithContext(ctx, method, target, bytes.NewReader(data))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Cookie", cookie)
	req.Header.Set("Accept", "application/json, text/plain, */*")
	req.Header.Set("User-Agent", "Mozilla/5.0")
	req.Header.Set("Referer", "https://www.ctyun.cn/console/expense/order/renew")
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	client := s.HTTP
	if client == nil {
		client = &http.Client{Timeout: 45 * time.Second}
	}
	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("天翼云控制台接口请求失败: %w", err)
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 16<<20))
	if err != nil {
		return nil, err
	}
	if resp.StatusCode == 401 || resp.StatusCode == 403 {
		return nil, errors.New("天翼云网页登录态已失效，请重新登录")
	}
	var result map[string]any
	if json.Unmarshal(raw, &result) != nil {
		return nil, fmt.Errorf("天翼云控制台接口返回非 JSON: %s", string(raw[:min(len(raw), 300)]))
	}
	code := first(result["code"], result["statusCode"])
	if code != "" && code != "core.ok" && code != "0" && code != "800" {
		return nil, errors.New(first(result["reason"], result["message"], result["description"], string(raw)))
	}
	if ok, exists := result["success"].(bool); exists && !ok {
		return nil, errors.New(first(result["reason"], result["message"], "天翼云控制台请求失败"))
	}
	return result, nil
}
func unwrap(data map[string]any) any {
	if v, ok := data["data"]; ok {
		return v
	}
	return data
}
func (s ConsoleService) renewable(ctx context.Context, a storage.AccountRecord) ([]map[string]any, error) {
	rows := []map[string]any{}
	for page := 1; page <= 50; page++ {
		q := url.Values{"autoRenewStatus": {"0"}, "dueNoRenewal": {"CUS_300_35_0001"}, "ifRenew": {"1"}, "isAutoToNeed": {"CUS_300_1_0001"}, "isDemand": {"true"}, "limit": {"50"}, "offset": {strconv.Itoa((page - 1) * 50)}, "pageNo": {strconv.Itoa(page)}, "pageSize": {"50"}, "sceneType": {"1"}, "statuses": {"PUB_100_01_0001,PUB_100_01_0003"}}
		data, err := s.request(ctx, a, http.MethodGet, "/v1/bcc/product/instance/List", q, nil)
		if err != nil {
			return nil, err
		}
		obj, _ := unwrap(data).(map[string]any)
		pageRows, _ := obj["list"].([]any)
		if len(pageRows) == 0 {
			break
		}
		rows = append(rows, objectSlice(pageRows)...)
		total := intOr(obj["total"], len(rows))
		if len(rows) >= total {
			break
		}
	}
	return rows, nil
}
func (s ConsoleService) resolve(ctx context.Context, a storage.AccountRecord, ids []string) ([]map[string]any, error) {
	if len(ids) == 0 {
		return nil, errors.New("请选择需要续订的云主机")
	}
	if len(ids) > 50 {
		return nil, errors.New("天翼云手动批量续订一次最多支持 50 个资源")
	}
	rows, err := s.renewable(ctx, a)
	if err != nil {
		return nil, err
	}
	byID := map[string]map[string]any{}
	for _, r := range rows {
		byID[first(r["orgResourceId"])] = r
	}
	out := []map[string]any{}
	missing := []string{}
	for _, id := range ids {
		r := byID[strings.TrimSpace(id)]
		if r == nil || first(r["resourceId"]) == "" {
			missing = append(missing, id)
		} else {
			out = append(out, r)
		}
	}
	if len(missing) > 0 {
		return nil, errors.New("官方续订管理没有返回这些云主机: " + strings.Join(missing, "、"))
	}
	return out, nil
}
func renewPayload(rows []map[string]any, month int, byYear bool) map[string]any {
	if month < 1 {
		month = 1
	}
	if month > 36 {
		month = 36
	}
	ids := []string{}
	for _, r := range rows {
		ids = append(ids, first(r["resourceId"]))
	}
	year := 0
	if byYear {
		year = 1
	}
	return map[string]any{"resourceIds": strings.Join(ids, ";"), "month": month, "resourceType": "VM", "byYear": year, "specRenewStatus": 0, "sceneType": 1}
}
func publicRenew(rows []map[string]any) []map[string]any {
	out := []map[string]any{}
	for _, r := range rows {
		out = append(out, map[string]any{"instance_id": first(r["orgResourceId"]), "renew_resource_id": first(r["resourceId"]), "name": first(r["resourceName"]), "zone_name": first(r["zoneName"]), "status": first(r["stateName"], r["statusName"]), "expire_date": first(r["expireDate"]), "service_tag": first(r["serviceTag"]), "resource_type": first(r["resourceType"])})
	}
	return out
}
func (s ConsoleService) RenewPrice(ctx context.Context, a storage.AccountRecord, ids []string, month int, byYear bool) (map[string]any, error) {
	rows, err := s.resolve(ctx, a, ids)
	if err != nil {
		return nil, err
	}
	p := renewPayload(rows, month, byYear)
	data, err := s.request(ctx, a, http.MethodPost, "/v2/bcc/order/renew/getPrice", nil, p)
	if err != nil {
		return nil, err
	}
	return map[string]any{"ok": true, "items": publicRenew(rows), "price": unwrap(data), "payload": p}, nil
}
func (s ConsoleService) RenewSubmit(ctx context.Context, a storage.AccountRecord, ids []string, month int, byYear bool) (map[string]any, error) {
	rows, err := s.resolve(ctx, a, ids)
	if err != nil {
		return nil, err
	}
	p := renewPayload(rows, month, byYear)
	data, err := s.request(ctx, a, http.MethodPost, "/v1/bcc/product/instance/renew/Submit", nil, map[string]any{"data": p})
	if err != nil {
		return nil, err
	}
	return map[string]any{"ok": true, "items": publicRenew(rows), "result": unwrap(data), "payload": p, "submitted_at": time.Now().Unix()}, nil
}
func (s ConsoleService) RenewOrderStatus(ctx context.Context, a storage.AccountRecord, id string) (map[string]any, error) {
	if strings.TrimSpace(id) == "" {
		return nil, errors.New("缺少续订支付订单 ID")
	}
	result := map[string]any{"ok": true, "master_order_id": id, "checked_at": time.Now().Unix(), "errors": map[string]string{}}
	paths := map[string]string{"detail": "/v1/bcc/order/GetDetail", "pay_type": "/v1/bcc/order/PayType", "pay_detail": "/v1/bcc/order/PayDetail", "query_status": "/v1/bcc/order/QueryOrderStatus"}
	for key, path := range paths {
		q := url.Values{}
		if key == "detail" {
			q.Set("masterOrderId", id)
			q.Set("paymentPage", "1")
		} else if key != "pay_type" && key != "pay_detail" {
			q.Set("masterOrderId", id)
		}
		data, err := s.request(ctx, a, http.MethodGet, path, q, nil)
		if err != nil {
			result["errors"].(map[string]string)[key] = err.Error()
		} else {
			result[key] = unwrap(data)
		}
	}
	detail, _ := result["detail"].(map[string]any)
	status := first(detail["status"], detail["masterOrderStatus"], detail["orderStatus"])
	name := first(detail["statusName"], detail["masterOrderStatusName"], detail["orderStatusName"])
	result["status"], result["status_name"] = status, name
	result["paid"] = strings.Contains(name, "支付成功") || strings.Contains(name, "已支付") || status == "2" || status == "3" || status == "14" || status == "32"
	result["pay_url"] = "https://www.ctyun.cn/console/expense/order/pay?orderId=" + url.QueryEscape(id)
	return result, nil
}

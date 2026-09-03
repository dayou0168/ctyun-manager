package ctyun

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/google/uuid"
)

const maxResponseSize = 32 << 20

type Client struct {
	AK, SK string
	HTTP   *http.Client
}

type APIError struct {
	Status  int
	Message string
}

func (e *APIError) Error() string {
	if e.Status > 0 {
		return fmt.Sprintf("天翼云 OpenAPI HTTP %d: %s", e.Status, e.Message)
	}
	return "天翼云 OpenAPI 返回错误: " + e.Message
}

func New(ak, sk string, timeout time.Duration) *Client {
	if timeout <= 0 {
		timeout = 20 * time.Second
	}
	return &Client{AK: ak, SK: sk, HTTP: &http.Client{Timeout: timeout}}
}

func EncodeQuery(query map[string]any) string {
	keys := make([]string, 0, len(query))
	for key, value := range query {
		if value != nil && fmt.Sprint(value) != "" {
			keys = append(keys, key)
		}
	}
	sort.Strings(keys)
	parts := make([]string, 0, len(keys))
	for _, key := range keys {
		parts = append(parts, percentEncode(key)+"="+percentEncode(queryTextValue(query[key])))
	}
	return strings.Join(parts, "&")
}

func percentEncode(value string) string {
	return strings.ReplaceAll(url.QueryEscape(value), "+", "%20")
}

func queryTextValue(value any) string {
	if flag, ok := value.(bool); ok {
		if flag {
			return "True"
		}
		return "False"
	}
	return fmt.Sprint(value)
}

func Sign(ak, sk string, body []byte, query map[string]any, eopDate, requestID string) map[string]string {
	queryText := EncodeQuery(query)
	sum := sha256.Sum256(body)
	headerText := "ctyun-eop-request-id:" + requestID + "\n" + "eop-date:" + eopDate + "\n"
	source := headerText + "\n" + queryText + "\n" + hex.EncodeToString(sum[:])
	kTime := hmacSHA256([]byte(sk), eopDate)
	kAK := hmacSHA256(kTime, ak)
	kDate := hmacSHA256(kAK, eopDate[:8])
	signature := base64.StdEncoding.EncodeToString(hmacSHA256(kDate, source))
	return map[string]string{"ctyun-eop-request-id": requestID, "eop-date": eopDate, "Eop-Authorization": ak + " Headers=ctyun-eop-request-id;eop-date Signature=" + signature, "Content-Type": "application/json"}
}

func hmacSHA256(key []byte, value string) []byte {
	mac := hmac.New(sha256.New, key)
	_, _ = mac.Write([]byte(value))
	return mac.Sum(nil)
}

func (c *Client) Request(ctx context.Context, endpoint, path, method string, params map[string]any) (map[string]any, error) {
	if c.AK == "" || c.SK == "" {
		return nil, errors.New("该账号缺少 AK/SK，无法调用正式 OpenAPI")
	}
	base, err := url.Parse(strings.TrimRight(endpoint, "/") + "/")
	if err != nil {
		return nil, err
	}
	target, err := base.Parse(strings.TrimLeft(path, "/"))
	if err != nil {
		return nil, err
	}
	method = strings.ToUpper(method)
	var body []byte
	query := map[string]any{}
	if method == http.MethodGet {
		query = params
		target.RawQuery = EncodeQuery(query)
	} else {
		body, err = json.Marshal(params)
		if err != nil {
			return nil, err
		}
	}
	requestID, err := uuid.NewUUID()
	if err != nil {
		return nil, err
	}
	date := time.Now().UTC().Format("20060102T150405Z")
	req, err := http.NewRequestWithContext(ctx, method, target.String(), bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	for k, v := range Sign(c.AK, c.SK, body, query, date, requestID.String()) {
		req.Header.Set(k, v)
	}
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return nil, fmt.Errorf("无法连接天翼云 OpenAPI: %w", err)
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, maxResponseSize+1))
	if err != nil {
		return nil, err
	}
	if len(raw) > maxResponseSize {
		return nil, errors.New("天翼云 OpenAPI 响应超过 32 MiB")
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, &APIError{Status: resp.StatusCode, Message: string(raw)}
	}
	var data map[string]any
	if err = json.Unmarshal(raw, &data); err != nil {
		return nil, fmt.Errorf("天翼云 OpenAPI 返回非 JSON: %w", err)
	}
	code := textValue(data["statusCode"])
	if code == "" {
		code = textValue(data["code"])
	}
	if code != "" && code != "800" && code != "200" && code != "0" {
		return nil, &APIError{Message: friendlyError(data)}
	}
	return data, nil
}

func (c *Client) ListRegions(ctx context.Context, endpoint, path string) ([]map[string]any, error) {
	data, err := c.Request(ctx, endpoint, path, http.MethodGet, map[string]any{})
	if err != nil {
		return nil, err
	}
	result := []map[string]any{}
	for _, item := range Items(data) {
		id := textValue(item["regionID"])
		if id == "" {
			id = textValue(item["regionId"])
		}
		if id == "" {
			id = textValue(item["id"])
		}
		if id == "" {
			continue
		}
		name := textValue(item["regionName"])
		if name == "" {
			name = textValue(item["name"])
		}
		if name == "" {
			name = textValue(item["region"])
		}
		if name == "" {
			name = id
		}
		copy := map[string]any{"regionID": id, "regionName": name}
		for k, v := range item {
			copy[k] = v
		}
		result = append(result, copy)
	}
	return result, nil
}

type ListRequest struct {
	Endpoint, Path, Method, ResourceType string
	RegionIDs                            []string
	Extra                                map[string]any
	Variants                             []map[string]any
	Paging                               bool
}

func ParseRegionIDs(value string) []string {
	for _, separator := range []string{"\r\n", "\n", "，", ";", "；"} {
		value = strings.ReplaceAll(value, separator, ",")
	}
	result := []string{}
	for _, item := range strings.Split(value, ",") {
		if item = strings.TrimSpace(item); item != "" {
			result = append(result, item)
		}
	}
	return result
}

func (c *Client) ListAll(ctx context.Context, request ListRequest) ([]map[string]any, error) {
	if len(request.RegionIDs) == 0 {
		return nil, errors.New("没有查询到可用资源池")
	}
	paths := ParseRegionIDs(request.Path)
	if len(paths) == 0 {
		return nil, errors.New("接口 path 未配置")
	}
	variants := request.Variants
	if len(variants) == 0 {
		variants = []map[string]any{{}}
	}
	result := []map[string]any{}
	seen := map[string]bool{}
	successes := 0
	problems := []string{}
	for _, path := range paths {
		for _, region := range request.RegionIDs {
			for _, variant := range variants {
				page, total := 1, 1
				for page <= total && page <= 200 {
					params := map[string]any{"regionID": region}
					for k, v := range request.Extra {
						params[k] = v
					}
					for k, v := range variant {
						params[k] = v
					}
					if request.Paging {
						params["pageNo"] = page
						params["pageSize"] = 50
						if request.ResourceType == "vpc" || request.ResourceType == "subnet" {
							params["pageNumber"] = page
							delete(params, "pageNo")
						}
						if request.ResourceType == "eip" {
							params["page"] = page
						}
					}
					data, err := c.Request(ctx, request.Endpoint, path, request.Method, params)
					if err != nil {
						problems = append(problems, fmt.Sprintf("%s %s: %v", path, region, err))
						break
					}
					successes++
					if request.Paging {
						total = TotalPages(data)
					}
					for _, item := range Items(data) {
						id := resourceID(item, request.ResourceType)
						if id == "" {
							continue
						}
						key := id + ":" + region
						if seen[key] {
							continue
						}
						seen[key] = true
						copy := map[string]any{}
						for k, v := range item {
							copy[k] = v
						}
						copy["_api_path"] = path
						copy["_scan_region"] = region
						result = append(result, copy)
					}
					page++
				}
			}
		}
	}
	if successes == 0 && len(problems) > 0 {
		return nil, errors.New(strings.Join(problems, "；"))
	}
	return result, nil
}

func resourceID(item map[string]any, kind string) string {
	keys := []string{"id", "uuid", "ID", kind + "ID", "resourceID", "instanceID", "eipID", "vpcID", "subnetID", "imageID", "imageUUID", "imageUuid", "image_uuid"}
	if kind == "ecs" {
		keys = []string{"instanceID", "instanceId", "instance_id", "deviceUUID", "deviceUuid", "resourceID", "resourceId", "uuid", "id", "ID"}
	}
	for _, key := range keys {
		if value := textValue(item[key]); value != "" {
			return value
		}
	}
	return ""
}

func Items(data map[string]any) []map[string]any {
	obj := data["returnObj"]
	if obj == nil {
		obj = data["data"]
	}
	if obj == nil {
		obj = data
	}
	return findItems(obj)
}
func findItems(obj any) []map[string]any {
	keys := []string{"regionList", "results", "eips", "vpcs", "subnets", "images", "records", "rows", "list", "items", "data", "zoneList", "securityGroups", "routeTables", "acls"}
	if m, ok := obj.(map[string]any); ok {
		for _, k := range keys {
			if a, ok := m[k].([]any); ok {
				return objectSlice(a)
			}
		}
	}
	if a, ok := obj.([]any); ok {
		result := []map[string]any{}
		nestedKeys := []string{"eips", "vpcs", "subnets", "images", "results", "records", "items", "zoneList", "securityGroups", "routeTables", "acls"}
		for _, entry := range objectSlice(a) {
			found := false
			for _, key := range nestedKeys {
				if child, ok := entry[key].([]any); ok {
					result = append(result, objectSlice(child)...)
					found = true
				}
			}
			if !found {
				result = append(result, entry)
			}
		}
		return result
	}
	return []map[string]any{}
}
func objectSlice(a []any) []map[string]any {
	r := []map[string]any{}
	for _, v := range a {
		if m, ok := v.(map[string]any); ok {
			r = append(r, m)
		}
	}
	return r
}
func TotalPages(data map[string]any) int {
	for _, v := range []any{data, data["returnObj"], data["data"]} {
		if m, ok := v.(map[string]any); ok {
			for _, k := range []string{"totalPage", "totalPages"} {
				if n := textValue(m[k]); n != "" {
					if parsed, e := strconv.Atoi(n); e == nil && parsed > 0 {
						return parsed
					}
				}
			}
		}
	}
	return 1
}
func textValue(v any) string {
	if v == nil {
		return ""
	}
	return fmt.Sprint(v)
}
func friendlyError(data map[string]any) string {
	for _, k := range []string{"message", "msg", "description", "error", "errorMessage"} {
		if v := textValue(data[k]); v != "" {
			return v
		}
	}
	raw, _ := json.Marshal(data)
	return string(raw)
}

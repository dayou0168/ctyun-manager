package ctyun

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"sync"

	"github.com/google/uuid"

	"github.com/dayou0168/ctyun-manager/internal/config"
	"github.com/dayou0168/ctyun-manager/internal/security"
	"github.com/dayou0168/ctyun-manager/internal/storage"
)

var SyncTypes = []string{"ecs", "eip", "vpc", "subnet", "vip", "image", "security_group", "route_table", "acl"}

type SyncStore interface {
	AccountByID(context.Context, int64) (storage.AccountRecord, error)
	ReplaceResources(context.Context, int64, string, []string, []storage.ResourceWrite) error
	RecordOperation(context.Context, *int64, string, string, string, string, string) error
}
type Syncer struct {
	Config config.Config
	Keys   *security.Keyring
	Store  SyncStore
}
type SyncResult struct {
	OK      bool              `json:"ok"`
	Counts  map[string]int    `json:"counts"`
	Errors  map[string]string `json:"errors"`
	Skipped map[string]string `json:"skipped"`
}

func (s *Syncer) Sync(ctx context.Context, accountID int64, kinds, overrideRegions []string) (SyncResult, error) {
	account, err := s.Store.AccountByID(ctx, accountID)
	if err != nil {
		return SyncResult{}, err
	}
	valid := []string{}
	result := SyncResult{OK: true, Counts: map[string]int{}, Errors: map[string]string{}, Skipped: map[string]string{}}
	allowed := map[string]bool{}
	for _, kind := range SyncTypes {
		allowed[kind] = true
	}
	seen := map[string]bool{}
	for _, kind := range kinds {
		if seen[kind] {
			continue
		}
		seen[kind] = true
		if !allowed[kind] {
			result.Errors[kind] = "不支持的同步类型"
			result.OK = false
		} else {
			valid = append(valid, kind)
		}
	}
	if len(valid) == 0 {
		return result, nil
	}
	var lock sync.Mutex
	var wait sync.WaitGroup
	limit := make(chan struct{}, 6)
	for _, kind := range valid {
		kind := kind
		wait.Add(1)
		go func() {
			defer wait.Done()
			limit <- struct{}{}
			defer func() { <-limit }()
			count, e := s.SyncKind(ctx, account, kind, overrideRegions)
			lock.Lock()
			defer lock.Unlock()
			if e != nil {
				result.Errors[kind] = e.Error()
				result.Counts[kind] = 0
				result.OK = false
			} else {
				result.Counts[kind] = count
			}
		}()
	}
	wait.Wait()
	status := "success"
	if !result.OK {
		status = "partial"
	}
	detail, _ := json.Marshal(result)
	_ = s.Store.RecordOperation(ctx, &accountID, "account", stringID(accountID), "sync", status, string(detail))
	return result, nil
}

func stringID(id int64) string { return fmt.Sprint(id) }

func (s *Syncer) SyncKind(ctx context.Context, account storage.AccountRecord, kind string, overrideRegions []string) (int, error) {
	ak, err := s.Keys.DecryptString(account.AKEncrypted)
	if err != nil {
		return 0, err
	}
	sk, err := s.Keys.DecryptString(account.SKEncrypted)
	if err != nil {
		return 0, err
	}
	client := New(ak, sk, s.Config.OpenAPITimeout)
	regions := overrideRegions
	if len(regions) == 0 {
		regions = ParseRegionIDs(account.Region)
	}
	if len(regions) == 0 {
		regions = ParseRegionIDs(s.Config.DefaultRegionIDs)
	}
	regionNames := map[string]string{}
	listed, regionErr := client.ListRegions(ctx, s.Config.RegionEndpoint, s.Config.RegionListPath)
	if regionErr == nil {
		for _, region := range listed {
			id := first(region["regionID"])
			if id != "" {
				regionNames[id] = first(region["regionName"], id)
				if len(regions) == 0 {
					regions = append(regions, id)
				}
			}
		}
	}
	if len(regions) == 0 {
		return 0, errors.New("没有查询到可用资源池")
	}
	request := ListRequest{ResourceType: kind, RegionIDs: regions, Paging: true, Method: http.MethodPost}
	switch kind {
	case "ecs":
		request.Endpoint = s.Config.ECSEndpoint
		request.Path = s.Config.ECSListPath
	case "eip":
		request.Endpoint = s.Config.EIPEndpoint
		request.Path = s.Config.EIPListPath
		request.Extra = map[string]any{"clientToken": uuid.NewString()}
	case "vpc":
		request.Endpoint = s.Config.VPCEndpoint
		request.Path = s.Config.VPCListPath
		request.Method = http.MethodGet
		request.Extra = map[string]any{"projectID": "0"}
	case "subnet":
		request.Endpoint = s.Config.SubnetEndpoint
		request.Path = s.Config.SubnetListPath
		request.Method = http.MethodGet
	case "vip":
		request.Endpoint = s.Config.VIPEndpoint
		request.Path = s.Config.VIPListPath
		request.Extra = map[string]any{"clientToken": uuid.NewString(), "projectID": "0"}
		request.Paging = false
	case "image":
		request.Endpoint = s.Config.IMSEndpoint
		request.Path = s.Config.IMSListPath
		request.Method = http.MethodGet
		request.Variants = []map[string]any{{"imageVisibilityCode": 1}, {"imageVisibilityCode": 0}, {"imageVisibilityCode": 2}, {"imageType": "standard"}, {"imageType": "public"}, {"imageType": "private"}, {"imageType": "shared"}, {}}
	case "security_group":
		request.Endpoint = s.Config.VPCEndpoint
		request.Path = "/v4/vpc/new-query-security-groups"
		request.Method = http.MethodGet
	case "route_table":
		request.Endpoint = s.Config.VPCEndpoint
		request.Path = "/v4/vpc/route-table/new-list"
		request.Method = http.MethodGet
	case "acl":
		request.Endpoint = s.Config.VPCEndpoint
		request.Path = "/v4/acl/new-list"
		request.Method = http.MethodGet
	default:
		return 0, errors.New("不支持的同步类型")
	}
	raw, err := client.ListAll(ctx, request)
	if err != nil {
		return 0, err
	}
	writes := make([]storage.ResourceWrite, 0, len(raw))
	for _, item := range raw {
		scanRegion := first(item["_scan_region"])
		delete(item, "_scan_region")
		normalized := Normalize(item, kind, scanRegion, regionNames)
		id := first(normalized["id"])
		if id == "" {
			continue
		}
		payload, err := json.Marshal(normalized)
		if err != nil {
			return 0, err
		}
		writes = append(writes, storage.ResourceWrite{ProviderID: id, Name: first(normalized["name"], id), Region: first(normalized["region"], account.Region), Status: first(normalized["status"]), BillingMode: first(normalized["billing_mode"]), PayloadJSON: string(payload)})
	}
	if err = s.Store.ReplaceResources(ctx, account.ID, kind, regions, writes); err != nil {
		return 0, err
	}
	return len(writes), nil
}

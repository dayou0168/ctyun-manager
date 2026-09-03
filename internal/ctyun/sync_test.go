package ctyun

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/dayou0168/ctyun-manager/internal/config"
	"github.com/dayou0168/ctyun-manager/internal/security"
	"github.com/dayou0168/ctyun-manager/internal/storage"
)

type memorySyncStore struct {
	account storage.AccountRecord
	kind    string
	regions []string
	writes  []storage.ResourceWrite
}

func (m *memorySyncStore) AccountByID(context.Context, int64) (storage.AccountRecord, error) {
	return m.account, nil
}
func (m *memorySyncStore) ReplaceResources(_ context.Context, _ int64, kind string, regions []string, writes []storage.ResourceWrite) error {
	m.kind = kind
	m.regions = regions
	m.writes = writes
	return nil
}
func (m *memorySyncStore) RecordOperation(context.Context, *int64, string, string, string, string, string) error {
	return nil
}

func TestSyncECSAcrossPages(t *testing.T) {
	t.Parallel()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Eop-Authorization") == "" {
			t.Error("unsigned request")
		}
		if r.URL.Path == "/regions" {
			_, _ = w.Write([]byte(`{"statusCode":800,"returnObj":{"regionList":[{"regionID":"r1","regionName":"Region One"}]}}`))
			return
		}
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		page := int(body["pageNo"].(float64))
		_, _ = w.Write([]byte(`{"statusCode":800,"returnObj":{"totalPages":2,"results":[{"instanceID":"ecs-` + string(rune('0'+page)) + `","instanceName":"node","instanceStatus":"running","regionID":"r1"}]}}`))
	}))
	defer server.Close()
	keys, err := security.LoadKeyring("missing", "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=", "fallback")
	if err != nil {
		t.Fatal(err)
	}
	ak, _ := keys.EncryptString("ak")
	sk, _ := keys.EncryptString("sk")
	store := &memorySyncStore{account: storage.AccountRecord{Account: storage.Account{ID: 2, Region: "r1", AKEncrypted: ak}, SKEncrypted: sk}}
	syncer := Syncer{Config: config.Config{RegionEndpoint: server.URL, RegionListPath: "/regions", ECSEndpoint: server.URL, ECSListPath: "/ecs", OpenAPITimeout: time.Second}, Keys: keys, Store: store}
	count, err := syncer.SyncKind(context.Background(), store.account, "ecs", nil)
	if err != nil {
		t.Fatal(err)
	}
	if count != 2 || store.kind != "ecs" || len(store.writes) != 2 || store.writes[0].Region != "r1" {
		t.Fatalf("count=%d store=%#v", count, store)
	}
}

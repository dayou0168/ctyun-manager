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

func testActionService(t *testing.T, handler http.HandlerFunc) (Service, storage.AccountRecord, func()) {
	t.Helper()
	server := httptest.NewServer(handler)
	keys, err := security.LoadKeyring("missing", "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=", "fallback")
	if err != nil {
		t.Fatal(err)
	}
	ak, _ := keys.EncryptString("ak")
	sk, _ := keys.EncryptString("sk")
	cfg := config.Config{ECSEndpoint: server.URL, EIPEndpoint: server.URL, VPCEndpoint: server.URL, SubnetEndpoint: server.URL, VIPEndpoint: server.URL, IMSEndpoint: server.URL, OpenAPITimeout: time.Second}
	return Service{Config: cfg, Keys: keys}, storage.AccountRecord{Account: storage.Account{Region: "r1", AKEncrypted: ak}, SKEncrypted: sk}, server.Close
}

func TestActionStartECS(t *testing.T) {
	service, account, closeFn := testActionService(t, func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v4/ecs/start-instance" {
			t.Errorf("path=%s", r.URL.Path)
		}
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		if body["regionID"] != "r1" || body["instanceID"] != "i-1" {
			t.Errorf("body=%#v", body)
		}
		_, _ = w.Write([]byte(`{"statusCode":800,"returnObj":{"jobID":"j1"}}`))
	})
	defer closeFn()
	if _, err := service.Action(context.Background(), account, "ecs", "start", map[string]any{"instanceID": "i-1"}); err != nil {
		t.Fatal(err)
	}
}

func TestActionRejectsIncompleteCreateBeforeRequest(t *testing.T) {
	requests := 0
	service, account, closeFn := testActionService(t, func(w http.ResponseWriter, r *http.Request) { requests++; w.WriteHeader(500) })
	defer closeFn()
	if _, err := service.Action(context.Background(), account, "vpc", "create", map[string]any{"name": "x"}); err == nil {
		t.Fatal("expected validation error")
	}
	if requests != 0 {
		t.Fatalf("unexpected requests=%d", requests)
	}
}

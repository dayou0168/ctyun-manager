package httpserver

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/dayou0168/ctyun-manager/internal/config"
	"github.com/dayou0168/ctyun-manager/internal/security"
	"github.com/dayou0168/ctyun-manager/internal/storage"
)

type fakeStore struct {
	user       storage.User
	accounts   []storage.Account
	finance    []storage.Finance
	resources  []storage.Resource
	operations []storage.Operation
	summary    storage.DashboardSummary
	err        error
}

func (f *fakeStore) Ping(context.Context) error { return f.err }
func (f *fakeStore) UserByUsername(_ context.Context, username string) (storage.User, error) {
	if f.err != nil {
		return storage.User{}, f.err
	}
	if username != f.user.Username {
		return storage.User{}, storage.ErrNotFound
	}
	return f.user, nil
}
func (f *fakeStore) Accounts(context.Context) ([]storage.Account, error) {
	return f.accounts, f.err
}
func (f *fakeStore) Finance(context.Context) ([]storage.Finance, error) {
	return f.finance, f.err
}
func (f *fakeStore) Resources(context.Context, string, *int64) ([]storage.Resource, error) {
	return f.resources, f.err
}
func (f *fakeStore) Operations(context.Context, int) ([]storage.Operation, error) {
	return f.operations, f.err
}
func (f *fakeStore) DashboardSummary(context.Context) (storage.DashboardSummary, error) {
	return f.summary, f.err
}
func (f *fakeStore) AccountByID(_ context.Context, id int64) (storage.AccountRecord, error) {
	for _, a := range f.accounts {
		if a.ID == id {
			return storage.AccountRecord{Account: a}, nil
		}
	}
	return storage.AccountRecord{}, storage.ErrNotFound
}

func TestCompatibilityEndpoints(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	staticDir := filepath.Join(root, "static")
	if err := os.Mkdir(staticDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(staticDir, "index.html"), []byte("go migration"), 0o644); err != nil {
		t.Fatal(err)
	}
	scriptPath := filepath.Join(root, "install-l2tp-server.sh")
	if err := os.WriteFile(scriptPath, []byte("#!/bin/sh\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	cfg := config.Config{StaticDir: staticDir, L2TPScriptPath: scriptPath, MasterKeyPath: filepath.Join(root, "master.key"), CTyunMode: "openapi"}
	handler := New(cfg, slog.New(slog.NewTextHandler(io.Discard, nil)), &fakeStore{}, nil).HTTPServer().Handler
	server := httptest.NewServer(handler)
	defer server.Close()

	for _, path := range []string{"/healthz", "/readyz", "/api/version", "/", "/install-l2tp-server.sh"} {
		response, err := http.Get(server.URL + path)
		if err != nil {
			t.Fatalf("GET %s: %v", path, err)
		}
		response.Body.Close()
		if response.StatusCode != http.StatusOK {
			t.Errorf("GET %s status = %d", path, response.StatusCode)
		}
	}
}

func TestVersionContract(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	cfg := config.Config{StaticDir: root, L2TPScriptPath: filepath.Join(root, "missing"), MasterKeyPath: filepath.Join(root, "master.key"), CTyunMode: "openapi"}
	handler := New(cfg, slog.New(slog.NewTextHandler(io.Discard, nil)), nil, nil).HTTPServer().Handler
	request := httptest.NewRequest(http.MethodGet, "/api/version", nil)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)

	var payload map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{"version", "build_time", "ctyun_mode", "encryption_key_status"} {
		if _, ok := payload[key]; !ok {
			t.Errorf("missing compatibility field %q", key)
		}
	}
}

func TestAuthenticationAndReadOnlyAccountsContract(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	keyPath := filepath.Join(root, "master.key")
	if err := os.WriteFile(keyPath, []byte("AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="), 0o600); err != nil {
		t.Fatal(err)
	}
	keys, err := security.LoadKeyring(keyPath, "", "test-session-secret")
	if err != nil {
		t.Fatal(err)
	}
	store := &fakeStore{
		user: storage.User{Username: "admin", PasswordHash: "AAECAwQFBgcICQoLDA0OD4MZX/ka/QubR2DXgcurVU0Iu2yoCNglI1wgyOlK21xo"},
		accounts: []storage.Account{{
			ID: 7, Name: "测试账号", ProviderAccountID: "provider-7", Region: "cn-huadong1",
			UsernameEncrypted: "gAAAAABlU_EASvjfAEgdY1TkAoQ_TPPtWM_rsbcy6MbSRJLfe7hMkYoZg2yWKNnn7A20cNmv-bgeB-hVHNxlMj-eoe5JIAmp2SDiDzGOHID701p3KTAZ9zo=",
			PasswordEncrypted: "present", Status: "enabled", CreatedAt: "2026-01-01", UpdatedAt: "2026-01-02",
		}},
	}
	cfg := config.Config{MasterKeyPath: keyPath, CTyunMode: "openapi", SessionSecret: "test-session-secret", PublicURL: "https://example.test"}
	handler := New(cfg, slog.New(slog.NewTextHandler(io.Discard, nil)), store, keys).HTTPServer().Handler

	unauthorized := httptest.NewRecorder()
	handler.ServeHTTP(unauthorized, httptest.NewRequest(http.MethodGet, "/api/me", nil))
	if unauthorized.Code != http.StatusUnauthorized || unauthorized.Body.String() != "{\"detail\":\"not_authenticated\"}\n" {
		t.Fatalf("unauthorized response = %d %s", unauthorized.Code, unauthorized.Body.String())
	}

	login := httptest.NewRecorder()
	loginRequest := httptest.NewRequest(http.MethodPost, "/api/login", bytes.NewBufferString(`{"username":"admin","password":"correct horse"}`))
	handler.ServeHTTP(login, loginRequest)
	if login.Code != http.StatusOK {
		t.Fatalf("login response = %d %s", login.Code, login.Body.String())
	}
	cookies := login.Result().Cookies()
	if len(cookies) != 1 || !cookies[0].HttpOnly || !cookies[0].Secure || cookies[0].SameSite != http.SameSiteLaxMode {
		t.Fatalf("unexpected session cookie: %#v", cookies)
	}

	me := httptest.NewRecorder()
	meRequest := httptest.NewRequest(http.MethodGet, "/api/me", nil)
	meRequest.AddCookie(cookies[0])
	handler.ServeHTTP(me, meRequest)
	if me.Code != http.StatusOK {
		t.Fatalf("me response = %d %s", me.Code, me.Body.String())
	}

	accounts := httptest.NewRecorder()
	accountsRequest := httptest.NewRequest(http.MethodGet, "/api/accounts", nil)
	accountsRequest.AddCookie(cookies[0])
	handler.ServeHTTP(accounts, accountsRequest)
	if accounts.Code != http.StatusOK {
		t.Fatalf("accounts response = %d %s", accounts.Code, accounts.Body.String())
	}
	var payload []map[string]any
	if err := json.Unmarshal(accounts.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if len(payload) != 1 || payload[0]["username_masked"] != "天翼***测试" || payload[0]["has_password"] != true {
		t.Fatalf("unexpected accounts payload: %#v", payload)
	}
}

func TestReadOnlyEndpointsContract(t *testing.T) {
	t.Parallel()
	available := 88.5
	financeStatus := "ok"
	operationAccountID := int64(7)
	store := &fakeStore{
		finance: []storage.Finance{{AccountID: 7, Available: &available, Status: &financeStatus}},
		resources: []storage.Resource{{
			ID: 9, AccountID: 7, ResourceType: "ecs", ProviderID: "ecs-9", Name: "node",
			Region: "cn-huadong1", Status: "running", BillingMode: "monthly",
			PayloadJSON: `{"cpu":2,"tags":["go"]}`, SyncedAt: "2026-09-03",
		}},
		operations: []storage.Operation{{
			ID: 11, AccountID: &operationAccountID, Action: "sync", Status: "success",
			Message: "done", CreatedAt: "2026-09-03",
		}},
		summary: storage.DashboardSummary{
			AccountCount: 1, ResourceCounts: map[string]int64{"ecs": 1},
			Finance: []storage.Finance{{AccountID: 7, Available: &available}},
		},
	}
	cfg := config.Config{SessionSecret: "contract-secret"}
	handler := New(cfg, slog.New(slog.NewTextHandler(io.Discard, nil)), store, nil).HTTPServer().Handler
	token, err := security.SignSession("admin", cfg.SessionSecret, time.Now())
	if err != nil {
		t.Fatal(err)
	}

	tests := []struct {
		path  string
		check func(*testing.T, any)
	}{
		{"/api/finance", func(t *testing.T, value any) {
			rows := value.([]any)
			if len(rows) != 1 || rows[0].(map[string]any)["available"] != 88.5 {
				t.Fatalf("unexpected finance: %#v", value)
			}
		}},
		{"/api/dashboard/summary", func(t *testing.T, value any) {
			row := value.(map[string]any)
			if row["account_count"] != float64(1) || row["resource_counts"].(map[string]any)["ecs"] != float64(1) {
				t.Fatalf("unexpected summary: %#v", value)
			}
		}},
		{"/api/resources/ecs?account_id=7", func(t *testing.T, value any) {
			row := value.([]any)[0].(map[string]any)
			if row["provider_id"] != "ecs-9" || row["payload"].(map[string]any)["cpu"] != float64(2) {
				t.Fatalf("unexpected resources: %#v", value)
			}
		}},
		{"/api/operations", func(t *testing.T, value any) {
			row := value.([]any)[0].(map[string]any)
			if row["action"] != "sync" || row["account_id"] != float64(7) {
				t.Fatalf("unexpected operations: %#v", value)
			}
		}},
		{"/api/runtime/status", func(t *testing.T, value any) {
			row := value.(map[string]any)
			if row["background_sync_enabled"] != false || row["browser"].(map[string]any)["sessions"] != float64(0) {
				t.Fatalf("unexpected runtime status: %#v", value)
			}
		}},
	}
	for _, test := range tests {
		t.Run(test.path, func(t *testing.T) {
			request := httptest.NewRequest(http.MethodGet, test.path, nil)
			request.AddCookie(&http.Cookie{Name: sessionCookie, Value: token})
			response := httptest.NewRecorder()
			handler.ServeHTTP(response, request)
			if response.Code != http.StatusOK {
				t.Fatalf("response = %d %s", response.Code, response.Body.String())
			}
			var payload any
			if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
				t.Fatal(err)
			}
			test.check(t, payload)
		})
	}
}

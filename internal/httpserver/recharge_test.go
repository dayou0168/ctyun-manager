package httpserver

import (
	"encoding/base64"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/dayou0168/ctyun-manager/internal/config"
	"github.com/dayou0168/ctyun-manager/internal/security"
	"github.com/dayou0168/ctyun-manager/internal/storage"
)

func TestNormalizeRechargeAmount(t *testing.T) {
	t.Parallel()
	tests := map[string]string{".5": "0.50", "0001.2": "1.20", "+9": "9.00", "99999999.99": "99999999.99"}
	for input, want := range tests {
		if got, ok := normalizeRechargeAmount(input); !ok || got != want {
			t.Errorf("normalizeRechargeAmount(%q) = %q, %v; want %q, true", input, got, ok, want)
		}
	}
	for _, input := range []string{"", "0", "-1", "1.001", "100000000", "NaN"} {
		if got, ok := normalizeRechargeAmount(input); ok {
			t.Errorf("normalizeRechargeAmount(%q) = %q, true; want rejection", input, got)
		}
	}
}

func TestRechargeRoutesForwardWithoutCallingOfficialCloud(t *testing.T) {
	t.Parallel()
	worker := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer worker-secret" {
			t.Fatalf("worker authorization = %q", r.Header.Get("Authorization"))
		}
		var input map[string]any
		if err := json.NewDecoder(r.Body).Decode(&input); err != nil {
			t.Fatal(err)
		}
		switch r.URL.Path {
		case "/v1/recharge/order":
			if input["amount"] != "1.20" || input["payment_method"] != "wechat" {
				t.Fatalf("unexpected order input: %#v", input)
			}
			writeJSON(w, 200, map[string]any{"status": "ready", "message": "ok", "order_no": "test-order", "amount": "1.20"})
		case "/v1/recharge/payment":
			writeJSON(w, 200, map[string]any{"status": "ready", "payment_method": input["payment_method"]})
		case "/v1/recharge/qr":
			writeJSON(w, 200, map[string]any{"status": "ready", "png_base64": base64.StdEncoding.EncodeToString([]byte("\x89PNG\r\n\x1a\nmock"))})
		case "/v1/recharge/refresh":
			writeJSON(w, 200, map[string]any{"status": "ready", "qr_remaining_seconds": 60})
		case "/v1/recharge/status":
			writeJSON(w, 200, map[string]any{"status": "pending", "trade_status": "NOTPAY"})
		default:
			http.NotFound(w, r)
		}
	}))
	defer worker.Close()

	root := t.TempDir()
	keyPath := filepath.Join(root, "master.key")
	if err := os.WriteFile(keyPath, []byte("AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="), 0o600); err != nil {
		t.Fatal(err)
	}
	keys, err := security.LoadKeyring(keyPath, "", "session-secret")
	if err != nil {
		t.Fatal(err)
	}
	cfg := config.Config{SessionSecret: "session-secret", BrowserWorkerURL: worker.URL, BrowserWorkerToken: "worker-secret"}
	store := &fakeStore{accounts: []storage.Account{{ID: 7, Name: "test", Status: "enabled"}}}
	handler := New(cfg, slog.New(slog.NewTextHandler(io.Discard, nil)), store, keys).HTTPServer().Handler
	token, err := security.SignSession("admin", cfg.SessionSecret, time.Now())
	if err != nil {
		t.Fatal(err)
	}

	request := func(method, path, requestBody string) *httptest.ResponseRecorder {
		req := httptest.NewRequest(method, path, strings.NewReader(requestBody))
		req.AddCookie(&http.Cookie{Name: sessionCookie, Value: token})
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, req)
		return response
	}

	checks := []struct{ method, path, body, contentType string }{
		{http.MethodPost, "/api/accounts/7/recharge/order", `{"amount":"1.2","payment_method":"wechat"}`, "application/json"},
		{http.MethodPost, "/api/accounts/7/recharge/payment", `{"payment_method":"alipay"}`, "application/json"},
		{http.MethodGet, "/api/accounts/7/recharge/qr", "", "image/png"},
		{http.MethodPost, "/api/accounts/7/recharge/qr/refresh", "", "application/json"},
		{http.MethodGet, "/api/accounts/7/recharge/status", "", "application/json"},
	}
	for _, check := range checks {
		response := request(check.method, check.path, check.body)
		if response.Code != http.StatusOK {
			t.Fatalf("%s %s = %d %s", check.method, check.path, response.Code, response.Body.String())
		}
		if !strings.HasPrefix(response.Header().Get("Content-Type"), check.contentType) {
			t.Errorf("%s Content-Type = %q", check.path, response.Header().Get("Content-Type"))
		}
	}

	invalid := request(http.MethodPost, "/api/accounts/7/recharge/order", `{"amount":"1.001","payment_method":"wechat"}`)
	if invalid.Code != http.StatusUnprocessableEntity {
		t.Fatalf("invalid amount status = %d", invalid.Code)
	}
}

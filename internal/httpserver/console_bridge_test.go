package httpserver

import (
	"archive/zip"
	"bytes"
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
)

func TestFilterCTyunStorageState(t *testing.T) {
	t.Parallel()
	state := map[string]any{
		"cookies": []any{
			map[string]any{"name": "keep", "domain": ".ctyun.cn"},
			map[string]any{"name": "keep-url", "url": "https://console.ctyun.cn/"},
			map[string]any{"name": "drop", "domain": ".example.com"},
		},
		"origins": []any{
			map[string]any{"origin": "https://www.ctyun.cn", "localStorage": []any{}},
			map[string]any{"origin": "https://example.com", "localStorage": []any{}},
		},
	}
	filtered := filterCTyunStorageState(state)
	if got := len(filtered["cookies"].([]any)); got != 2 {
		t.Fatalf("cookie count = %d, want 2", got)
	}
	if got := len(filtered["origins"].([]any)); got != 1 {
		t.Fatalf("origin count = %d, want 1", got)
	}
}

func TestIsCTyunHostRejectsLookalikes(t *testing.T) {
	t.Parallel()
	for _, host := range []string{"ctyun.cn.evil.example", "evilctyun.cn", "example.com"} {
		if isCTyunHost(host) {
			t.Fatalf("accepted lookalike host %q", host)
		}
	}
}

func TestConsoleBridgeZIPRequiresAuthenticationAndContainsExtension(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	extension := filepath.Join(root, "ctyun-console-bridge", "extension")
	if err := os.MkdirAll(extension, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(extension, "manifest.json"), []byte(`{"name":"bridge"}`), 0o644); err != nil {
		t.Fatal(err)
	}
	cfg := config.Config{StaticDir: root, SessionSecret: "bridge-session"}
	handler := New(cfg, slog.New(slog.NewTextHandler(io.Discard, nil)), &fakeStore{}, nil).HTTPServer().Handler

	unauthorized := httptest.NewRecorder()
	handler.ServeHTTP(unauthorized, httptest.NewRequest(http.MethodGet, "/ctyun-console-bridge.zip", nil))
	if unauthorized.Code != http.StatusUnauthorized {
		t.Fatalf("unauthorized status = %d", unauthorized.Code)
	}
	token, err := security.SignSession("admin", cfg.SessionSecret, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	req := httptest.NewRequest(http.MethodGet, "/ctyun-console-bridge.zip", nil)
	req.AddCookie(&http.Cookie{Name: sessionCookie, Value: token})
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, req)
	if response.Code != http.StatusOK || response.Header().Get("Content-Type") != "application/zip" {
		t.Fatalf("ZIP response = %d %q %s", response.Code, response.Header().Get("Content-Type"), response.Body.String())
	}
	archive, err := zip.NewReader(bytes.NewReader(response.Body.Bytes()), int64(response.Body.Len()))
	if err != nil {
		t.Fatal(err)
	}
	if len(archive.File) != 1 || archive.File[0].Name != "manifest.json" {
		t.Fatalf("unexpected ZIP entries: %#v", archive.File)
	}
}

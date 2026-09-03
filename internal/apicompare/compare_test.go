package apicompare

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestRunMatchesAndReportsNestedDifference(t *testing.T) {
	t.Parallel()
	python := comparisonServer(t, map[string]any{
		"/api/accounts": []any{map[string]any{"id": 1, "name": "same"}},
		"/api/finance":  map[string]any{"amount": 1.0, "state": "old"},
	})
	goServer := comparisonServer(t, map[string]any{
		"/api/accounts": []any{map[string]any{"id": 1.0, "name": "same"}},
		"/api/finance":  map[string]any{"amount": 1, "state": "new"},
	})
	report, err := Run(context.Background(), Config{
		PythonURL: python.URL, GoURL: goServer.URL, Username: "admin", Password: "secret",
		Endpoints: []string{"/api/accounts", "/api/finance"}, Timeout: time.Second,
	})
	if err != nil {
		t.Fatal(err)
	}
	if report.Matched != 1 || report.Different != 1 || report.Errors != 0 {
		t.Fatalf("unexpected summary: %#v", report)
	}
	differences := report.Results[1].Differences
	if len(differences) != 1 || differences[0].Path != "/state" || differences[0].Reason != "different value" {
		t.Fatalf("unexpected differences: %#v", differences)
	}
}

func TestRunRejectsWriteOrExternalEndpoints(t *testing.T) {
	t.Parallel()
	for _, endpoint := range []string{
		"/api/accounts/1/actions", "/api/accounts?delete=1", "https://example.test/api/accounts",
		"/api/resources/ecs/1", "/not-api",
	} {
		if err := ValidateEndpoint(endpoint); err == nil {
			t.Errorf("ValidateEndpoint(%q) unexpectedly succeeded", endpoint)
		}
	}
	for _, endpoint := range []string{"/api/accounts", "/api/resources/ecs", "/api/resources/ecs?account_id=7"} {
		if err := ValidateEndpoint(endpoint); err != nil {
			t.Errorf("ValidateEndpoint(%q): %v", endpoint, err)
		}
	}
}

func TestRunRequiresCredentialsBeforeRequests(t *testing.T) {
	t.Parallel()
	_, err := Run(context.Background(), Config{PythonURL: "http://127.0.0.1", GoURL: "http://127.0.0.1"})
	if err == nil {
		t.Fatal("missing credentials unexpectedly accepted")
	}
}

func TestRunTreatsMatchingServerErrorsAsErrors(t *testing.T) {
	t.Parallel()
	python := comparisonServer(t, map[string]any{"/api/accounts": responseValue{status: http.StatusInternalServerError, body: map[string]any{"detail": "internal_error"}}})
	goServer := comparisonServer(t, map[string]any{"/api/accounts": responseValue{status: http.StatusInternalServerError, body: map[string]any{"detail": "internal_error"}}})
	report, err := Run(context.Background(), Config{
		PythonURL: python.URL, GoURL: goServer.URL, Username: "admin", Password: "secret",
		Endpoints: []string{"/api/accounts"}, Timeout: time.Second,
	})
	if err != nil {
		t.Fatal(err)
	}
	if report.Errors != 1 || report.Results[0].Outcome != "error" {
		t.Fatalf("matching failures must not pass comparison: %#v", report)
	}
}

func TestNormalizeIgnoresExpectedBuildVersionDifferences(t *testing.T) {
	t.Parallel()
	version := cloneAndNormalize("/api/version", map[string]any{"version": "old", "ctyun_mode": "openapi"}).(map[string]any)
	if _, exists := version["version"]; exists {
		t.Fatal("/api/version retained a deployment-specific version")
	}
	me := cloneAndNormalize("/api/me", map[string]any{"username": "admin", "version": "old"}).(map[string]any)
	if _, exists := me["version"]; exists {
		t.Fatal("/api/me retained a deployment-specific version")
	}
}

type responseValue struct {
	status int
	body   any
}

func comparisonServer(t *testing.T, responses map[string]any) *httptest.Server {
	t.Helper()
	mux := http.NewServeMux()
	mux.HandleFunc("POST /api/login", func(w http.ResponseWriter, r *http.Request) {
		var body map[string]string
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil || body["username"] != "admin" || body["password"] != "secret" {
			http.Error(w, "bad credentials", http.StatusUnauthorized)
			return
		}
		http.SetCookie(w, &http.Cookie{Name: "ctyun_manager_session", Value: "test", Path: "/"})
		_ = json.NewEncoder(w).Encode(map[string]any{"ok": true})
	})
	for path, payload := range responses {
		payload := payload
		mux.HandleFunc("GET "+path, func(w http.ResponseWriter, r *http.Request) {
			if _, err := r.Cookie("ctyun_manager_session"); err != nil {
				http.Error(w, "unauthorized", http.StatusUnauthorized)
				return
			}
			status := http.StatusOK
			body := payload
			if response, ok := payload.(responseValue); ok {
				status = response.status
				body = response.body
			}
			w.WriteHeader(status)
			_ = json.NewEncoder(w).Encode(body)
		})
	}
	server := httptest.NewServer(mux)
	t.Cleanup(server.Close)
	return server
}

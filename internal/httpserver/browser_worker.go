package httpserver

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/dayou0168/ctyun-manager/internal/security"
	"github.com/dayou0168/ctyun-manager/internal/storage"
)

type browserStore interface {
	UpdateAccountBrowserState(context.Context, int64, string, string) error
	UpsertFinance(context.Context, int64, *float64, *float64, string, string) error
}

func (s *Server) accountBalance(w http.ResponseWriter, r *http.Request, _ security.Session) {
	id, account, ok := s.actionAccount(w, r)
	if !ok {
		return
	}
	result, err := s.callBrowserWorker(r.Context(), "/v1/finance", account, nil)
	if err != nil {
		writeDetail(w, 502, err.Error())
		return
	}
	s.persistBrowserResult(r.Context(), id, result, true)
	delete(result, "storage_state")
	writeJSON(w, 200, result)
}
func (s *Server) openRecharge(w http.ResponseWriter, r *http.Request, _ security.Session) {
	s.openBrowser(w, r, "recharge")
}
func (s *Server) openConsole(w http.ResponseWriter, r *http.Request, _ security.Session) {
	s.openBrowser(w, r, "console")
}
func (s *Server) openBrowser(w http.ResponseWriter, r *http.Request, target string) {
	id, account, ok := s.actionAccount(w, r)
	if !ok {
		return
	}
	result, err := s.callBrowserWorker(r.Context(), "/v1/open", account, map[string]any{"target": target})
	if err != nil {
		writeDetail(w, 502, err.Error())
		return
	}
	s.persistBrowserResult(r.Context(), id, result, false)
	delete(result, "storage_state")
	result["viewer_url"] = ""
	writeJSON(w, 200, result)
}
func (s *Server) closeBrowserSession(w http.ResponseWriter, r *http.Request, _ security.Session) {
	_, account, ok := s.actionAccount(w, r)
	if !ok {
		return
	}
	result, err := s.callBrowserWorker(r.Context(), "/v1/close", account, nil)
	if err != nil {
		writeDetail(w, 502, err.Error())
		return
	}
	writeJSON(w, 200, result)
}

func (s *Server) callBrowserWorker(ctx context.Context, path string, account storage.AccountRecord, extra map[string]any) (map[string]any, error) {
	if strings.TrimSpace(s.cfg.BrowserWorkerToken) == "" {
		return nil, errors.New("浏览器工作进程未配置")
	}
	decrypt := func(v string) string { x, _ := s.keys.DecryptString(v); return x }
	state := map[string]any{}
	if raw := decrypt(account.CookieEncrypted); raw != "" {
		_ = json.Unmarshal([]byte(raw), &state)
	}
	request := map[string]any{"account": map[string]any{"id": account.ID, "username": decrypt(account.UsernameEncrypted), "password": decrypt(account.PasswordEncrypted), "totp_secret": decrypt(account.TOTPEncrypted), "storage_state": state}}
	for k, v := range extra {
		request[k] = v
	}
	raw, _ := json.Marshal(request)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, strings.TrimRight(s.cfg.BrowserWorkerURL, "/")+path, bytes.NewReader(raw))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+s.cfg.BrowserWorkerToken)
	req.Header.Set("Content-Type", "application/json")
	client := &http.Client{Timeout: 2 * time.Minute}
	resp, err := client.Do(req)
	if err != nil {
		return nil, errors.New("无法连接 Node.js 浏览器工作进程: " + err.Error())
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
	if err != nil {
		return nil, err
	}
	var result map[string]any
	if json.Unmarshal(data, &result) != nil {
		return nil, errors.New("浏览器工作进程返回非 JSON")
	}
	if resp.StatusCode >= 400 {
		return nil, errors.New(firstText(result, "detail", "message"))
	}
	return result, nil
}
func (s *Server) persistBrowserResult(ctx context.Context, id int64, result map[string]any, finance bool) {
	store, ok := s.store.(browserStore)
	if !ok || s.cfg.DatabaseReadOnly {
		return
	}
	provider := firstText(result, "provider_account_id")
	if state, exists := result["storage_state"]; exists {
		if raw, err := json.Marshal(state); err == nil {
			if encrypted, err := s.keys.EncryptString(string(raw)); err == nil {
				_ = store.UpdateAccountBrowserState(ctx, id, encrypted, provider)
			}
		}
	}
	if finance {
		var available, owe *float64
		if v, ok := result["available"].(float64); ok {
			available = &v
		}
		if v, ok := result["owe"].(float64); ok {
			owe = &v
		}
		_ = store.UpsertFinance(ctx, id, available, owe, firstText(result, "status"), firstText(result, "message"))
	}
}

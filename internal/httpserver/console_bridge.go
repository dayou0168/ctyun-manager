package httpserver

import (
	"archive/zip"
	"bytes"
	"encoding/json"
	"errors"
	"io/fs"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/dayou0168/ctyun-manager/internal/security"
)

func (s *Server) consoleBridgeState(w http.ResponseWriter, r *http.Request, _ security.Session) {
	id, account, ok := s.actionAccount(w, r)
	if !ok {
		return
	}

	message := "已从保存的登录态生成本机浏览器控制台登录态"
	var state map[string]any
	if result, err := s.callBrowserWorker(r.Context(), "/v1/open", account, map[string]any{"target": "console"}); err == nil {
		s.persistBrowserResult(r.Context(), id, result, false)
		state, _ = result["storage_state"].(map[string]any)
		message = "已刷新并生成本机浏览器控制台登录态"
	} else {
		raw, decryptErr := s.keys.DecryptString(account.CookieEncrypted)
		if decryptErr != nil {
			writeDetail(w, http.StatusConflict, "该账号没有可导出的天翼云登录态，请先刷新余额或打开充值页面。")
			return
		}
		_ = json.Unmarshal([]byte(raw), &state)
		message += "；后台保活未确认，如果打开后是登录页，请刷新账号登录态"
	}

	filtered := filterCTyunStorageState(state)
	cookies, _ := filtered["cookies"].([]any)
	if len(cookies) == 0 {
		writeDetail(w, http.StatusConflict, "该账号没有可导出的天翼云登录态，请先刷新余额或打开充值页面。")
		return
	}
	origins, _ := filtered["origins"].([]any)
	writeJSON(w, http.StatusOK, map[string]any{
		"status":        "ready",
		"message":       message,
		"account_id":    id,
		"account_name":  account.Name,
		"target_url":    "https://console.ctyun.cn/console/index/#/console",
		"storage_state": filtered,
		"cookie_count":  len(cookies),
		"origin_count":  len(origins),
		"issued_at":     time.Now().Unix(),
	})
}

func filterCTyunStorageState(state map[string]any) map[string]any {
	result := map[string]any{"cookies": []any{}, "origins": []any{}}
	if state == nil {
		return result
	}
	for _, item := range asAnySlice(state["cookies"]) {
		cookie, ok := item.(map[string]any)
		if !ok {
			continue
		}
		domain, _ := cookie["domain"].(string)
		cookieURL, _ := cookie["url"].(string)
		if isCTyunHost(domain) || isCTyunURL(cookieURL) {
			result["cookies"] = append(result["cookies"].([]any), cookie)
		}
	}
	for _, item := range asAnySlice(state["origins"]) {
		origin, ok := item.(map[string]any)
		if !ok {
			continue
		}
		originURL, _ := origin["origin"].(string)
		if isCTyunURL(originURL) {
			result["origins"] = append(result["origins"].([]any), origin)
		}
	}
	return result
}

func asAnySlice(value any) []any {
	items, _ := value.([]any)
	return items
}

func isCTyunURL(raw string) bool {
	parsed, err := url.Parse(strings.TrimSpace(raw))
	return err == nil && isCTyunHost(parsed.Hostname())
}

func isCTyunHost(host string) bool {
	host = strings.TrimPrefix(strings.ToLower(strings.TrimSpace(host)), ".")
	return host == "ctyun.cn" || strings.HasSuffix(host, ".ctyun.cn")
}

func (s *Server) consoleBridgeZIP(w http.ResponseWriter, _ *http.Request, _ security.Session) {
	root := filepath.Join(s.cfg.StaticDir, "ctyun-console-bridge", "extension")
	info, err := os.Stat(root)
	if err != nil || !info.IsDir() {
		writeDetail(w, http.StatusNotFound, "console bridge extension not found")
		return
	}

	var buffer bytes.Buffer
	archive := zip.NewWriter(&buffer)
	err = filepath.WalkDir(root, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() {
			return nil
		}
		relative, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		if strings.HasPrefix(relative, "..") || filepath.IsAbs(relative) {
			return errors.New("invalid console bridge path")
		}
		contents, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		writer, err := archive.Create(filepath.ToSlash(relative))
		if err != nil {
			return err
		}
		_, err = writer.Write(contents)
		return err
	})
	if closeErr := archive.Close(); err == nil {
		err = closeErr
	}
	if err != nil {
		writeDetail(w, http.StatusInternalServerError, "could not build console bridge extension")
		return
	}

	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Type", "application/zip")
	w.Header().Set("Content-Disposition", `attachment; filename="ctyun-console-bridge.zip"`)
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(buffer.Bytes())
}

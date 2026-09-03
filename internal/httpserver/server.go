package httpserver

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/dayou0168/ctyun-manager/internal/buildinfo"
	"github.com/dayou0168/ctyun-manager/internal/config"
	"github.com/dayou0168/ctyun-manager/internal/ctyun"
	"github.com/dayou0168/ctyun-manager/internal/security"
	"github.com/dayou0168/ctyun-manager/internal/storage"
)

const (
	sessionCookie = "ctyun_manager_session"
	sessionMaxAge = 24 * time.Hour
)

type ReadStore interface {
	Ping(context.Context) error
	UserByUsername(context.Context, string) (storage.User, error)
	Accounts(context.Context) ([]storage.Account, error)
	Finance(context.Context) ([]storage.Finance, error)
	Resources(context.Context, string, *int64) ([]storage.Resource, error)
	Operations(context.Context, int) ([]storage.Operation, error)
	DashboardSummary(context.Context) (storage.DashboardSummary, error)
	AccountByID(context.Context, int64) (storage.AccountRecord, error)
}

type WriteStore interface {
	AccountByID(context.Context, int64) (storage.AccountRecord, error)
	CreateAccount(context.Context, storage.AccountWrite) (int64, error)
	UpdateAccount(context.Context, int64, storage.AccountWrite) error
	DeleteAccount(context.Context, int64) error
	ResourceByProvider(context.Context, int64, string, string) (storage.Resource, error)
	RecordOperation(context.Context, *int64, string, string, string, string, string) error
}

type Server struct {
	cfg     config.Config
	logger  *slog.Logger
	store   ReadStore
	keys    *security.Keyring
	syncMu  sync.Mutex
	syncing map[int64]bool
	http    *http.Server
}

func New(cfg config.Config, logger *slog.Logger, store ReadStore, keys *security.Keyring) *Server {
	if logger == nil {
		logger = slog.Default()
	}
	s := &Server{cfg: cfg, logger: logger, store: store, keys: keys, syncing: map[int64]bool{}}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", s.health)
	mux.HandleFunc("GET /readyz", s.ready)
	mux.HandleFunc("GET /api/version", s.version)
	mux.HandleFunc("POST /api/login", s.login)
	mux.HandleFunc("POST /api/logout", s.logout)
	mux.HandleFunc("GET /api/me", s.requireUser(s.me))
	mux.HandleFunc("GET /api/accounts", s.requireUser(s.accounts))
	mux.HandleFunc("POST /api/accounts", s.requireUser(s.createAccount))
	mux.HandleFunc("PUT /api/accounts/{account_id}", s.requireUser(s.updateAccount))
	mux.HandleFunc("DELETE /api/accounts/{account_id}", s.requireUser(s.deleteAccount))
	mux.HandleFunc("GET /api/accounts/{account_id}/regions", s.requireUser(s.accountRegions))
	mux.HandleFunc("POST /api/accounts/{account_id}/sync", s.requireUser(s.syncAccount))
	mux.HandleFunc("POST /api/accounts/{account_id}/sync-types", s.requireUser(s.syncAccountTypes))
	mux.HandleFunc("POST /api/accounts/{account_id}/prices/{resource_type}", s.requireUser(s.queryPrice))
	mux.HandleFunc("POST /api/accounts/{account_id}/actions", s.requireUser(s.runAction))
	mux.HandleFunc("POST /api/accounts/{account_id}/ecs/renew/price", s.requireUser(s.ecsRenewPrice))
	mux.HandleFunc("POST /api/accounts/{account_id}/ecs/renew/submit", s.requireUser(s.ecsRenewSubmit))
	mux.HandleFunc("POST /api/accounts/{account_id}/ecs/renew/order-status", s.requireUser(s.ecsRenewOrderStatus))
	mux.HandleFunc("POST /api/accounts/{account_id}/ecs/{resource_id}/remote-login", s.requireUser(s.ecsRemoteLogin))
	mux.HandleFunc("GET /api/accounts/{account_id}/totp", s.requireUser(s.accountTOTP))
	mux.HandleFunc("GET /api/accounts/{account_id}/balance", s.requireUser(s.accountBalance))
	mux.HandleFunc("GET /api/accounts/{account_id}/options/{kind}", s.requireUser(s.accountOptions))
	mux.HandleFunc("POST /api/accounts/{account_id}/options/prewarm", s.requireUser(s.prewarmOptions))
	mux.HandleFunc("POST /api/recharge/prewarm", s.requireUser(s.rechargePrewarm))
	mux.HandleFunc("POST /api/accounts/{account_id}/recharge/open", s.requireUser(s.openRecharge))
	mux.HandleFunc("POST /api/accounts/{account_id}/recharge/order", s.requireUser(s.rechargeOrder))
	mux.HandleFunc("POST /api/accounts/{account_id}/recharge/payment", s.requireUser(s.rechargePayment))
	mux.HandleFunc("GET /api/accounts/{account_id}/recharge/qr", s.requireUser(s.rechargeQR))
	mux.HandleFunc("POST /api/accounts/{account_id}/recharge/qr/refresh", s.requireUser(s.rechargeQRRefresh))
	mux.HandleFunc("GET /api/accounts/{account_id}/recharge/status", s.requireUser(s.rechargeStatus))
	mux.HandleFunc("POST /api/accounts/{account_id}/console/open", s.requireUser(s.openConsole))
	mux.HandleFunc("POST /api/accounts/{account_id}/console/bridge-state", s.requireUser(s.consoleBridgeState))
	mux.HandleFunc("POST /api/accounts/{account_id}/recharge/close", s.requireUser(s.closeBrowserSession))
	mux.HandleFunc("GET /api/finance", s.requireUser(s.finance))
	mux.HandleFunc("GET /api/dashboard/summary", s.requireUser(s.dashboardSummary))
	mux.HandleFunc("GET /api/resources/{resource_type}", s.requireUser(s.resources))
	mux.HandleFunc("GET /api/operations", s.requireUser(s.operations))
	mux.HandleFunc("GET /api/runtime/status", s.requireUser(s.runtimeStatus))
	mux.HandleFunc("GET /api/linux/servers", s.requireUser(s.listLinuxServers))
	mux.HandleFunc("POST /api/linux/servers", s.requireUser(s.createLinuxServer))
	mux.HandleFunc("PUT /api/linux/servers/{server_id}", s.requireUser(s.updateLinuxServer))
	mux.HandleFunc("DELETE /api/linux/servers/{server_id}", s.requireUser(s.deleteLinuxServer))
	mux.HandleFunc("POST /api/linux/servers/{server_id}/test", s.requireUser(s.testLinuxServer))
	mux.HandleFunc("POST /api/linux/servers/{server_id}/command", s.requireUser(s.linuxCommand))
	mux.HandleFunc("POST /api/linux/servers/{server_id}/files/list", s.requireUser(s.linuxFilesList))
	mux.HandleFunc("POST /api/linux/servers/{server_id}/files/read", s.requireUser(s.linuxFileRead))
	mux.HandleFunc("POST /api/linux/servers/{server_id}/files/write", s.requireUser(s.linuxFileWrite))
	mux.HandleFunc("GET /api/linux/servers/{server_id}/l2tp/config", s.requireUser(s.linuxL2TPConfig))
	mux.HandleFunc("POST /api/linux/servers/{server_id}/l2tp/config", s.requireUser(s.linuxL2TPSaveConfig))
	mux.HandleFunc("POST /api/linux/servers/{server_id}/l2tp/apply", s.requireUser(s.linuxL2TPApply))
	mux.HandleFunc("POST /api/linux/servers/{server_id}/l2tp/install", s.requireUser(s.linuxL2TPInstall))
	mux.HandleFunc("GET /api/linux/servers/{server_id}/l2tp/vips", s.requireUser(s.linuxL2TPVIPs))
	mux.HandleFunc("GET /api/linux/servers/{server_id}/ssh", s.linuxSSH)
	mux.HandleFunc("GET /install-l2tp-server.sh", s.l2tpScript)
	mux.HandleFunc("GET /ctyun-console-bridge.zip", s.requireUser(s.consoleBridgeZIP))
	mux.Handle("GET /static/", noCache(http.StripPrefix("/static/", http.FileServer(http.Dir(cfg.StaticDir)))))
	mux.HandleFunc("GET /", s.index)
	s.http = &http.Server{
		Addr:         cfg.Address,
		Handler:      recoverer(logger, requestLog(logger, mux)),
		ReadTimeout:  cfg.ReadTimeout,
		WriteTimeout: cfg.WriteTimeout,
		IdleTimeout:  cfg.IdleTimeout,
	}
	return s
}

func (s *Server) HTTPServer() *http.Server { return s.http }

func (s *Server) health(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{
		"status":          "ok",
		"service":         "ctyun-manager-go",
		"migration_phase": 6,
		"go_version":      runtime.Version(),
	})
}

func (s *Server) ready(w http.ResponseWriter, r *http.Request) {
	checks := map[string]string{}
	status := http.StatusOK
	for name, path := range map[string]string{
		"index":       filepath.Join(s.cfg.StaticDir, "index.html"),
		"l2tp_script": s.cfg.L2TPScriptPath,
	} {
		if info, err := os.Stat(path); err != nil || info.IsDir() {
			checks[name] = "missing"
			status = http.StatusServiceUnavailable
		} else {
			checks[name] = "ok"
		}
	}
	if s.store == nil {
		checks["database"] = "unavailable"
		status = http.StatusServiceUnavailable
	} else if err := s.store.Ping(r.Context()); err != nil {
		checks["database"] = "unavailable"
		status = http.StatusServiceUnavailable
	} else {
		checks["database"] = "ok"
	}
	writeJSON(w, status, map[string]any{
		"status": statusText(status),
		"checks": checks,
	})
}

func (s *Server) version(w http.ResponseWriter, _ *http.Request) {
	info := buildinfo.Current()
	keyStatus := "not_detected"
	if stat, err := os.Stat(s.cfg.MasterKeyPath); err == nil && !stat.IsDir() {
		keyStatus = "managed"
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"version":               info.Version,
		"build_time":            info.BuildTime,
		"commit":                info.Commit,
		"go_version":            info.GoVersion,
		"ctyun_mode":            s.cfg.CTyunMode,
		"encryption_key_status": keyStatus,
		"migration_phase":       6,
	})
}

func (s *Server) login(w http.ResponseWriter, r *http.Request) {
	if s.store == nil {
		writeDetail(w, http.StatusServiceUnavailable, "database_unavailable")
		return
	}
	var body struct {
		Username string `json:"username"`
		Password string `json:"password"`
	}
	decoder := json.NewDecoder(io.LimitReader(r.Body, 1<<20))
	if err := decoder.Decode(&body); err != nil {
		writeDetail(w, http.StatusUnprocessableEntity, "invalid_request")
		return
	}
	user, err := s.store.UserByUsername(r.Context(), body.Username)
	if err != nil && !errors.Is(err, storage.ErrNotFound) {
		s.logger.Error("login_user_query_failed", "error", err)
		writeDetail(w, http.StatusInternalServerError, "internal_error")
		return
	}
	if errors.Is(err, storage.ErrNotFound) || !security.VerifyPassword(body.Password, user.PasswordHash) {
		writeDetail(w, http.StatusUnauthorized, "bad_credentials")
		return
	}
	token, err := security.SignSession(body.Username, s.cfg.SessionSecret, time.Now())
	if err != nil {
		s.logger.Error("session_sign_failed", "error", err)
		writeDetail(w, http.StatusInternalServerError, "internal_error")
		return
	}
	http.SetCookie(w, &http.Cookie{
		Name:     sessionCookie,
		Value:    token,
		Path:     "/",
		MaxAge:   int(sessionMaxAge.Seconds()),
		HttpOnly: true,
		Secure:   strings.HasPrefix(strings.ToLower(s.cfg.PublicURL), "https://"),
		SameSite: http.SameSiteLaxMode,
	})
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "username": body.Username})
}

func (s *Server) logout(w http.ResponseWriter, _ *http.Request) {
	http.SetCookie(w, &http.Cookie{
		Name:     sessionCookie,
		Value:    "",
		Path:     "/",
		MaxAge:   -1,
		HttpOnly: true,
		Secure:   strings.HasPrefix(strings.ToLower(s.cfg.PublicURL), "https://"),
		SameSite: http.SameSiteLaxMode,
	})
	writeJSON(w, http.StatusOK, map[string]any{"ok": true})
}

func (s *Server) requireUser(next func(http.ResponseWriter, *http.Request, security.Session)) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		cookie, err := r.Cookie(sessionCookie)
		if err != nil {
			writeDetail(w, http.StatusUnauthorized, "not_authenticated")
			return
		}
		session, ok := security.VerifySession(cookie.Value, s.cfg.SessionSecret, time.Now(), sessionMaxAge)
		if !ok {
			writeDetail(w, http.StatusUnauthorized, "not_authenticated")
			return
		}
		next(w, r, session)
	}
}

func (s *Server) me(w http.ResponseWriter, _ *http.Request, session security.Session) {
	keyStatus := "not_detected"
	if stat, err := os.Stat(s.cfg.MasterKeyPath); err == nil && !stat.IsDir() {
		keyStatus = "managed"
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"username": session.Subject, "ctyun_mode": s.cfg.CTyunMode,
		"version": buildinfo.Current().Version, "encryption_key_status": keyStatus,
	})
}

func (s *Server) accounts(w http.ResponseWriter, r *http.Request, _ security.Session) {
	if s.store == nil {
		writeDetail(w, http.StatusServiceUnavailable, "database_unavailable")
		return
	}
	rows, err := s.store.Accounts(r.Context())
	if err != nil {
		s.logger.Error("accounts_query_failed", "error", err)
		writeDetail(w, http.StatusInternalServerError, "internal_error")
		return
	}
	result := make([]map[string]any, 0, len(rows))
	for _, row := range rows {
		result = append(result, s.publicAccount(row))
	}
	writeJSON(w, http.StatusOK, result)
}

type accountBody struct {
	Name     string `json:"name"`
	Region   string `json:"region"`
	Username string `json:"username"`
	Password string `json:"password"`
	TOTP     string `json:"totp_secret"`
	AK       string `json:"ak"`
	SK       string `json:"sk"`
	Notes    string `json:"notes"`
}

func (s *Server) createAccount(w http.ResponseWriter, r *http.Request, _ security.Session) {
	writer, ok := s.store.(WriteStore)
	if !ok {
		writeDetail(w, http.StatusServiceUnavailable, "database_read_only")
		return
	}
	var body accountBody
	if json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&body) != nil {
		writeDetail(w, 422, "invalid_request")
		return
	}
	missing := []string{}
	for _, field := range []struct{ label, value string }{{"官网登录账号", body.Username}, {"官网登录密码", body.Password}, {"Google 2FA", body.TOTP}, {"AccessKey", body.AK}, {"SecretKey", body.SK}} {
		if strings.TrimSpace(field.value) == "" {
			missing = append(missing, field.label)
		}
	}
	if len(missing) > 0 {
		writeDetail(w, 422, "请一次性填写："+strings.Join(missing, "、"))
		return
	}
	totp, err := security.NormalizeTOTP(body.TOTP)
	if err != nil {
		writeDetail(w, 422, "Google 2FA 密钥或 otpauth URI 格式无效")
		return
	}
	value, err := s.encryptedAccount(body, totp, storage.AccountRecord{})
	if err != nil {
		writeDetail(w, 500, "internal_error")
		return
	}
	id, err := writer.CreateAccount(r.Context(), value)
	if err != nil {
		s.internalStoreError(w, "account_create_failed", err)
		return
	}
	row, err := writer.AccountByID(r.Context(), id)
	if err != nil {
		s.internalStoreError(w, "account_read_after_create_failed", err)
		return
	}
	writeJSON(w, http.StatusOK, s.publicAccount(row.Account))
}

func (s *Server) updateAccount(w http.ResponseWriter, r *http.Request, _ security.Session) {
	writer, ok := s.store.(WriteStore)
	if !ok {
		writeDetail(w, 503, "database_read_only")
		return
	}
	id, err := strconv.ParseInt(r.PathValue("account_id"), 10, 64)
	if err != nil {
		writeDetail(w, 422, "invalid_request")
		return
	}
	current, err := writer.AccountByID(r.Context(), id)
	if errors.Is(err, storage.ErrNotFound) {
		writeDetail(w, 404, "account_not_found")
		return
	}
	if err != nil {
		s.internalStoreError(w, "account_query_failed", err)
		return
	}
	var body accountBody
	if json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&body) != nil {
		writeDetail(w, 422, "invalid_request")
		return
	}
	totp := ""
	if body.TOTP != "" {
		totp, err = security.NormalizeTOTP(body.TOTP)
		if err != nil {
			writeDetail(w, 422, "Google 2FA 密钥或 otpauth URI 格式无效")
			return
		}
	}
	value, err := s.encryptedAccount(body, totp, current)
	if err != nil {
		writeDetail(w, 500, "internal_error")
		return
	}
	if err = writer.UpdateAccount(r.Context(), id, value); err != nil {
		s.internalStoreError(w, "account_update_failed", err)
		return
	}
	row, _ := writer.AccountByID(r.Context(), id)
	writeJSON(w, 200, s.publicAccount(row.Account))
}

func (s *Server) deleteAccount(w http.ResponseWriter, r *http.Request, _ security.Session) {
	writer, ok := s.store.(WriteStore)
	if !ok {
		writeDetail(w, 503, "database_read_only")
		return
	}
	id, err := strconv.ParseInt(r.PathValue("account_id"), 10, 64)
	if err != nil {
		writeDetail(w, 422, "invalid_request")
		return
	}
	err = writer.DeleteAccount(r.Context(), id)
	if errors.Is(err, storage.ErrNotFound) {
		writeDetail(w, 404, "account_not_found")
		return
	}
	if err != nil {
		s.internalStoreError(w, "account_delete_failed", err)
		return
	}
	writeJSON(w, 200, map[string]any{"ok": true})
}

func (s *Server) accountRegions(w http.ResponseWriter, r *http.Request, _ security.Session) {
	if !s.requireStore(w) {
		return
	}
	id, err := strconv.ParseInt(r.PathValue("account_id"), 10, 64)
	if err != nil {
		writeDetail(w, 422, "invalid_request")
		return
	}
	account, err := s.store.AccountByID(r.Context(), id)
	if errors.Is(err, storage.ErrNotFound) {
		writeDetail(w, 404, "account_not_found")
		return
	}
	if err != nil {
		s.internalStoreError(w, "account_query_failed", err)
		return
	}
	ak, err := s.keys.DecryptString(account.AKEncrypted)
	if err != nil {
		writeDetail(w, 501, "该账号缺少 AK/SK，无法调用正式 OpenAPI。")
		return
	}
	sk, err := s.keys.DecryptString(account.SKEncrypted)
	if err != nil || ak == "" || sk == "" {
		writeDetail(w, 501, "该账号缺少 AK/SK，无法调用正式 OpenAPI。")
		return
	}
	client := ctyun.New(ak, sk, s.cfg.OpenAPITimeout)
	regions, err := client.ListRegions(r.Context(), s.cfg.RegionEndpoint, s.cfg.RegionListPath)
	if err != nil {
		s.logger.Error("ctyun_regions_failed", "account_id", id, "error", err)
		writeDetail(w, 501, err.Error())
		return
	}
	writeJSON(w, 200, regions)
}

func (s *Server) syncAccount(w http.ResponseWriter, r *http.Request, _ security.Session) {
	s.runSync(w, r, ctyun.SyncTypes, nil, true)
}
func (s *Server) syncAccountTypes(w http.ResponseWriter, r *http.Request, _ security.Session) {
	var body struct {
		Types     []string `json:"types"`
		RegionIDs []string `json:"region_ids"`
	}
	if json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&body) != nil {
		writeDetail(w, 422, "invalid_request")
		return
	}
	s.runSync(w, r, body.Types, body.RegionIDs, false)
}
func (s *Server) runSync(w http.ResponseWriter, r *http.Request, kinds, regions []string, full bool) {
	if s.cfg.DatabaseReadOnly {
		writeDetail(w, 503, "database_read_only")
		return
	}
	store, ok := s.store.(ctyun.SyncStore)
	if !ok {
		writeDetail(w, 503, "database_unavailable")
		return
	}
	id, err := strconv.ParseInt(r.PathValue("account_id"), 10, 64)
	if err != nil {
		writeDetail(w, 422, "invalid_request")
		return
	}
	s.syncMu.Lock()
	if s.syncing[id] {
		s.syncMu.Unlock()
		writeDetail(w, 409, "该账号已有同步任务正在运行，请稍后再试。")
		return
	}
	s.syncing[id] = true
	s.syncMu.Unlock()
	defer func() { s.syncMu.Lock(); delete(s.syncing, id); s.syncMu.Unlock() }()
	syncer := ctyun.Syncer{Config: s.cfg, Keys: s.keys, Store: store}
	result, err := syncer.Sync(r.Context(), id, kinds, regions)
	if errors.Is(err, storage.ErrNotFound) {
		writeDetail(w, 404, "account_not_found")
		return
	}
	if err != nil {
		s.logger.Error("ctyun_sync_failed", "account_id", id, "error", err)
		writeDetail(w, 501, err.Error())
		return
	}
	if full && len(result.Errors) > 0 {
		total := 0
		for _, count := range result.Counts {
			total += count
		}
		if total == 0 {
			keys := make([]string, 0, len(result.Errors))
			for key := range result.Errors {
				keys = append(keys, key)
			}
			sort.Strings(keys)
			messages := []string{}
			for _, key := range keys {
				messages = append(messages, key+": "+result.Errors[key])
			}
			writeDetail(w, 501, "同步完成但未发现资源；接口错误："+strings.Join(messages, "；"))
			return
		}
	}
	writeJSON(w, 200, result)
}

func (s *Server) encryptedAccount(body accountBody, normalizedTOTP string, current storage.AccountRecord) (storage.AccountWrite, error) {
	value := storage.AccountWrite{Name: body.Name, Region: body.Region, Notes: body.Notes, UsernameEncrypted: current.UsernameEncrypted, PasswordEncrypted: current.PasswordEncrypted, TOTPEncrypted: current.TOTPEncrypted, AKEncrypted: current.AKEncrypted, SKEncrypted: current.SKEncrypted, CookieEncrypted: current.CookieEncrypted}
	for _, field := range []struct {
		plain  string
		target *string
	}{{body.Username, &value.UsernameEncrypted}, {body.Password, &value.PasswordEncrypted}, {normalizedTOTP, &value.TOTPEncrypted}, {body.AK, &value.AKEncrypted}, {body.SK, &value.SKEncrypted}} {
		if field.plain != "" {
			encrypted, err := s.keys.EncryptString(field.plain)
			if err != nil {
				return storage.AccountWrite{}, err
			}
			*field.target = encrypted
		}
	}
	if body.Username != "" || body.Password != "" || body.TOTP != "" {
		value.CookieEncrypted = ""
	}
	return value, nil
}

func (s *Server) publicAccount(row storage.Account) map[string]any {
	username, ak := "", ""
	if s.keys != nil {
		username, _ = s.keys.DecryptString(row.UsernameEncrypted)
		ak, _ = s.keys.DecryptString(row.AKEncrypted)
	}
	return map[string]any{"id": row.ID, "name": row.Name, "provider_account_id": row.ProviderAccountID, "region": row.Region, "status": row.Status, "notes": row.Notes, "username_masked": security.Mask(username), "ak_masked": security.Mask(ak), "has_password": row.PasswordEncrypted != "", "has_totp": row.TOTPEncrypted != "", "has_cookie": row.CookieEncrypted != "", "created_at": row.CreatedAt, "updated_at": row.UpdatedAt}
}

func (s *Server) finance(w http.ResponseWriter, r *http.Request, _ security.Session) {
	if !s.requireStore(w) {
		return
	}
	rows, err := s.store.Finance(r.Context())
	if err != nil {
		s.internalStoreError(w, "finance_query_failed", err)
		return
	}
	writeJSON(w, http.StatusOK, rows)
}

func (s *Server) dashboardSummary(w http.ResponseWriter, r *http.Request, _ security.Session) {
	if !s.requireStore(w) {
		return
	}
	result, err := s.store.DashboardSummary(r.Context())
	if err != nil {
		s.internalStoreError(w, "dashboard_summary_query_failed", err)
		return
	}
	writeJSON(w, http.StatusOK, result)
}

func (s *Server) resources(w http.ResponseWriter, r *http.Request, _ security.Session) {
	if !s.requireStore(w) {
		return
	}
	var accountID *int64
	if raw := r.URL.Query().Get("account_id"); raw != "" {
		parsed, err := strconv.ParseInt(raw, 10, 64)
		if err != nil {
			writeDetail(w, http.StatusUnprocessableEntity, "invalid_request")
			return
		}
		// FastAPI's implementation treats zero as if the filter were omitted.
		if parsed != 0 {
			accountID = &parsed
		}
	}
	rows, err := s.store.Resources(r.Context(), r.PathValue("resource_type"), accountID)
	if err != nil {
		s.internalStoreError(w, "resources_query_failed", err)
		return
	}
	result := make([]map[string]any, 0, len(rows))
	for _, row := range rows {
		var payload any
		if err := json.Unmarshal([]byte(row.PayloadJSON), &payload); err != nil {
			s.logger.Error("resource_payload_decode_failed", "resource_id", row.ID, "error", err)
			writeDetail(w, http.StatusInternalServerError, "internal_error")
			return
		}
		result = append(result, map[string]any{
			"id": row.ID, "account_id": row.AccountID, "resource_type": row.ResourceType,
			"provider_id": row.ProviderID, "name": row.Name, "region": row.Region,
			"status": row.Status, "billing_mode": row.BillingMode, "payload": payload,
			"synced_at": row.SyncedAt,
		})
	}
	writeJSON(w, http.StatusOK, result)
}

func (s *Server) operations(w http.ResponseWriter, r *http.Request, _ security.Session) {
	if !s.requireStore(w) {
		return
	}
	rows, err := s.store.Operations(r.Context(), 200)
	if err != nil {
		s.internalStoreError(w, "operations_query_failed", err)
		return
	}
	writeJSON(w, http.StatusOK, rows)
}

func (s *Server) runtimeStatus(w http.ResponseWriter, _ *http.Request, _ security.Session) {
	writeJSON(w, http.StatusOK, map[string]any{
		"background_sync_enabled":  s.cfg.BackgroundSyncEnabled,
		"finance_refresh_enabled":  s.cfg.FinanceRefreshEnabled,
		"finance_refresh_queue":    0,
		"finance_refresh_pending":  0,
		"cookie_keepalive_enabled": s.cfg.CookieKeepaliveEnabled,
		"cookie_keepalive_queue":   0,
		"cookie_keepalive_pending": 0,
		"browser":                  map[string]any{"sessions": 0, "accounts": []any{}},
	})
}

func (s *Server) requireStore(w http.ResponseWriter) bool {
	if s.store != nil {
		return true
	}
	writeDetail(w, http.StatusServiceUnavailable, "database_unavailable")
	return false
}

func (s *Server) internalStoreError(w http.ResponseWriter, event string, err error) {
	s.logger.Error(event, "error", err)
	writeDetail(w, http.StatusInternalServerError, "internal_error")
}

func (s *Server) index(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/" {
		http.NotFound(w, r)
		return
	}
	setNoCache(w.Header())
	http.ServeFile(w, r, filepath.Join(s.cfg.StaticDir, "index.html"))
}

func (s *Server) l2tpScript(w http.ResponseWriter, r *http.Request) {
	file, err := os.Open(s.cfg.L2TPScriptPath)
	if err != nil {
		http.Error(w, "install-l2tp-server.sh not found", http.StatusNotFound)
		return
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		http.Error(w, "could not read install-l2tp-server.sh", http.StatusInternalServerError)
		return
	}
	setNoCache(w.Header())
	w.Header().Set("Content-Type", "text/x-shellscript; charset=utf-8")
	w.Header().Set("Content-Disposition", `attachment; filename="install-l2tp-server.sh"`)
	http.ServeContent(w, r, info.Name(), info.ModTime(), file)
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func writeDetail(w http.ResponseWriter, status int, detail string) {
	writeJSON(w, status, map[string]string{"detail": detail})
}

func statusText(status int) string {
	if status >= 200 && status < 300 {
		return "ok"
	}
	return "not_ready"
}

func setNoCache(headers http.Header) {
	headers.Set("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
	headers.Set("Pragma", "no-cache")
	headers.Set("Expires", "0")
}

func noCache(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		setNoCache(w.Header())
		next.ServeHTTP(w, r)
	})
}

func requestLog(logger *slog.Logger, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		next.ServeHTTP(w, r)
		logger.Info("http_request", "method", r.Method, "path", r.URL.Path, "duration_ms", time.Since(start).Milliseconds())
	})
}

func recoverer(logger *slog.Logger, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer func() {
			if recovered := recover(); recovered != nil {
				logger.Error("http_panic", "path", r.URL.Path, "error", fmt.Sprint(recovered))
				http.Error(w, "internal server error", http.StatusInternalServerError)
			}
		}()
		next.ServeHTTP(w, r)
	})
}

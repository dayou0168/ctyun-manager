package httpserver

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strconv"
	"time"

	"github.com/dayou0168/ctyun-manager/internal/ctyun"
	"github.com/dayou0168/ctyun-manager/internal/security"
	"github.com/dayou0168/ctyun-manager/internal/storage"
)

func (s *Server) queryPrice(w http.ResponseWriter, r *http.Request, _ security.Session) {
	id, account, ok := s.actionAccount(w, r)
	if !ok {
		return
	}
	var body struct {
		Payload map[string]any `json:"payload"`
	}
	if json.NewDecoder(io.LimitReader(r.Body, 2<<20)).Decode(&body) != nil {
		writeDetail(w, 422, "invalid_request")
		return
	}
	result, err := (ctyun.Service{Config: s.cfg, Keys: s.keys}).Price(r.Context(), account, r.PathValue("resource_type"), body.Payload)
	if err != nil {
		s.recordOperation(r, id, r.PathValue("resource_type"), "", "query_price", "failed", err.Error())
		writeDetail(w, 501, err.Error())
		return
	}
	s.recordOperation(r, id, r.PathValue("resource_type"), "", "query_price", "success", "")
	writeJSON(w, 200, result)
}

func (s *Server) runAction(w http.ResponseWriter, r *http.Request, _ security.Session) {
	id, account, ok := s.actionAccount(w, r)
	if !ok {
		return
	}
	writer, ok := s.store.(WriteStore)
	if !ok || s.cfg.DatabaseReadOnly {
		writeDetail(w, 503, "database_read_only")
		return
	}
	var body struct {
		ResourceType string         `json:"resource_type"`
		Action       string         `json:"action"`
		ResourceID   string         `json:"resource_id"`
		Payload      map[string]any `json:"payload"`
	}
	if json.NewDecoder(io.LimitReader(r.Body, 2<<20)).Decode(&body) != nil || body.ResourceType == "" || body.Action == "" {
		writeDetail(w, 422, "invalid_request")
		return
	}
	if body.Payload == nil {
		body.Payload = map[string]any{}
	}
	if body.ResourceID != "" {
		body.Payload["resource_id"] = body.ResourceID
		if cached, err := writer.ResourceByProvider(r.Context(), id, body.ResourceType, body.ResourceID); err == nil {
			var payload map[string]any
			if json.Unmarshal([]byte(cached.PayloadJSON), &payload) == nil {
				for k, v := range payload {
					if _, exists := body.Payload[k]; !exists {
						body.Payload[k] = v
					}
				}
				if _, exists := body.Payload["regionID"]; !exists {
					body.Payload["regionID"] = cached.Region
				}
			}
		}
	}
	result, err := (ctyun.Service{Config: s.cfg, Keys: s.keys}).Action(r.Context(), account, body.ResourceType, body.Action, body.Payload)
	if err != nil {
		s.recordOperation(r, id, body.ResourceType, body.ResourceID, body.Action, "failed", err.Error())
		writeDetail(w, 501, err.Error())
		return
	}
	raw, _ := json.Marshal(result)
	s.recordOperation(r, id, body.ResourceType, body.ResourceID, body.Action, "success", string(raw))
	result["post_sync"] = map[string]any{"queued": false, "message": "操作已提交，请刷新资源确认最终状态"}
	writeJSON(w, 200, result)
}

func (s *Server) accountTOTP(w http.ResponseWriter, r *http.Request, _ security.Session) {
	_, account, ok := s.actionAccount(w, r)
	if !ok {
		return
	}
	secret, err := s.keys.DecryptString(account.TOTPEncrypted)
	now := time.Now()
	remaining := 30 - int(now.Unix()%30)
	result := map[string]any{"code": "", "has_totp": secret != "", "period": 30, "remaining": remaining, "server_time": now.Unix(), "expires_at": now.Unix() + int64(remaining)}
	if err == nil && secret != "" {
		result["code"], err = security.CurrentTOTP(secret, now)
	}
	if err != nil {
		writeDetail(w, 500, "TOTP 密钥无法解密或格式无效")
		return
	}
	writeJSON(w, 200, result)
}

type renewBody struct {
	ResourceIDs []string `json:"resource_ids"`
	Month       int      `json:"month"`
	ByYear      bool     `json:"by_year"`
}

func (s *Server) ecsRenewPrice(w http.ResponseWriter, r *http.Request, _ security.Session) {
	s.handleRenew(w, r, "price")
}
func (s *Server) ecsRenewSubmit(w http.ResponseWriter, r *http.Request, _ security.Session) {
	s.handleRenew(w, r, "submit")
}
func (s *Server) handleRenew(w http.ResponseWriter, r *http.Request, action string) {
	id, a, ok := s.actionAccount(w, r)
	if !ok {
		return
	}
	var body renewBody
	if json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&body) != nil {
		writeDetail(w, 422, "invalid_request")
		return
	}
	service := ctyun.ConsoleService{Keys: s.keys}
	var result map[string]any
	var err error
	if action == "submit" {
		if s.cfg.DatabaseReadOnly {
			writeDetail(w, 503, "database_read_only")
			return
		}
		result, err = service.RenewSubmit(r.Context(), a, body.ResourceIDs, body.Month, body.ByYear)
	} else {
		result, err = service.RenewPrice(r.Context(), a, body.ResourceIDs, body.Month, body.ByYear)
	}
	if err != nil {
		s.recordOperation(r, id, "ecs", "", "renew_"+action, "failed", err.Error())
		writeDetail(w, 501, err.Error())
		return
	}
	s.recordOperation(r, id, "ecs", "", "renew_"+action, "success", "")
	writeJSON(w, 200, result)
}
func (s *Server) ecsRenewOrderStatus(w http.ResponseWriter, r *http.Request, _ security.Session) {
	_, a, ok := s.actionAccount(w, r)
	if !ok {
		return
	}
	var body struct {
		MasterOrderID string `json:"master_order_id"`
	}
	if json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&body) != nil {
		writeDetail(w, 422, "invalid_request")
		return
	}
	result, err := (ctyun.ConsoleService{Keys: s.keys}).RenewOrderStatus(r.Context(), a, body.MasterOrderID)
	if err != nil {
		writeDetail(w, 501, err.Error())
		return
	}
	writeJSON(w, 200, result)
}
func (s *Server) ecsRemoteLogin(w http.ResponseWriter, r *http.Request, _ security.Session) {
	id, a, ok := s.actionAccount(w, r)
	if !ok {
		return
	}
	writer, ok := s.store.(WriteStore)
	if !ok {
		writeDetail(w, 503, "database_unavailable")
		return
	}
	resourceID := r.PathValue("resource_id")
	row, err := writer.ResourceByProvider(r.Context(), id, "ecs", resourceID)
	if errors.Is(err, storage.ErrNotFound) {
		writeDetail(w, 404, "未找到已同步的云主机资源")
		return
	}
	payload := map[string]any{"resource_id": resourceID, "regionID": row.Region}
	_ = json.Unmarshal([]byte(row.PayloadJSON), &payload)
	payload["resource_id"] = resourceID
	if _, exists := payload["regionID"]; !exists {
		payload["regionID"] = row.Region
	}
	result, err := (ctyun.Service{Config: s.cfg, Keys: s.keys}).RemoteLogin(r.Context(), a, payload)
	if err != nil {
		writeDetail(w, 501, err.Error())
		return
	}
	writeJSON(w, 200, result)
}

func (s *Server) actionAccount(w http.ResponseWriter, r *http.Request) (int64, storage.AccountRecord, bool) {
	id, err := strconv.ParseInt(r.PathValue("account_id"), 10, 64)
	if err != nil {
		writeDetail(w, 422, "invalid_request")
		return 0, storage.AccountRecord{}, false
	}
	account, err := s.store.AccountByID(r.Context(), id)
	if errors.Is(err, storage.ErrNotFound) {
		writeDetail(w, 404, "account_not_found")
		return 0, storage.AccountRecord{}, false
	}
	if err != nil {
		s.internalStoreError(w, "account_query_failed", err)
		return 0, storage.AccountRecord{}, false
	}
	return id, account, true
}

func (s *Server) recordOperation(r *http.Request, accountID int64, kind, resourceID, action, status, message string) {
	if writer, ok := s.store.(WriteStore); ok {
		_ = writer.RecordOperation(r.Context(), &accountID, kind, resourceID, action, status, message)
	}
}

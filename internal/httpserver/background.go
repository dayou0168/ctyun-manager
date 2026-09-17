package httpserver

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/dayou0168/ctyun-manager/internal/ctyun"
	"github.com/dayou0168/ctyun-manager/internal/storage"
)

// StartBackground starts periodic, idempotent maintenance loops. Each pass uses a
// bounded context and existing account-level sync guards, so shutdown and overlap
// do not leave an unbounded in-memory queue behind.
func (s *Server) StartBackground(ctx context.Context) {
	if s.store == nil {
		return
	}
	if s.cfg.BackgroundSyncEnabled {
		go s.periodic(ctx, 30*time.Second, s.cfg.BackgroundSyncInterval, s.backgroundSyncOnce)
	}
	if s.cfg.FinanceRefreshEnabled && strings.TrimSpace(s.cfg.BrowserWorkerToken) != "" {
		go s.periodic(ctx, 15*time.Second, s.cfg.FinanceRefreshInterval, s.backgroundFinanceOnce)
	}
	if s.cfg.CookieKeepaliveEnabled && strings.TrimSpace(s.cfg.BrowserWorkerToken) != "" {
		go s.periodic(ctx, 60*time.Second, s.cfg.CookieKeepaliveInterval, s.backgroundKeepaliveOnce)
	}
	if _, ok := s.store.(WriteStore); ok && !s.cfg.DatabaseReadOnly {
		go s.periodic(ctx, 3*time.Second, 3*time.Second, s.backgroundActionJobsOnce)
	}
}

func (s *Server) periodic(ctx context.Context, initial, interval time.Duration, action func(context.Context)) {
	if interval <= 0 {
		return
	}
	timer := time.NewTimer(initial)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return
	case <-timer.C:
	}
	for {
		action(ctx)
		timer.Reset(interval)
		select {
		case <-ctx.Done():
			return
		case <-timer.C:
		}
	}
}

func (s *Server) enabledAccountRecords(ctx context.Context) []storage.AccountRecord {
	rows, err := s.store.Accounts(ctx)
	if err != nil {
		s.logger.Error("background_accounts_failed", "error", err)
		return nil
	}
	result := make([]storage.AccountRecord, 0, len(rows))
	for _, row := range rows {
		if row.Status != "" && !strings.EqualFold(row.Status, "enabled") {
			continue
		}
		record, err := s.store.AccountByID(ctx, row.ID)
		if err == nil {
			result = append(result, record)
		}
	}
	return result
}

func (s *Server) backgroundSyncOnce(parent context.Context) {
	store, ok := s.store.(ctyun.SyncStore)
	if !ok || s.cfg.DatabaseReadOnly {
		return
	}
	for _, account := range s.enabledAccountRecords(parent) {
		select {
		case <-parent.Done():
			return
		default:
		}
		s.syncMu.Lock()
		if s.syncing[account.ID] {
			s.syncMu.Unlock()
			continue
		}
		s.syncing[account.ID] = true
		s.syncMu.Unlock()
		ctx, cancel := context.WithTimeout(parent, 15*time.Minute)
		syncer := &ctyun.Syncer{Config: s.cfg, Keys: s.keys, Store: store}
		_, err := syncer.Sync(ctx, account.ID, ctyun.SyncTypes, nil)
		cancel()
		s.syncMu.Lock()
		delete(s.syncing, account.ID)
		s.syncMu.Unlock()
		if err != nil {
			s.logger.Error("background_sync_failed", "account_id", account.ID, "error", err)
		}
	}
}

func (s *Server) backgroundFinanceOnce(parent context.Context) {
	for _, account := range s.enabledAccountRecords(parent) {
		ctx, cancel := context.WithTimeout(parent, 90*time.Second)
		result, err := s.callBrowserWorker(ctx, "/v1/finance", account, nil)
		if err == nil {
			s.persistBrowserResult(ctx, account.ID, result, true)
		} else {
			s.logger.Warn("background_finance_failed", "account_id", account.ID, "error", err)
		}
		cancel()
	}
}

func (s *Server) backgroundKeepaliveOnce(parent context.Context) {
	for _, account := range s.enabledAccountRecords(parent) {
		ctx, cancel := context.WithTimeout(parent, 90*time.Second)
		result, err := s.callBrowserWorker(ctx, "/v1/prewarm", account, nil)
		if err == nil {
			s.persistBrowserResult(ctx, account.ID, result, false)
		} else {
			s.logger.Warn("background_cookie_keepalive_failed", "account_id", account.ID, "error", err)
		}
		cancel()
	}
}

func actionSyncTypes(resourceType, action string) []string {
	switch resourceType {
	case "ecs":
		if action == "create_image" {
			return []string{"image"}
		}
		if action == "change_private_ip" || action == "change_vpc" {
			return []string{"ecs", "vpc", "subnet", "security_group", "vip"}
		}
		return []string{"ecs"}
	case "eip":
		return []string{"eip", "ecs", "vip"}
	case "vip":
		return []string{"vip", "ecs", "eip"}
	case "vpc":
		if action == "create_subnet" {
			return []string{"vpc", "subnet"}
		}
		return []string{"vpc", "subnet", "vip", "security_group", "route_table", "acl"}
	case "subnet":
		return []string{"vpc", "subnet", "vip", "route_table", "acl"}
	case "security_group":
		return []string{"vpc", "subnet", "vip", "security_group"}
	default:
		return []string{resourceType}
	}
}

func actionTargetType(job storage.ActionJob) string {
	if job.ResourceType == "vpc" && job.Action == "create_subnet" {
		return "subnet"
	}
	if job.ResourceType == "ecs" && job.Action == "create_image" {
		return "image"
	}
	return job.ResourceType
}

func (s *Server) backgroundActionJobsOnce(parent context.Context) {
	writer, ok := s.store.(WriteStore)
	if !ok {
		return
	}
	syncStore, ok := s.store.(ctyun.SyncStore)
	if !ok {
		return
	}
	jobs, err := writer.DueActionJobs(parent, time.Now().Unix(), 50)
	if err != nil {
		s.logger.Error("resource_action_jobs_query_failed", "error", err)
		return
	}
	for _, job := range jobs {
		select {
		case <-parent.Done():
			return
		default:
		}
		s.syncMu.Lock()
		if s.syncing[job.AccountID] {
			s.syncMu.Unlock()
			continue
		}
		s.syncing[job.AccountID] = true
		s.syncMu.Unlock()
		attempt := job.Attempts + 1
		_ = writer.UpdateActionJob(parent, job.ID, "retrying", job.LastError, attempt, time.Now().Add(5*time.Minute).Unix())
		ctx, cancel := context.WithTimeout(parent, 3*time.Minute)
		regions := []string{}
		if strings.TrimSpace(job.Region) != "" {
			regions = []string{job.Region}
		}
		result, syncErr := (&ctyun.Syncer{Config: s.cfg, Keys: s.keys, Store: syncStore}).Sync(ctx, job.AccountID, actionSyncTypes(job.ResourceType, job.Action), regions)
		confirmed, reason := false, ""
		if syncErr == nil {
			targetType := actionTargetType(job)
			if typeError := result.Errors[targetType]; typeError != "" {
				syncErr = fmt.Errorf("%s 同步失败: %s", targetType, typeError)
			} else {
				confirmed, reason = confirmActionJob(ctx, writer, job)
			}
		}
		cancel()
		s.syncMu.Lock()
		delete(s.syncing, job.AccountID)
		s.syncMu.Unlock()
		if confirmed {
			_ = writer.UpdateActionJob(parent, job.ID, "completed", "", attempt, 0)
			_ = writer.RecordOperation(parent, &job.AccountID, job.ResourceType, job.ResourceID, "post_action_sync", "success", reason)
			continue
		}
		message := reason
		if syncErr != nil {
			message = syncErr.Error()
		}
		if attempt >= 9 {
			_ = writer.UpdateActionJob(parent, job.ID, "unconfirmed", message, attempt, 0)
			_ = writer.RecordOperation(parent, &job.AccountID, job.ResourceType, job.ResourceID, "post_action_sync", "unconfirmed", message)
			continue
		}
		delays := []time.Duration{2, 5, 10, 20, 40, 80, 150, 300, 600}
		delay := delays[min(attempt-1, len(delays)-1)] * time.Second
		_ = writer.UpdateActionJob(parent, job.ID, "retrying", message, attempt, time.Now().Add(delay).Unix())
	}
}

func confirmActionJob(ctx context.Context, store WriteStore, job storage.ActionJob) (bool, string) {
	targetType := actionTargetType(job)
	deleteLike := job.Action == "delete" || job.Action == "release" || job.Action == "unsubscribe" || job.Action == "reject"
	if job.ResourceID != "" {
		row, err := store.ResourceByProvider(ctx, job.AccountID, targetType, job.ResourceID)
		if errors.Is(err, storage.ErrNotFound) {
			if deleteLike {
				return true, "官方资源列表已无该资源"
			}
			return false, "官方资源列表暂未找到目标资源"
		}
		if err != nil {
			return false, err.Error()
		}
		if deleteLike {
			return false, "官方资源列表仍存在该资源"
		}
		status := strings.ToLower(row.Status)
		switch job.Action {
		case "start", "reboot":
			return strings.Contains(status, "running") || strings.Contains(status, "active"), "官方状态已更新为运行中"
		case "stop":
			return strings.Contains(status, "stop") || strings.Contains(status, "shut") || strings.Contains(status, "closed"), "官方状态已更新为已关机"
		case "bind", "unbind":
			var payload map[string]any
			_ = json.Unmarshal([]byte(row.PayloadJSON), &payload)
			binding := strings.ToLower(fmt.Sprint(payload["binding_status"]))
			if job.Action == "bind" {
				return binding == "bound", "官方列表已确认绑定"
			}
			return binding == "unbound", "官方列表已确认解绑"
		default:
			return row.SyncState == "fresh", "官方资源列表已重新同步"
		}
	}
	var payload map[string]any
	_ = json.Unmarshal([]byte(job.PayloadJSON), &payload)
	name := firstText(payload, "name", "displayName", "instanceName", "eipName", "imageName")
	rows, err := store.Resources(ctx, targetType, &job.AccountID)
	if err != nil {
		return false, err.Error()
	}
	for _, row := range rows {
		if row.SyncState != "fresh" || (job.Region != "" && row.Region != job.Region) {
			continue
		}
		if name == "" || row.Name == name {
			return true, "官方资源列表已出现目标资源"
		}
	}
	return false, "官方资源列表暂未出现目标资源"
}

package httpserver

import (
	"context"
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

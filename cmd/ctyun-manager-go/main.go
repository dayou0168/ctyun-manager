package main

import (
	"context"
	"log/slog"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/dayou0168/ctyun-manager/internal/config"
	"github.com/dayou0168/ctyun-manager/internal/httpserver"
	"github.com/dayou0168/ctyun-manager/internal/security"
	"github.com/dayou0168/ctyun-manager/internal/storage"
)

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	cfg, err := config.Load()
	if err != nil {
		logger.Error("invalid_configuration", "error", err)
		os.Exit(1)
	}
	keys, err := security.LoadKeyring(cfg.MasterKeyPath, cfg.ConfiguredKey, cfg.SessionSecret)
	if err != nil {
		logger.Error("encryption_keyring_unavailable", "error", err)
		os.Exit(1)
	}
	var store *storage.Store
	if cfg.DatabaseReadOnly {
		store, err = storage.OpenReadOnly(cfg.DatabasePath)
	} else {
		store, err = storage.OpenReadWrite(cfg.DatabasePath)
	}
	var readStore httpserver.ReadStore
	if err != nil {
		logger.Warn("database_read_only_open_failed", "error", err)
	} else {
		defer store.Close()
		readStore = store
	}
	application := httpserver.New(cfg, logger, readStore, keys)
	server := application.HTTPServer()

	shutdownSignals, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	application.StartBackground(shutdownSignals)
	go func() {
		<-shutdownSignals.Done()
		ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		defer cancel()
		if err := server.Shutdown(ctx); err != nil {
			logger.Error("graceful_shutdown_failed", "error", err)
		}
	}()

	logger.Info("server_starting", "address", cfg.Address, "static_dir", cfg.StaticDir, "database", cfg.DatabasePath)
	if err := server.ListenAndServe(); err != nil && err.Error() != "http: Server closed" {
		logger.Error("server_stopped", "error", err)
		os.Exit(1)
	}
}

package config

import (
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

type Config struct {
	Address                 string
	StaticDir               string
	DatabasePath            string
	MasterKeyPath           string
	L2TPScriptPath          string
	CTyunMode               string
	SessionSecret           string
	ConfiguredKey           string
	PublicURL               string
	BackgroundSyncEnabled   bool
	BackgroundSyncInterval  time.Duration
	FinanceRefreshEnabled   bool
	FinanceRefreshInterval  time.Duration
	CookieKeepaliveEnabled  bool
	CookieKeepaliveInterval time.Duration
	RechargePrewarmEnabled  bool
	DatabaseReadOnly        bool
	RegionEndpoint          string
	RegionListPath          string
	OpenAPITimeout          time.Duration
	DefaultRegionIDs        string
	ECSEndpoint             string
	ECSListPath             string
	EIPEndpoint             string
	EIPListPath             string
	VPCEndpoint             string
	VPCListPath             string
	SubnetEndpoint          string
	SubnetListPath          string
	VIPEndpoint             string
	VIPListPath             string
	IMSEndpoint             string
	IMSListPath             string
	BrowserWorkerURL        string
	BrowserWorkerToken      string
	ReadTimeout             time.Duration
	WriteTimeout            time.Duration
	IdleTimeout             time.Duration
}

func Load() (Config, error) {
	port, err := envPort("CTYUN_MANAGER_GO_PORT", 18000)
	if err != nil {
		return Config{}, err
	}
	root := envText("CTYUN_MANAGER_ROOT", ".")
	dbPath := envText("CTYUN_MANAGER_DB", filepath.Join(root, "data", "ctyun-manager.db"))
	cfg := Config{
		Address:                 net.JoinHostPort(envText("CTYUN_MANAGER_GO_HOST", "127.0.0.1"), strconv.Itoa(port)),
		StaticDir:               envText("CTYUN_MANAGER_STATIC_DIR", filepath.Join(root, "app", "static")),
		DatabasePath:            dbPath,
		MasterKeyPath:           envText("CTYUN_MANAGER_MASTER_KEY_FILE", filepath.Join(filepath.Dir(dbPath), "master.key")),
		L2TPScriptPath:          envText("CTYUN_MANAGER_L2TP_SCRIPT", filepath.Join(root, "install-l2tp-server.sh")),
		CTyunMode:               envText("CTYUN_MANAGER_CTYUN_MODE", "openapi"),
		SessionSecret:           envText("CTYUN_MANAGER_SESSION_SECRET", "change-this-session-secret"),
		ConfiguredKey:           envText("CTYUN_MANAGER_MASTER_KEY", ""),
		PublicURL:               envText("CTYUN_MANAGER_PUBLIC_URL", "http://127.0.0.1:8000"),
		BackgroundSyncEnabled:   envBool("CTYUN_BACKGROUND_SYNC_ENABLED", false),
		BackgroundSyncInterval:  time.Duration(envInt("CTYUN_BACKGROUND_SYNC_SECONDS", 1800, 60, 86400)) * time.Second,
		FinanceRefreshEnabled:   envBool("CTYUN_FINANCE_REFRESH_ENABLED", true),
		FinanceRefreshInterval:  time.Duration(envInt("CTYUN_FINANCE_REFRESH_SECONDS", 1800, 60, 86400)) * time.Second,
		CookieKeepaliveEnabled:  envBool("CTYUN_COOKIE_KEEPALIVE_ENABLED", true),
		CookieKeepaliveInterval: time.Duration(envInt("CTYUN_COOKIE_KEEPALIVE_SECONDS", 3600, 60, 86400)) * time.Second,
		RechargePrewarmEnabled:  envBool("CTYUN_RECHARGE_PREWARM_ENABLED", false),
		DatabaseReadOnly:        envBool("CTYUN_MANAGER_DB_READ_ONLY", true),
		RegionEndpoint:          envText("CTYUN_REGION_ENDPOINT", "https://ctecs-global.ctapi.ctyun.cn"),
		RegionListPath:          envText("CTYUN_REGION_LIST_PATH", "/v4/region/list-regions"),
		OpenAPITimeout:          time.Duration(envInt("CTYUN_OPENAPI_TIMEOUT_SECONDS", 20, 5, 60)) * time.Second,
		DefaultRegionIDs:        envText("CTYUN_DEFAULT_REGION_IDS", ""),
		ECSEndpoint:             envText("CTYUN_ECS_ENDPOINT", "https://ctecs-global.ctapi.ctyun.cn"),
		ECSListPath:             envText("CTYUN_ECS_LIST_PATH", "/v4/ecs/list-instances"),
		EIPEndpoint:             envText("CTYUN_EIP_ENDPOINT", "https://ctvpc-global.ctapi.ctyun.cn"),
		EIPListPath:             envText("CTYUN_EIP_LIST_PATH", "/v4/eip/list"),
		VPCEndpoint:             envText("CTYUN_VPC_ENDPOINT", "https://ctvpc-global.ctapi.ctyun.cn"),
		VPCListPath:             envText("CTYUN_VPC_LIST_PATH", "/v4/vpc/new-list"),
		SubnetEndpoint:          envText("CTYUN_SUBNET_ENDPOINT", "https://ctvpc-global.ctapi.ctyun.cn"),
		SubnetListPath:          envText("CTYUN_SUBNET_LIST_PATH", "/v4/vpc/new-list-subnet"),
		VIPEndpoint:             envText("CTYUN_VIP_ENDPOINT", "https://ctvpc-global.ctapi.ctyun.cn"),
		VIPListPath:             envText("CTYUN_VIP_LIST_PATH", "/v4/vpc/havip/list"),
		IMSEndpoint:             envText("CTYUN_IMS_ENDPOINT", "https://ctimage-global.ctapi.ctyun.cn"),
		IMSListPath:             envText("CTYUN_IMS_LIST_PATH", "/v4/image/list"),
		BrowserWorkerURL:        envText("CTYUN_BROWSER_WORKER_URL", "http://127.0.0.1:18080"),
		BrowserWorkerToken:      envText("CTYUN_BROWSER_WORKER_TOKEN", ""),
		ReadTimeout:             15 * time.Second,
		WriteTimeout:            time.Duration(envInt("CTYUN_MANAGER_WRITE_TIMEOUT_SECONDS", 900, 30, 3600)) * time.Second,
		IdleTimeout:             60 * time.Second,
	}
	if cfg.StaticDir, err = filepath.Abs(cfg.StaticDir); err != nil {
		return Config{}, fmt.Errorf("resolve static directory: %w", err)
	}
	if cfg.DatabasePath, err = filepath.Abs(cfg.DatabasePath); err != nil {
		return Config{}, fmt.Errorf("resolve database path: %w", err)
	}
	if cfg.MasterKeyPath, err = filepath.Abs(cfg.MasterKeyPath); err != nil {
		return Config{}, fmt.Errorf("resolve master key path: %w", err)
	}
	if cfg.L2TPScriptPath, err = filepath.Abs(cfg.L2TPScriptPath); err != nil {
		return Config{}, fmt.Errorf("resolve L2TP script path: %w", err)
	}
	return cfg, nil
}

func envInt(name string, fallback, minimum, maximum int) int {
	value, err := strconv.Atoi(envText(name, strconv.Itoa(fallback)))
	if err != nil {
		return fallback
	}
	if value < minimum {
		return minimum
	}
	if value > maximum {
		return maximum
	}
	return value
}

func envBool(name string, fallback bool) bool {
	value := strings.ToLower(strings.TrimSpace(os.Getenv(name)))
	if value == "" {
		return fallback
	}
	switch value {
	case "1", "true", "yes", "on":
		return true
	case "0", "false", "no", "off":
		return false
	default:
		return fallback
	}
}

func envText(name, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(name)); value != "" {
		return value
	}
	return fallback
}

func envPort(name string, fallback int) (int, error) {
	value := envText(name, strconv.Itoa(fallback))
	port, err := strconv.Atoi(value)
	if err != nil || port < 1 || port > 65535 {
		return 0, fmt.Errorf("%s must be a TCP port between 1 and 65535", name)
	}
	return port, nil
}

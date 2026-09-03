package httpserver

import (
	"net/http/httptest"
	"strings"
	"testing"
)

func TestSameWebSocketOrigin(t *testing.T) {
	t.Parallel()
	tests := []struct {
		origin string
		want   bool
	}{
		{"", true},
		{"https://manager.example.com", true},
		{"http://manager.example.com", true},
		{"https://manager.example.com:8443", false},
		{"https://manager.example.com.evil.test", false},
		{"https://evil.test/?manager.example.com", false},
		{"file://manager.example.com", false},
	}
	for _, tc := range tests {
		r := httptest.NewRequest("GET", "http://manager.example.com/api/linux/servers/1/ssh", nil)
		if tc.origin != "" {
			r.Header.Set("Origin", tc.origin)
		}
		if got := sameWebSocketOrigin(r); got != tc.want {
			t.Errorf("origin %q: got %v, want %v", tc.origin, got, tc.want)
		}
	}
}

func TestL2TPInstallEnvIncludesClientAndScanSettings(t *testing.T) {
	t.Parallel()
	env := l2tpInstallEnv(
		1701, 1280, 1280,
		"172.18.0.1", "172.18.0.2-172.18.255.254", "172.18.0.0/16", 32,
		"www.baidu.com", "192.168.0.100-192.168.0.110", []string{"192.168.0.108/32"},
		"VPN_ENABLE_IPSEC=0",
	)
	for _, want := range []string{
		"VPN_INTERACTIVE=0",
		"VPN_LOCAL_IP='172.18.0.1'",
		"VPN_CLIENT_POOL='172.18.0.2-172.18.255.254'",
		"VPN_CIDR='172.18.0.0/16'",
		"VPN_VIP_CANDIDATES='192.168.0.108/32'",
		"VPN_VIP_SCAN_RANGE='192.168.0.100-192.168.0.110'",
		"VPN_AUTO_CONFIG_FROM_USERS=0",
	} {
		if !strings.Contains(env, want) {
			t.Errorf("environment does not contain %q: %s", want, env)
		}
	}
}

func TestValidateL2TPInstallBody(t *testing.T) {
	t.Parallel()
	if err := validateL2TPInstallBody(1701, 1280, 1280, "172.18.0.1", "172.18.0.2-172.18.255.254", "172.18.0.0/16", 32, []string{"192.168.0.108/32"}); err != nil {
		t.Fatal(err)
	}
	if err := validateL2TPInstallBody(1701, 1280, 1280, "172.18.0.1", "bad", "172.18.0.0/16", 32, nil); err == nil {
		t.Fatal("invalid pool was accepted")
	}
}

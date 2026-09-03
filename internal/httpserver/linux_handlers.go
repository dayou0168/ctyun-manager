package httpserver

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"
	"github.com/gorilla/websocket"
	"golang.org/x/crypto/ssh"

	"github.com/dayou0168/ctyun-manager/internal/security"
	"github.com/dayou0168/ctyun-manager/internal/sshmanager"
	"github.com/dayou0168/ctyun-manager/internal/storage"
)

const l2tpUsersPath = "/etc/l2tp-vpn/users.conf"
const xl2tpdSourceArchive = "xl2tpd-v1.3.20.tar.gz"

type linuxStore interface {
	LinuxServers(context.Context) ([]storage.LinuxServer, error)
	LinuxServerByID(context.Context, int64) (storage.LinuxServer, error)
	CreateLinuxServer(context.Context, storage.LinuxServerWrite) (int64, error)
	UpdateLinuxServer(context.Context, int64, storage.LinuxServerWrite) error
	DeleteLinuxServer(context.Context, int64) error
	UpdateLinuxStatus(context.Context, int64, string, string, string) error
	RecordOperation(context.Context, *int64, string, string, string, string, string) error
}
type linuxBody struct {
	Name       string `json:"name"`
	Host       string `json:"host"`
	Port       int    `json:"port"`
	Username   string `json:"username"`
	Password   string `json:"password"`
	PrivateKey string `json:"private_key"`
	Passphrase string `json:"private_key_passphrase"`
	Notes      string `json:"notes"`
}

func (s *Server) linuxStore(w http.ResponseWriter) (linuxStore, bool) {
	v, ok := s.store.(linuxStore)
	if !ok {
		writeDetail(w, 503, "database_unavailable")
	}
	return v, ok
}
func (s *Server) listLinuxServers(w http.ResponseWriter, r *http.Request, _ security.Session) {
	store, ok := s.linuxStore(w)
	if !ok {
		return
	}
	rows, err := store.LinuxServers(r.Context())
	if err != nil {
		s.internalStoreError(w, "linux_list_failed", err)
		return
	}
	out := make([]map[string]any, 0, len(rows))
	for _, row := range rows {
		out = append(out, s.publicLinux(row))
	}
	writeJSON(w, 200, out)
}
func (s *Server) createLinuxServer(w http.ResponseWriter, r *http.Request, _ security.Session) {
	if s.cfg.DatabaseReadOnly {
		writeDetail(w, 503, "database_read_only")
		return
	}
	store, ok := s.linuxStore(w)
	if !ok {
		return
	}
	var body linuxBody
	if json.NewDecoder(io.LimitReader(r.Body, 3<<20)).Decode(&body) != nil {
		writeDetail(w, 422, "invalid_request")
		return
	}
	value, err := s.linuxWrite(body, storage.LinuxServer{})
	if err != nil {
		writeDetail(w, 422, err.Error())
		return
	}
	id, err := store.CreateLinuxServer(r.Context(), value)
	if err != nil {
		s.internalStoreError(w, "linux_create_failed", err)
		return
	}
	row, _ := store.LinuxServerByID(r.Context(), id)
	_ = store.RecordOperation(r.Context(), nil, "linux", fmt.Sprint(id), "create_server", "success", body.Name)
	writeJSON(w, 200, s.publicLinux(row))
}
func (s *Server) updateLinuxServer(w http.ResponseWriter, r *http.Request, _ security.Session) {
	if s.cfg.DatabaseReadOnly {
		writeDetail(w, 503, "database_read_only")
		return
	}
	store, ok := s.linuxStore(w)
	if !ok {
		return
	}
	id, err := pathID(r, "server_id")
	if err != nil {
		writeDetail(w, 422, "invalid_request")
		return
	}
	current, err := store.LinuxServerByID(r.Context(), id)
	if errors.Is(err, storage.ErrNotFound) {
		writeDetail(w, 404, "linux_server_not_found")
		return
	}
	var body linuxBody
	if json.NewDecoder(io.LimitReader(r.Body, 3<<20)).Decode(&body) != nil {
		writeDetail(w, 422, "invalid_request")
		return
	}
	value, err := s.linuxWrite(body, current)
	if err != nil {
		writeDetail(w, 422, err.Error())
		return
	}
	if value.Host != current.Host || value.Port != current.Port || body.Username != "" {
		value.Fingerprint = ""
	}
	if err = store.UpdateLinuxServer(r.Context(), id, value); err != nil {
		s.internalStoreError(w, "linux_update_failed", err)
		return
	}
	row, _ := store.LinuxServerByID(r.Context(), id)
	writeJSON(w, 200, s.publicLinux(row))
}
func (s *Server) deleteLinuxServer(w http.ResponseWriter, r *http.Request, _ security.Session) {
	if s.cfg.DatabaseReadOnly {
		writeDetail(w, 503, "database_read_only")
		return
	}
	store, ok := s.linuxStore(w)
	if !ok {
		return
	}
	id, err := pathID(r, "server_id")
	if err != nil {
		writeDetail(w, 422, "invalid_request")
		return
	}
	if err = store.DeleteLinuxServer(r.Context(), id); errors.Is(err, storage.ErrNotFound) {
		writeDetail(w, 404, "linux_server_not_found")
		return
	} else if err != nil {
		s.internalStoreError(w, "linux_delete_failed", err)
		return
	}
	writeJSON(w, 200, map[string]any{"ok": true})
}

func (s *Server) testLinuxServer(w http.ResponseWriter, r *http.Request, _ security.Session) {
	store, row, cfg, ok := s.linuxConnection(w, r)
	if !ok {
		return
	}
	conn, err := sshmanager.Connect(r.Context(), cfg, 10*time.Second)
	if err != nil {
		s.linuxFailed(r, store, row, err)
		writeDetail(w, 502, err.Error())
		return
	}
	defer conn.Close()
	_ = store.UpdateLinuxStatus(r.Context(), row.ID, "ready", "SSH 连接正常", conn.Fingerprint)
	writeJSON(w, 200, map[string]any{"ok": true, "fingerprint": conn.Fingerprint})
}
func (s *Server) linuxCommand(w http.ResponseWriter, r *http.Request, _ security.Session) {
	store, row, cfg, ok := s.linuxConnection(w, r)
	if !ok {
		return
	}
	var body struct {
		Command string `json:"command"`
		Timeout int    `json:"timeout"`
	}
	if json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&body) != nil {
		writeDetail(w, 422, "invalid_request")
		return
	}
	if body.Timeout < 3 {
		body.Timeout = 30
	}
	if body.Timeout > 300 {
		body.Timeout = 300
	}
	conn, err := sshmanager.Connect(r.Context(), cfg, 15*time.Second)
	if err != nil {
		s.linuxFailed(r, store, row, err)
		writeDetail(w, 502, err.Error())
		return
	}
	defer conn.Close()
	ctx, cancel := context.WithTimeout(r.Context(), time.Duration(body.Timeout)*time.Second)
	defer cancel()
	result, err := conn.Run(ctx, body.Command)
	if err != nil {
		s.linuxFailed(r, store, row, err)
		writeDetail(w, 502, err.Error())
		return
	}
	status := "success"
	if result["exit_status"] != 0 {
		status = "failed"
	}
	_ = store.UpdateLinuxStatus(r.Context(), row.ID, "ready", fmt.Sprintf("命令退出码：%v", result["exit_status"]), conn.Fingerprint)
	_ = store.RecordOperation(r.Context(), nil, "linux", fmt.Sprint(row.ID), "run_command", status, bounded(body.Command, 160))
	result["ok"] = status == "success"
	writeJSON(w, 200, result)
}

func (s *Server) linuxFilesList(w http.ResponseWriter, r *http.Request, _ security.Session) {
	s.withSFTP(w, r, func(conn *sshmanager.Connection) (map[string]any, error) {
		var b struct {
			Path string `json:"path"`
		}
		if json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&b) != nil {
			return nil, errors.New("invalid_request")
		}
		return conn.List(b.Path)
	})
}
func (s *Server) linuxFileRead(w http.ResponseWriter, r *http.Request, _ security.Session) {
	s.withSFTP(w, r, func(conn *sshmanager.Connection) (map[string]any, error) {
		var b struct {
			Path string `json:"path"`
		}
		if json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&b) != nil {
			return nil, errors.New("invalid_request")
		}
		return conn.Read(b.Path)
	})
}
func (s *Server) linuxFileWrite(w http.ResponseWriter, r *http.Request, _ security.Session) {
	if s.cfg.DatabaseReadOnly {
		writeDetail(w, 503, "database_read_only")
		return
	}
	s.withSFTP(w, r, func(conn *sshmanager.Connection) (map[string]any, error) {
		var b struct {
			Path    string `json:"path"`
			Content string `json:"content"`
		}
		if json.NewDecoder(io.LimitReader(r.Body, 3<<20)).Decode(&b) != nil {
			return nil, errors.New("invalid_request")
		}
		result, err := conn.Write(b.Path, b.Content, 0)
		if result != nil {
			result["ok"] = err == nil
		}
		return result, err
	})
}
func (s *Server) withSFTP(w http.ResponseWriter, r *http.Request, fn func(*sshmanager.Connection) (map[string]any, error)) {
	store, row, cfg, ok := s.linuxConnection(w, r)
	if !ok {
		return
	}
	conn, err := sshmanager.Connect(r.Context(), cfg, 15*time.Second)
	if err == nil {
		defer conn.Close()
		result, callErr := fn(conn)
		err = callErr
		if err == nil {
			_ = store.UpdateLinuxStatus(r.Context(), row.ID, "ready", "", conn.Fingerprint)
			writeJSON(w, 200, result)
			return
		}
	}
	s.linuxFailed(r, store, row, err)
	writeDetail(w, 502, err.Error())
}

func (s *Server) linuxL2TPConfig(w http.ResponseWriter, r *http.Request, _ security.Session) {
	s.withSFTP(w, r, func(conn *sshmanager.Connection) (map[string]any, error) {
		result, err := conn.Read(l2tpUsersPath)
		if err != nil && strings.Contains(strings.ToLower(err.Error()), "no such file") {
			return map[string]any{"exists": false, "path": l2tpUsersPath, "parent": "/etc/l2tp-vpn", "content": defaultL2TPUsers(), "size": 0}, nil
		}
		if result != nil {
			result["exists"] = true
		}
		return result, err
	})
}
func (s *Server) linuxL2TPSaveConfig(w http.ResponseWriter, r *http.Request, _ security.Session) {
	if s.cfg.DatabaseReadOnly {
		writeDetail(w, 503, "database_read_only")
		return
	}
	var body struct {
		Content string `json:"content"`
		Apply   bool   `json:"apply"`
	}
	if json.NewDecoder(io.LimitReader(r.Body, 3<<20)).Decode(&body) != nil {
		writeDetail(w, 422, "invalid_request")
		return
	}
	s.runL2TPUpload(w, r, body.Content, body.Apply)
}
func (s *Server) linuxL2TPApply(w http.ResponseWriter, r *http.Request, _ security.Session) {
	if s.cfg.DatabaseReadOnly {
		writeDetail(w, 503, "database_read_only")
		return
	}
	s.runL2TPCommand(w, r, l2tpApplyCommand(), 300)
}
func (s *Server) linuxL2TPInstall(w http.ResponseWriter, r *http.Request, _ security.Session) {
	if s.cfg.DatabaseReadOnly {
		writeDetail(w, 503, "database_read_only")
		return
	}
	var body struct {
		Port            int      `json:"port"`
		MTU             int      `json:"mtu"`
		MRU             int      `json:"mru"`
		PSK             string   `json:"psk"`
		RandomPSK       bool     `json:"random_psk"`
		LocalIP         string   `json:"local_ip"`
		ClientPool      string   `json:"client_pool"`
		CIDR            string   `json:"cidr"`
		UsersConfig     string   `json:"users_config"`
		VIPCandidates   []string `json:"vip_candidates"`
		VIPScanRange    string   `json:"vip_scan_range"`
		VIPScanParallel int      `json:"vip_scan_parallel"`
		VIPProbeTarget  string   `json:"vip_probe_target"`
	}
	if json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&body) != nil {
		writeDetail(w, 422, "invalid_request")
		return
	}
	if body.Port == 0 {
		body.Port = 1701
	}
	if body.MTU == 0 {
		body.MTU = 1280
	}
	if body.MRU == 0 {
		body.MRU = 1280
	}
	if body.LocalIP == "" {
		body.LocalIP = "172.18.0.1"
	}
	if body.ClientPool == "" {
		body.ClientPool = "172.18.0.2-172.18.255.254"
	}
	if body.CIDR == "" {
		body.CIDR = "172.18.0.0/16"
	}
	if body.VIPScanParallel == 0 {
		body.VIPScanParallel = 32
	}
	if err := validateL2TPInstallBody(body.Port, body.MTU, body.MRU, body.LocalIP, body.ClientPool, body.CIDR, body.VIPScanParallel, body.VIPCandidates); err != nil {
		writeDetail(w, 422, err.Error())
		return
	}
	script, err := os.ReadFile(s.cfg.L2TPScriptPath)
	if err != nil {
		writeDetail(w, 500, "install-l2tp-server.sh not found")
		return
	}
	sourceArchivePath := filepath.Join(filepath.Dir(s.cfg.L2TPScriptPath), "third_party", xl2tpdSourceArchive)
	sourceArchive, err := os.ReadFile(sourceArchivePath)
	if err != nil {
		writeDetail(w, 500, "bundled xl2tpd source archive not found")
		return
	}
	store, row, cfg, ok := s.linuxConnection(w, r)
	if !ok {
		return
	}
	conn, err := sshmanager.Connect(r.Context(), cfg, 15*time.Second)
	if err != nil {
		s.linuxFailed(r, store, row, err)
		writeDetail(w, 502, err.Error())
		return
	}
	defer conn.Close()
	tmp := "/tmp/ctyun-install-l2tp-" + uuid.NewString() + ".sh"
	if _, err = conn.Write(tmp, string(script), 0700); err == nil {
		sourceTmp := "/tmp/ctyun-xl2tpd-" + uuid.NewString() + ".tar.gz"
		if _, err = conn.Write(sourceTmp, string(sourceArchive), 0600); err != nil {
			s.linuxFailed(r, store, row, err)
			writeDetail(w, 502, err.Error())
			return
		}
		usersTmp := ""
		if body.UsersConfig != "" {
			usersTmp = "/tmp/ctyun-l2tp-users-" + uuid.NewString() + ".conf"
			if _, err = conn.Write(usersTmp, strings.ReplaceAll(body.UsersConfig, "\r\n", "\n"), 0600); err != nil {
				s.linuxFailed(r, store, row, err)
				writeDetail(w, 502, err.Error())
				return
			}
		}
		ipsec := "VPN_ENABLE_IPSEC=0"
		if body.RandomPSK {
			ipsec = "VPN_ENABLE_IPSEC=1 VPN_RANDOM_PSK=1"
		} else if body.PSK != "" {
			ipsec = "VPN_ENABLE_IPSEC=1 VPN_IPSEC_PSK=" + shellQuote(body.PSK)
		}
		env := l2tpInstallEnv(body.Port, body.MTU, body.MRU, body.LocalIP, body.ClientPool, body.CIDR, body.VIPScanParallel, body.VIPProbeTarget, body.VIPScanRange, body.VIPCandidates, ipsec)
		env += " VPN_XL2TPD_SOURCE_FILE=" + shellQuote(sourceTmp)
		prepareUsers := ""
		cleanup := "rm -f " + shellQuote(tmp) + " " + shellQuote(sourceTmp)
		if usersTmp != "" {
			prepareUsers = "SUDO=sudo; [ \"$(id -u)\" -eq 0 ] && SUDO=; $SUDO mkdir -p /etc/l2tp-vpn; $SUDO install -m 600 " + shellQuote(usersTmp) + " " + l2tpUsersPath + "; "
			cleanup += " " + shellQuote(usersTmp)
		}
		command := fmt.Sprintf("set -e; trap %s EXIT; chmod 700 %s; %sif [ \"$(id -u)\" -eq 0 ]; then env %s bash %s; else sudo env %s bash %s; fi", shellQuote(cleanup), shellQuote(tmp), prepareUsers, env, shellQuote(tmp), env, shellQuote(tmp))
		ctx, cancel := context.WithTimeout(r.Context(), 15*time.Minute)
		defer cancel()
		var result map[string]any
		result, err = conn.Run(ctx, command)
		if err == nil {
			result["ok"] = result["exit_status"] == 0
			writeJSON(w, 200, result)
			return
		}
	}
	s.linuxFailed(r, store, row, err)
	writeDetail(w, 502, err.Error())
}

func validateL2TPInstallBody(port, mtu, mru int, localIP, clientPool, cidr string, parallel int, candidates []string) error {
	if port < 1 || port > 65535 || mtu < 576 || mtu > 1500 || mru < 576 || mru > 1500 || parallel < 1 || parallel > 128 {
		return errors.New("invalid_l2tp_settings")
	}
	if net.ParseIP(localIP) == nil || strings.Contains(localIP, ":") {
		return errors.New("invalid_local_ip")
	}
	if _, _, err := net.ParseCIDR(cidr); err != nil || strings.Contains(cidr, ":") {
		return errors.New("invalid_vpn_cidr")
	}
	parts := strings.Split(clientPool, "-")
	if len(parts) != 2 || net.ParseIP(strings.TrimSpace(parts[0])) == nil || net.ParseIP(strings.TrimSpace(parts[1])) == nil || strings.Contains(clientPool, ":") {
		return errors.New("invalid_client_pool")
	}
	for _, candidate := range candidates {
		ip := strings.TrimSpace(strings.SplitN(candidate, "/", 2)[0])
		if net.ParseIP(ip) == nil || strings.Contains(ip, ":") {
			return errors.New("invalid_vip_candidate")
		}
	}
	return nil
}

func l2tpInstallEnv(port, mtu, mru int, localIP, clientPool, cidr string, parallel int, probeTarget, scanRange string, candidates []string, ipsec string) string {
	values := []string{
		"VPN_INTERACTIVE=0", "VPN_L2TP_PORT=" + strconv.Itoa(port), "VPN_MTU=" + strconv.Itoa(mtu), "VPN_MRU=" + strconv.Itoa(mru),
		"VPN_LOCAL_IP=" + shellQuote(localIP), "VPN_CLIENT_POOL=" + shellQuote(clientPool), "VPN_CIDR=" + shellQuote(cidr),
		"VPN_PLATFORM_SCAN=1", "VPN_VIP_CANDIDATES=" + shellQuote(strings.Join(candidates, ",")), "VPN_VIPS=''", "VPN_AUTO_CONFIG_FROM_USERS=0",
		"VPN_VIP_SCAN_PARALLEL=" + strconv.Itoa(parallel),
	}
	if strings.TrimSpace(probeTarget) != "" {
		values = append(values, "VPN_VIP_PROBE_TARGET="+shellQuote(strings.TrimSpace(probeTarget)))
	}
	if strings.TrimSpace(scanRange) != "" {
		values = append(values, "VPN_VIP_SCAN_RANGE="+shellQuote(strings.TrimSpace(scanRange)))
	}
	return strings.Join(append(values, ipsec), " ")
}

func (s *Server) linuxL2TPVIPs(w http.ResponseWriter, r *http.Request, _ security.Session) {
	id, err := pathID(r, "server_id")
	if err != nil {
		writeDetail(w, 422, "invalid_request")
		return
	}
	if store, ok := s.store.(linuxStore); ok {
		if _, err = store.LinuxServerByID(r.Context(), id); errors.Is(err, storage.ErrNotFound) {
			writeDetail(w, 404, "linux_server_not_found")
			return
		}
	}
	accounts, err := s.store.Accounts(r.Context())
	if err != nil {
		s.internalStoreError(w, "account_query_failed", err)
		return
	}
	names := map[int64]string{}
	for _, a := range accounts {
		names[a.ID] = a.Name
	}
	rows, err := s.store.Resources(r.Context(), "vip", nil)
	if err != nil {
		s.internalStoreError(w, "vip_query_failed", err)
		return
	}
	items := []map[string]any{}
	seen := map[string]bool{}
	for _, row := range rows {
		var p map[string]any
		if json.Unmarshal([]byte(row.PayloadJSON), &p) != nil {
			continue
		}
		ip := firstText(p, "ip", "ipv4", "ipAddress", "private_ip", "privateIP")
		if ip == "" || seen[ip] {
			continue
		}
		seen[ip] = true
		public := firstText(p, "public_ip", "publicIP", "eipAddress", "floatingIP")
		items = append(items, map[string]any{"private_ip": ip, "public_ip": public, "source": "虚拟IP", "account_id": row.AccountID, "account_name": names[row.AccountID], "region": row.Region, "resource_id": row.ProviderID, "name": row.Name})
	}
	writeJSON(w, 200, map[string]any{"server_id": id, "candidate_items": items, "items": items, "probe_items": items, "matched_ecs_count": 0, "server_host": ""})
}

func (s *Server) runL2TPUpload(w http.ResponseWriter, r *http.Request, content string, apply bool) {
	store, row, cfg, ok := s.linuxConnection(w, r)
	if !ok {
		return
	}
	conn, err := sshmanager.Connect(r.Context(), cfg, 15*time.Second)
	if err != nil {
		s.linuxFailed(r, store, row, err)
		writeDetail(w, 502, err.Error())
		return
	}
	defer conn.Close()
	tmp := "/tmp/ctyun-l2tp-users-" + uuid.NewString() + ".conf"
	_, err = conn.Write(tmp, strings.ReplaceAll(content, "\r\n", "\n"), 0600)
	if err == nil {
		command := "set -e; SUDO=sudo; [ \"$(id -u)\" -eq 0 ] && SUDO=; $SUDO mkdir -p /etc/l2tp-vpn; $SUDO install -m 600 " + shellQuote(tmp) + " " + l2tpUsersPath + "; rm -f " + shellQuote(tmp)
		if apply {
			command += "; " + l2tpApplyCommand()
		}
		ctx, cancel := context.WithTimeout(r.Context(), 5*time.Minute)
		defer cancel()
		var result map[string]any
		result, err = conn.Run(ctx, command)
		if err == nil {
			result["ok"] = result["exit_status"] == 0
			writeJSON(w, 200, result)
			return
		}
	}
	s.linuxFailed(r, store, row, err)
	writeDetail(w, 502, err.Error())
}
func (s *Server) runL2TPCommand(w http.ResponseWriter, r *http.Request, command string, seconds int) {
	store, row, cfg, ok := s.linuxConnection(w, r)
	if !ok {
		return
	}
	conn, err := sshmanager.Connect(r.Context(), cfg, 15*time.Second)
	if err == nil {
		defer conn.Close()
		ctx, cancel := context.WithTimeout(r.Context(), time.Duration(seconds)*time.Second)
		defer cancel()
		var result map[string]any
		result, err = conn.Run(ctx, command)
		if err == nil {
			result["ok"] = result["exit_status"] == 0
			writeJSON(w, 200, result)
			return
		}
	}
	s.linuxFailed(r, store, row, err)
	writeDetail(w, 502, err.Error())
}
func l2tpApplyCommand() string {
	return `SUDO=sudo; [ "$(id -u)" -eq 0 ] && SUDO=; if [ -x /usr/local/sbin/l2tp-vpn-apply-config.sh ]; then $SUDO env VPN_INTERACTIVE=0 VPN_APPLY_ONLY=1 /usr/local/sbin/l2tp-vpn-apply-config.sh; elif [ -x /usr/local/sbin/l2tp-vpn-apply-users.sh ]; then $SUDO /usr/local/sbin/l2tp-vpn-apply-users.sh; else echo 'l2tp-vpn apply helper not found; run installer first' >&2; exit 3; fi`
}

func (s *Server) linuxConnection(w http.ResponseWriter, r *http.Request) (linuxStore, storage.LinuxServer, sshmanager.Config, bool) {
	store, ok := s.linuxStore(w)
	if !ok {
		return nil, storage.LinuxServer{}, sshmanager.Config{}, false
	}
	id, err := pathID(r, "server_id")
	if err != nil {
		writeDetail(w, 422, "invalid_request")
		return nil, storage.LinuxServer{}, sshmanager.Config{}, false
	}
	row, err := store.LinuxServerByID(r.Context(), id)
	if errors.Is(err, storage.ErrNotFound) {
		writeDetail(w, 404, "linux_server_not_found")
		return nil, row, sshmanager.Config{}, false
	}
	decrypt := func(v string) string { x, _ := s.keys.DecryptString(v); return x }
	return store, row, sshmanager.Config{Host: row.Host, Port: row.Port, Username: decrypt(row.UsernameEncrypted), Password: decrypt(row.PasswordEncrypted), PrivateKey: decrypt(row.PrivateKeyEncrypted), Passphrase: decrypt(row.PassphraseEncrypted), ExpectedFingerprint: row.Fingerprint}, true
}
func (s *Server) linuxFailed(r *http.Request, store linuxStore, row storage.LinuxServer, err error) {
	if err == nil {
		return
	}
	_ = store.UpdateLinuxStatus(r.Context(), row.ID, "failed", bounded(err.Error(), 500), "")
	_ = store.RecordOperation(r.Context(), nil, "linux", fmt.Sprint(row.ID), "ssh", "failed", err.Error())
}
func (s *Server) linuxWrite(body linuxBody, current storage.LinuxServer) (storage.LinuxServerWrite, error) {
	host, err := sshmanager.NormalizeHost(body.Host)
	if err != nil {
		return storage.LinuxServerWrite{}, err
	}
	if body.Port == 0 {
		body.Port = 22
	}
	if body.Name == "" {
		return storage.LinuxServerWrite{}, errors.New("请填写服务器名称")
	}
	enc := func(v string) (string, error) { return s.keys.EncryptString(v) }
	value := storage.LinuxServerWrite{Name: strings.TrimSpace(body.Name), Host: host, Port: body.Port, UsernameEncrypted: current.UsernameEncrypted, PasswordEncrypted: current.PasswordEncrypted, PrivateKeyEncrypted: current.PrivateKeyEncrypted, PassphraseEncrypted: current.PassphraseEncrypted, Fingerprint: current.Fingerprint, Notes: body.Notes}
	for plain, target := range map[string]*string{body.Username: &value.UsernameEncrypted, body.Password: &value.PasswordEncrypted, body.PrivateKey: &value.PrivateKeyEncrypted, body.Passphrase: &value.PassphraseEncrypted} {
		if plain != "" {
			*target, err = enc(plain)
			if err != nil {
				return value, err
			}
		}
	}
	if value.UsernameEncrypted == "" || value.PasswordEncrypted == "" && value.PrivateKeyEncrypted == "" {
		return value, errors.New("请填写 SSH 登录账号以及密码或私钥")
	}
	return value, nil
}
func (s *Server) publicLinux(v storage.LinuxServer) map[string]any {
	username, _ := s.keys.DecryptString(v.UsernameEncrypted)
	return map[string]any{"id": v.ID, "name": v.Name, "host": v.Host, "port": v.Port, "status": v.Status, "last_status": v.LastStatus, "last_message": v.LastMessage, "fingerprint": v.Fingerprint, "notes": v.Notes, "username_masked": security.Mask(username), "has_password": v.PasswordEncrypted != "", "has_private_key": v.PrivateKeyEncrypted != "", "created_at": v.CreatedAt, "updated_at": v.UpdatedAt}
}
func pathID(r *http.Request, key string) (int64, error) {
	return strconv.ParseInt(r.PathValue(key), 10, 64)
}
func shellQuote(v string) string { return "'" + strings.ReplaceAll(v, "'", "'\"'\"'") + "'" }
func bounded(v string, n int) string {
	if len(v) > n {
		return v[:n]
	}
	return v
}
func defaultL2TPUsers() string {
	return "# L2TP VPN 用户配置\n# 账号,密码,出口虚拟内网IP,共享连接数,客户端内网IP或IP段,公网IP备注\n# DF31,112233..,192.168.0.101,254,,主网卡公网IP\n"
}
func firstText(m map[string]any, keys ...string) string {
	for _, key := range keys {
		if v := strings.TrimSpace(fmt.Sprint(m[key])); v != "" && v != "<nil>" {
			return v
		}
	}
	return ""
}

var sshUpgrader = websocket.Upgrader{CheckOrigin: sameWebSocketOrigin}

func sameWebSocketOrigin(r *http.Request) bool {
	origin := r.Header.Get("Origin")
	if origin == "" {
		return true
	}
	parsed, err := url.Parse(origin)
	return err == nil && (parsed.Scheme == "http" || parsed.Scheme == "https") && strings.EqualFold(parsed.Host, r.Host)
}

func (s *Server) linuxSSH(w http.ResponseWriter, r *http.Request) {
	cookie, err := r.Cookie(sessionCookie)
	if err != nil {
		writeDetail(w, http.StatusUnauthorized, "not_authenticated")
		return
	}
	if _, ok := security.VerifySession(cookie.Value, s.cfg.SessionSecret, time.Now(), sessionMaxAge); !ok {
		writeDetail(w, http.StatusUnauthorized, "not_authenticated")
		return
	}
	store, row, cfg, ok := s.linuxConnection(w, r)
	if !ok {
		return
	}
	ws, err := sshUpgrader.Upgrade(w, r, nil)
	if err != nil {
		return
	}
	defer ws.Close()
	_ = ws.WriteMessage(websocket.TextMessage, []byte(fmt.Sprintf("正在连接 %s (%s:%d)...\r\n", row.Name, row.Host, row.Port)))
	conn, err := sshmanager.Connect(r.Context(), cfg, 15*time.Second)
	if err != nil {
		s.linuxFailed(r, store, row, err)
		_ = ws.WriteMessage(websocket.TextMessage, []byte("\r\nSSH 连接失败："+err.Error()+"\r\n"))
		return
	}
	defer conn.Close()
	session, err := conn.Client.NewSession()
	if err != nil {
		_ = ws.WriteMessage(websocket.TextMessage, []byte(err.Error()))
		return
	}
	defer session.Close()
	if err = session.RequestPty("xterm", 36, 120, ssh.TerminalModes{ssh.ECHO: 1, ssh.TTY_OP_ISPEED: 14400, ssh.TTY_OP_OSPEED: 14400}); err != nil {
		_ = ws.WriteMessage(websocket.TextMessage, []byte(err.Error()))
		return
	}
	stdin, err := session.StdinPipe()
	if err != nil {
		return
	}
	stdout, err := session.StdoutPipe()
	if err != nil {
		return
	}
	if err = session.Shell(); err != nil {
		_ = ws.WriteMessage(websocket.TextMessage, []byte(err.Error()))
		return
	}
	_ = store.UpdateLinuxStatus(r.Context(), row.ID, "ready", "SSH 会话已建立", conn.Fingerprint)
	_ = store.RecordOperation(r.Context(), nil, "linux", fmt.Sprint(row.ID), "open_ssh", "success", row.Name)
	_ = ws.WriteMessage(websocket.TextMessage, []byte("SSH 会话已连接。\r\n"))
	done := make(chan struct{})
	var once sync.Once
	finish := func() { once.Do(func() { close(done) }) }
	go func() {
		defer finish()
		defer ws.Close()
		buf := make([]byte, 4096)
		for {
			n, e := stdout.Read(buf)
			if n > 0 {
				if ws.WriteMessage(websocket.TextMessage, buf[:n]) != nil {
					return
				}
			}
			if e != nil {
				return
			}
		}
	}()
	for {
		_, data, e := ws.ReadMessage()
		if e != nil {
			break
		}
		text := string(data)
		if strings.HasPrefix(text, "__resize__:") {
			parts := strings.Split(text, ":")
			if len(parts) == 3 {
				cols, _ := strconv.Atoi(parts[1])
				rows, _ := strconv.Atoi(parts[2])
				if cols < 60 {
					cols = 60
				}
				if cols > 240 {
					cols = 240
				}
				if rows < 16 {
					rows = 16
				}
				if rows > 80 {
					rows = 80
				}
				_ = session.WindowChange(rows, cols)
			}
			continue
		}
		if _, e = stdin.Write(data); e != nil {
			break
		}
		select {
		case <-done:
			return
		default:
		}
	}
}

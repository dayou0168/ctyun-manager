package sshmanager

import (
	"bytes"
	"context"
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"path"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/pkg/sftp"
	"golang.org/x/crypto/ssh"
)

const MaxFileSize = 2_000_000

type Config struct {
	Host                                                            string
	Port                                                            int
	Username, Password, PrivateKey, Passphrase, ExpectedFingerprint string
}
type Connection struct {
	Client      *ssh.Client
	Fingerprint string
}

func NormalizeHost(value string) (string, error) {
	v := strings.TrimSpace(value)
	if v == "" {
		return "", errors.New("请填写服务器地址")
	}
	if strings.Contains(v, "://") || strings.ContainsAny(v, " \t\r\n") {
		return "", errors.New("服务器地址只填写 IP 或域名，不要包含协议或空格")
	}
	return strings.Trim(v, "[]"), nil
}
func Connect(ctx context.Context, c Config, timeout time.Duration) (*Connection, error) {
	host, err := NormalizeHost(c.Host)
	if err != nil {
		return nil, err
	}
	if c.Port < 1 || c.Port > 65535 {
		return nil, errors.New("SSH 端口必须在 1-65535 之间")
	}
	if strings.TrimSpace(c.Username) == "" {
		return nil, errors.New("请填写 SSH 登录账号")
	}
	auth := []ssh.AuthMethod{}
	if c.PrivateKey != "" {
		var signer ssh.Signer
		if c.Passphrase != "" {
			signer, err = ssh.ParsePrivateKeyWithPassphrase([]byte(c.PrivateKey), []byte(c.Passphrase))
		} else {
			signer, err = ssh.ParsePrivateKey([]byte(c.PrivateKey))
		}
		if err != nil {
			return nil, errors.New("私钥格式无法识别，请确认是 OpenSSH/PEM 私钥")
		}
		auth = append(auth, ssh.PublicKeys(signer))
	} else if c.Password != "" {
		auth = append(auth, ssh.Password(c.Password))
	} else {
		return nil, errors.New("请填写 SSH 密码或私钥")
	}
	fingerprint := ""
	callback := func(_ string, _ net.Addr, key ssh.PublicKey) error {
		fingerprint = key.Type() + " " + base64.StdEncoding.EncodeToString(key.Marshal())[:18] + "..."
		if c.ExpectedFingerprint != "" && c.ExpectedFingerprint != fingerprint {
			return fmt.Errorf("SSH 主机指纹已变化：原 %s，现 %s", c.ExpectedFingerprint, fingerprint)
		}
		return nil
	}
	clientCfg := &ssh.ClientConfig{User: c.Username, Auth: auth, HostKeyCallback: callback, Timeout: timeout}
	dialer := net.Dialer{Timeout: timeout}
	raw, err := dialer.DialContext(ctx, "tcp", net.JoinHostPort(host, fmt.Sprint(c.Port)))
	if err != nil {
		return nil, fmt.Errorf("SSH 连接失败: %w", err)
	}
	cc, ch, reqs, err := ssh.NewClientConn(raw, net.JoinHostPort(host, fmt.Sprint(c.Port)), clientCfg)
	if err != nil {
		raw.Close()
		return nil, fmt.Errorf("SSH 认证失败: %w", err)
	}
	return &Connection{Client: ssh.NewClient(cc, ch, reqs), Fingerprint: fingerprint}, nil
}

func (c *Connection) Close() {
	if c != nil && c.Client != nil {
		_ = c.Client.Close()
	}
}
func (c *Connection) Run(ctx context.Context, command string) (map[string]any, error) {
	if strings.TrimSpace(command) == "" {
		return nil, errors.New("请填写要执行的命令")
	}
	session, err := c.Client.NewSession()
	if err != nil {
		return nil, err
	}
	defer session.Close()
	var outBuf, errBuf bytes.Buffer
	session.Stdout = &limitWriter{w: &outBuf, n: 65536}
	session.Stderr = &limitWriter{w: &errBuf, n: 65536}
	done := make(chan error, 1)
	go func() { done <- session.Run(command) }()
	exit := 0
	select {
	case err = <-done:
		if err != nil {
			var ee *ssh.ExitError
			if errors.As(err, &ee) {
				exit = ee.ExitStatus()
			} else {
				return nil, err
			}
		}
	case <-ctx.Done():
		_ = session.Signal(ssh.SIGKILL)
		return nil, fmt.Errorf("命令执行超时: %w", ctx.Err())
	}
	return map[string]any{"exit_status": exit, "stdout": outBuf.String(), "stderr": errBuf.String(), "fingerprint": c.Fingerprint}, nil
}

type limitWriter struct {
	w  io.Writer
	n  int
	mu sync.Mutex
}

func (l *limitWriter) Write(p []byte) (int, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	original := len(p)
	if l.n <= 0 {
		return original, nil
	}
	if len(p) > l.n {
		p = p[:l.n]
	}
	n, err := l.w.Write(p)
	l.n -= n
	return original, err
}

func (c *Connection) sftp() (*sftp.Client, error) { return sftp.NewClient(c.Client) }
func (c *Connection) List(remote string) (map[string]any, error) {
	client, err := c.sftp()
	if err != nil {
		return nil, err
	}
	defer client.Close()
	normalized, err := client.RealPath(cleanRemote(remote, "."))
	if err != nil {
		return nil, err
	}
	entries, err := client.ReadDir(normalized)
	if err != nil {
		return nil, err
	}
	sort.Slice(entries, func(i, j int) bool {
		if entries[i].IsDir() != entries[j].IsDir() {
			return entries[i].IsDir()
		}
		return strings.ToLower(entries[i].Name()) < strings.ToLower(entries[j].Name())
	})
	items := []map[string]any{}
	for _, e := range entries {
		kind := "file"
		if e.IsDir() {
			kind = "dir"
		}
		items = append(items, map[string]any{"name": e.Name(), "path": path.Join(normalized, e.Name()), "type": kind, "size": e.Size(), "mtime": e.ModTime().Unix(), "mode": fmt.Sprintf("%#o", e.Mode().Perm()), "uid": 0, "gid": 0})
	}
	return map[string]any{"path": normalized, "parent": path.Dir(normalized), "entries": items, "fingerprint": c.Fingerprint}, nil
}
func (c *Connection) Read(remote string) (map[string]any, error) {
	client, err := c.sftp()
	if err != nil {
		return nil, err
	}
	defer client.Close()
	normalized, err := client.RealPath(cleanRemote(remote, ""))
	if err != nil {
		return nil, err
	}
	stat, err := client.Stat(normalized)
	if err != nil {
		return nil, err
	}
	if stat.Size() > MaxFileSize {
		return nil, fmt.Errorf("远程文件过大 (%d bytes)", stat.Size())
	}
	f, err := client.Open(normalized)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	data, err := io.ReadAll(io.LimitReader(f, MaxFileSize+1))
	if err != nil {
		return nil, err
	}
	if len(data) > MaxFileSize {
		return nil, errors.New("远程文件超过 2000000 字节")
	}
	return map[string]any{"path": normalized, "parent": path.Dir(normalized), "content": string(data), "size": stat.Size(), "mtime": stat.ModTime().Unix(), "mode": fmt.Sprintf("%#o", stat.Mode().Perm()), "fingerprint": c.Fingerprint}, nil
}
func (c *Connection) Write(remote, content string, mode uint32) (map[string]any, error) {
	if len([]byte(content)) > MaxFileSize {
		return nil, errors.New("远程文件内容超过 2000000 字节")
	}
	client, err := c.sftp()
	if err != nil {
		return nil, err
	}
	defer client.Close()
	remote = cleanRemote(remote, "")
	if remote == "" {
		return nil, errors.New("请填写远程文件路径")
	}
	f, err := client.OpenFile(remote, os.O_WRONLY|os.O_CREATE|os.O_TRUNC)
	if err != nil {
		return nil, err
	}
	if _, err = f.Write([]byte(content)); err != nil {
		f.Close()
		return nil, err
	}
	if err = f.Close(); err != nil {
		return nil, err
	}
	if mode > 0 {
		_ = client.Chmod(remote, os.FileMode(mode))
	}
	stat, err := client.Stat(remote)
	if err != nil {
		return nil, err
	}
	return map[string]any{"path": remote, "parent": path.Dir(remote), "size": stat.Size(), "mtime": stat.ModTime().Unix(), "mode": fmt.Sprintf("%#o", stat.Mode().Perm()), "fingerprint": c.Fingerprint}, nil
}
func cleanRemote(v, fallback string) string {
	v = strings.TrimSpace(strings.ReplaceAll(v, "\\", "/"))
	if v == "" {
		return fallback
	}
	return path.Clean(v)
}

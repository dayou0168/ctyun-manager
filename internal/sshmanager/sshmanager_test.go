package sshmanager

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"errors"
	"net"
	"strconv"
	"strings"
	"testing"
	"time"

	"golang.org/x/crypto/ssh"
)

func TestHostFingerprintUsesCompleteSHA256Digest(t *testing.T) {
	t.Parallel()
	_, firstPrivate, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	_, secondPrivate, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	firstSigner, err := ssh.NewSignerFromKey(firstPrivate)
	if err != nil {
		t.Fatal(err)
	}
	secondSigner, err := ssh.NewSignerFromKey(secondPrivate)
	if err != nil {
		t.Fatal(err)
	}
	first := HostFingerprint(firstSigner.PublicKey())
	second := HostFingerprint(secondSigner.PublicKey())
	if !strings.HasPrefix(first, "ssh-ed25519 SHA256:") {
		t.Fatalf("unexpected fingerprint: %s", first)
	}
	if first == second {
		t.Fatal("different host keys produced the same fingerprint")
	}
}

func TestProbeFingerprintDoesNotRequireValidCredentials(t *testing.T) {
	_, privateKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	signer, err := ssh.NewSignerFromKey(privateKey)
	if err != nil {
		t.Fatal(err)
	}
	serverConfig := &ssh.ServerConfig{
		PasswordCallback: func(ssh.ConnMetadata, []byte) (*ssh.Permissions, error) {
			return nil, errors.New("authentication rejected")
		},
	}
	serverConfig.AddHostKey(signer)

	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	go func() {
		conn, acceptErr := listener.Accept()
		if acceptErr != nil {
			return
		}
		defer conn.Close()
		_, _, _, _ = ssh.NewServerConn(conn, serverConfig)
	}()

	host, portText, err := net.SplitHostPort(listener.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	port, err := strconv.Atoi(portText)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	fingerprint, err := ProbeFingerprint(ctx, Config{Host: host, Port: port, Username: "root"}, 2*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	if want := HostFingerprint(signer.PublicKey()); fingerprint != want {
		t.Fatalf("fingerprint = %q, want %q", fingerprint, want)
	}
}

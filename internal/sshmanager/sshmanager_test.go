package sshmanager

import (
	"crypto/ed25519"
	"crypto/rand"
	"strings"
	"testing"

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

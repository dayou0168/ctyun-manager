package security

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestPythonCompatibilityVectors(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	key := "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
	if err := os.WriteFile(filepath.Join(root, "master.key"), []byte(key), 0o600); err != nil {
		t.Fatal(err)
	}
	keyring, err := LoadKeyring(filepath.Join(root, "master.key"), "", "unused")
	if err != nil {
		t.Fatal(err)
	}
	plain, err := keyring.DecryptString("gAAAAABlU_EASvjfAEgdY1TkAoQ_TPPtWM_rsbcy6MbSRJLfe7hMkYoZg2yWKNnn7A20cNmv-bgeB-hVHNxlMj-eoe5JIAmp2SDiDzGOHID701p3KTAZ9zo=")
	if err != nil || plain != "天翼云兼容测试" {
		t.Fatalf("Fernet compatibility failed: plain=%q err=%v", plain, err)
	}
	passwordHash := "AAECAwQFBgcICQoLDA0OD4MZX/ka/QubR2DXgcurVU0Iu2yoCNglI1wgyOlK21xo"
	if !VerifyPassword("correct horse", passwordHash) || VerifyPassword("wrong", passwordHash) {
		t.Fatal("PBKDF2 compatibility failed")
	}
	sessionToken := "eyJzdWIiOiJhZG1pbiIsImlhdCI6MTcwMDAwMDAwMH0=.10026ed96cfc983ed8320738fe9d14279c48b2e9b7520454ea76ad83b17f46ca"
	session, ok := VerifySession(sessionToken, "test-session-secret", time.Unix(1_700_000_100, 0), 24*time.Hour)
	if !ok || session.Subject != "admin" {
		t.Fatal("session compatibility failed")
	}
}

func TestFernetEncryptRoundTrip(t *testing.T) {
	t.Parallel()
	keyring, err := LoadKeyring("missing", "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=", "fallback")
	if err != nil {
		t.Fatal(err)
	}
	token, err := keyring.EncryptString("Go 写入兼容测试")
	if err != nil {
		t.Fatal(err)
	}
	plain, err := keyring.DecryptString(token)
	if err != nil || plain != "Go 写入兼容测试" {
		t.Fatalf("plain = %q, err = %v", plain, err)
	}
}

func TestNormalizeTOTP(t *testing.T) {
	t.Parallel()
	value, err := NormalizeTOTP("otpauth://totp/Test?secret=jbsw%20y3dp%20ehpk3pxp")
	if err != nil || value != "JBSWY3DPEHPK3PXP" {
		t.Fatalf("value = %q, err = %v", value, err)
	}
	if _, err := NormalizeTOTP("not-base32!"); err == nil {
		t.Fatal("invalid secret accepted")
	}
}

func TestCurrentTOTP(t *testing.T) {
	code, err := CurrentTOTP("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", time.Unix(59, 0))
	if err != nil {
		t.Fatal(err)
	}
	if code != "287082" {
		t.Fatalf("code=%s", code)
	}
}

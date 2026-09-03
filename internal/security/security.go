package security

import (
	"bytes"
	"crypto/aes"
	"crypto/cipher"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha1"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base32"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"os"
	"strings"
	"time"
)

const passwordIterations = 210_000

var ErrInvalidToken = errors.New("invalid token")

type Keyring struct {
	keys [][]byte
}

func LoadKeyring(masterKeyPath, configuredKey, sessionSecret string) (*Keyring, error) {
	values := []string{}
	if data, err := os.ReadFile(masterKeyPath); err == nil {
		managed := string(bytes.TrimSpace(data))
		key, decodeErr := base64.URLEncoding.DecodeString(managed)
		if decodeErr != nil || len(key) != 32 {
			return nil, fmt.Errorf("managed Fernet key is invalid: %s", masterKeyPath)
		}
		values = append(values, managed)
	} else if !errors.Is(err, os.ErrNotExist) {
		return nil, fmt.Errorf("read master key: %w", err)
	}
	if configuredKey != "" {
		values = append(values, configuredKey)
	}
	fallback := sha256.Sum256([]byte(sessionSecret))
	values = append(values, base64.URLEncoding.EncodeToString(fallback[:]))

	keyring := &Keyring{}
	seen := map[string]bool{}
	for _, value := range values {
		if seen[value] {
			continue
		}
		key, err := base64.URLEncoding.DecodeString(value)
		if err != nil || len(key) != 32 {
			continue
		}
		seen[value] = true
		keyring.keys = append(keyring.keys, key)
	}
	if len(keyring.keys) == 0 {
		return nil, errors.New("no valid Fernet-compatible key was found")
	}
	return keyring, nil
}

func (k *Keyring) DecryptString(token string) (string, error) {
	if token == "" {
		return "", nil
	}
	for _, key := range k.keys {
		plain, err := decryptFernet(key, token)
		if err == nil {
			return string(plain), nil
		}
	}
	return "", ErrInvalidToken
}

func (k *Keyring) EncryptString(value string) (string, error) {
	if value == "" {
		return "", nil
	}
	if k == nil || len(k.keys) == 0 {
		return "", errors.New("encryption key is unavailable")
	}
	key := k.keys[0]
	plainBytes := []byte(value)
	padding := aes.BlockSize - len(plainBytes)%aes.BlockSize
	plain := append(plainBytes, bytes.Repeat([]byte{byte(padding)}, padding)...)
	iv := make([]byte, aes.BlockSize)
	if _, err := rand.Read(iv); err != nil {
		return "", fmt.Errorf("generate Fernet IV: %w", err)
	}
	block, err := aes.NewCipher(key[16:])
	if err != nil {
		return "", err
	}
	ciphertext := make([]byte, len(plain))
	cipher.NewCBCEncrypter(block, iv).CryptBlocks(ciphertext, plain)
	raw := make([]byte, 9, 9+len(iv)+len(ciphertext)+sha256.Size)
	raw[0] = 0x80
	binary.BigEndian.PutUint64(raw[1:9], uint64(time.Now().Unix()))
	raw = append(raw, iv...)
	raw = append(raw, ciphertext...)
	mac := hmac.New(sha256.New, key[:16])
	_, _ = mac.Write(raw)
	raw = append(raw, mac.Sum(nil)...)
	return base64.URLEncoding.EncodeToString(raw), nil
}

func decryptFernet(key []byte, token string) ([]byte, error) {
	raw, err := base64.URLEncoding.DecodeString(token)
	if err != nil || len(raw) < 1+8+aes.BlockSize+aes.BlockSize+sha256.Size || raw[0] != 0x80 {
		return nil, ErrInvalidToken
	}
	signed := raw[:len(raw)-sha256.Size]
	providedMAC := raw[len(raw)-sha256.Size:]
	mac := hmac.New(sha256.New, key[:16])
	_, _ = mac.Write(signed)
	if !hmac.Equal(mac.Sum(nil), providedMAC) {
		return nil, ErrInvalidToken
	}
	iv := raw[9 : 9+aes.BlockSize]
	ciphertext := raw[9+aes.BlockSize : len(raw)-sha256.Size]
	if len(ciphertext) == 0 || len(ciphertext)%aes.BlockSize != 0 {
		return nil, ErrInvalidToken
	}
	block, err := aes.NewCipher(key[16:])
	if err != nil {
		return nil, ErrInvalidToken
	}
	plain := make([]byte, len(ciphertext))
	cipher.NewCBCDecrypter(block, iv).CryptBlocks(plain, ciphertext)
	padding := int(plain[len(plain)-1])
	if padding < 1 || padding > aes.BlockSize || padding > len(plain) {
		return nil, ErrInvalidToken
	}
	for _, value := range plain[len(plain)-padding:] {
		if int(value) != padding {
			return nil, ErrInvalidToken
		}
	}
	return plain[:len(plain)-padding], nil
}

func VerifyPassword(password, encoded string) bool {
	raw, err := base64.StdEncoding.DecodeString(encoded)
	if err != nil || len(raw) <= 16 {
		return false
	}
	expected := raw[16:]
	actual := pbkdf2SHA256([]byte(password), raw[:16], passwordIterations, len(expected))
	return subtle.ConstantTimeCompare(actual, expected) == 1
}

func pbkdf2SHA256(password, salt []byte, iterations, size int) []byte {
	result := make([]byte, 0, size)
	var blockNumber uint32 = 1
	for len(result) < size {
		mac := hmac.New(sha256.New, password)
		_, _ = mac.Write(salt)
		var counter [4]byte
		binary.BigEndian.PutUint32(counter[:], blockNumber)
		_, _ = mac.Write(counter[:])
		u := mac.Sum(nil)
		block := append([]byte(nil), u...)
		for i := 1; i < iterations; i++ {
			mac = hmac.New(sha256.New, password)
			_, _ = mac.Write(u)
			u = mac.Sum(nil)
			for j := range block {
				block[j] ^= u[j]
			}
		}
		result = append(result, block...)
		blockNumber++
	}
	return result[:size]
}

func SignSession(subject, secret string, now time.Time) (string, error) {
	payload := struct {
		Subject string `json:"sub"`
		Issued  int64  `json:"iat"`
	}{Subject: subject, Issued: now.Unix()}
	data, err := json.Marshal(payload)
	if err != nil {
		return "", err
	}
	body := base64.URLEncoding.EncodeToString(data)
	mac := hmac.New(sha256.New, []byte(secret))
	_, _ = mac.Write([]byte(body))
	return body + "." + hex.EncodeToString(mac.Sum(nil)), nil
}

type Session struct {
	Subject string `json:"sub"`
	Issued  int64  `json:"iat"`
}

func VerifySession(token, secret string, now time.Time, maxAge time.Duration) (Session, bool) {
	var result Session
	separator := bytes.LastIndexByte([]byte(token), '.')
	if separator <= 0 {
		return result, false
	}
	body, signature := token[:separator], token[separator+1:]
	provided, err := hex.DecodeString(signature)
	if err != nil {
		return result, false
	}
	mac := hmac.New(sha256.New, []byte(secret))
	_, _ = mac.Write([]byte(body))
	if !hmac.Equal(mac.Sum(nil), provided) {
		return result, false
	}
	data, err := base64.URLEncoding.DecodeString(body)
	if err != nil || json.Unmarshal(data, &result) != nil || result.Subject == "" || result.Issued <= 0 {
		return Session{}, false
	}
	age := now.Unix() - result.Issued
	if time.Duration(age)*time.Second > maxAge {
		return Session{}, false
	}
	return result, true
}

func Mask(value string) string {
	characters := []rune(value)
	if len(characters) == 0 {
		return ""
	}
	if len(characters) <= 6 {
		return "***"
	}
	return string(characters[:2]) + "***" + string(characters[len(characters)-2:])
}

func NormalizeTOTP(value string) (string, error) {
	value = strings.TrimSpace(value)
	if strings.HasPrefix(value, "otpauth://") {
		parsed, err := url.Parse(value)
		if err != nil {
			return "", errors.New("invalid otpauth URI")
		}
		value = parsed.Query().Get("secret")
		if value == "" {
			return "", errors.New("otpauth URI is missing secret")
		}
	}
	normalized := strings.ToUpper(strings.ReplaceAll(value, " ", ""))
	if normalized == "" {
		return "", errors.New("TOTP secret is empty")
	}
	if _, err := base32.StdEncoding.WithPadding(base32.NoPadding).DecodeString(strings.TrimRight(normalized, "=")); err != nil {
		return "", errors.New("invalid TOTP secret")
	}
	return normalized, nil
}

func CurrentTOTP(secret string, now time.Time) (string, error) {
	normalized, err := NormalizeTOTP(secret)
	if err != nil {
		return "", err
	}
	key, err := base32.StdEncoding.WithPadding(base32.NoPadding).DecodeString(strings.TrimRight(normalized, "="))
	if err != nil {
		return "", err
	}
	var counter [8]byte
	binary.BigEndian.PutUint64(counter[:], uint64(now.Unix()/30))
	mac := hmac.New(sha1.New, key)
	_, _ = mac.Write(counter[:])
	digest := mac.Sum(nil)
	offset := digest[len(digest)-1] & 15
	code := (uint32(digest[offset])&127)<<24 | uint32(digest[offset+1])<<16 | uint32(digest[offset+2])<<8 | uint32(digest[offset+3])
	return fmt.Sprintf("%06d", code%1_000_000), nil
}

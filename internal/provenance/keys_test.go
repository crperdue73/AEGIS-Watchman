package provenance

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"os"
	"path/filepath"
	"testing"
)

func writeKeyFiles(t *testing.T, dir string) (keyPath string, pub ed25519.PublicKey) {
	t.Helper()
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	keyPath = filepath.Join(dir, "k.key")
	if err := os.WriteFile(keyPath, []byte(base64.StdEncoding.EncodeToString(priv)+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(keyPath+".pub", []byte(base64.StdEncoding.EncodeToString(pub)+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	return keyPath, pub
}

func TestDetachedSignVerifyRoundTrip(t *testing.T) {
	dir := t.TempDir()
	keyPath, pub := writeKeyFiles(t, dir)
	priv, err := LoadPrivateKey(keyPath)
	if err != nil {
		t.Fatalf("LoadPrivateKey: %v", err)
	}
	data := []byte("sha256  carbonato@6  carbonato.yaml\n")

	sig, err := SignDetached(priv, data)
	if err != nil {
		t.Fatalf("SignDetached: %v", err)
	}
	pub2, err := LoadPublicKey(keyPath + ".pub")
	if err != nil {
		t.Fatalf("LoadPublicKey: %v", err)
	}
	if err := VerifyDetached(pub2, data, sig); err != nil {
		t.Fatalf("VerifyDetached: %v", err)
	}

	// A one-byte change to the payload must break verification.
	if err := VerifyDetached(pub2, append(data, 'x'), sig); err == nil {
		t.Error("tampered payload should not verify")
	}
	// A different key must not verify.
	_, pubOther := writeKeyFiles(t, t.TempDir())
	if err := VerifyDetached(pubOther, data, sig); err == nil {
		t.Error("a signature should not verify under another key")
	}
	_ = pub
}

// A public key file given the private key path must work: operators commonly
// keep one file, and demanding a split should not be a footgun.
func TestLoadPublicKeyFromPrivateOrPub(t *testing.T) {
	dir := t.TempDir()
	keyPath, pub := writeKeyFiles(t, dir)

	fromKey, err := LoadPublicKey(keyPath) // given the PRIVATE key path
	if err != nil {
		t.Fatalf("LoadPublicKey(private): %v", err)
	}
	fromPub, err := LoadPublicKey(keyPath + ".pub")
	if err != nil {
		t.Fatalf("LoadPublicKey(.pub): %v", err)
	}
	if !fromKey.Equal(pub) || !fromPub.Equal(pub) {
		t.Errorf("derived public keys do not match the generated key")
	}
}

func TestLoadKeysRejectBadInput(t *testing.T) {
	dir := t.TempDir()
	bad := filepath.Join(dir, "bad.key")
	if err := os.WriteFile(bad, []byte("not base64!!\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadPrivateKey(bad); err == nil {
		t.Error("a non-base64 key should be rejected")
	}
	if _, err := LoadPublicKey(filepath.Join(dir, "absent.key")); err == nil {
		t.Error("a missing key should be rejected")
	}
}

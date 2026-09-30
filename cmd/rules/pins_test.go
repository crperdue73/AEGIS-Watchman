package main

import (
	"bytes"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/CRPerdue/agentids/internal/provenance"
	"github.com/CRPerdue/agentids/internal/rules"
)

func TestParseVerifyArgs(t *testing.T) {
	cases := []struct {
		in      []string
		want    verifyOpts
		wantErr bool
	}{
		{[]string{"rules/"}, verifyOpts{Paths: []string{"rules/"}}, false},
		{[]string{"--pins", "p.pins", "rules/"}, verifyOpts{Pins: "p.pins", Paths: []string{"rules/"}}, false},
		{[]string{"--pins=p.pins", "a.yaml"}, verifyOpts{Pins: "p.pins", Paths: []string{"a.yaml"}}, false},
		{[]string{"--pins", "p.pins", "--pubkey", "k.pub", "--sig", "s.sig", "d"}, verifyOpts{Pins: "p.pins", Pub: "k.pub", Sig: "s.sig", Paths: []string{"d"}}, false},
		{[]string{"--pins"}, verifyOpts{}, true},
		{[]string{"--pins="}, verifyOpts{}, true},
		{[]string{"--pubkey", "k.pub"}, verifyOpts{}, true},             // pubkey without pins
		{[]string{"--sig", "s.sig", "--pins", "p"}, verifyOpts{}, true}, // sig without pubkey
	}
	for _, c := range cases {
		got, err := parseVerifyArgs(c.in)
		if c.wantErr {
			if err == nil {
				t.Errorf("%v: expected an error", c.in)
			}
			continue
		}
		if err != nil {
			t.Errorf("%v: unexpected error: %v", c.in, err)
			continue
		}
		if got.Pins != c.want.Pins || got.Pub != c.want.Pub || got.Sig != c.want.Sig ||
			strings.Join(got.Paths, ",") != strings.Join(c.want.Paths, ",") {
			t.Errorf("%v: got %+v, want %+v", c.in, got, c.want)
		}
	}
}

// TestCheckPinsOKAndDrift is the acceptance path for a pin: a set that matches
// reports ok, and every kind of drift (changed bytes, a new pack, a removed
// pack) is a hard failure naming the file.
func TestCheckPinsOKAndDrift(t *testing.T) {
	dir := t.TempDir()
	pk := writeFile(t, dir, "a.yaml", oneRule)

	pins, err := rules.PinSet([]string{dir})
	if err != nil {
		t.Fatal(err)
	}
	pinsFile := filepath.Join(t.TempDir(), "packs.pins")
	if err := os.WriteFile(pinsFile, rules.FormatPins(pins), 0o600); err != nil {
		t.Fatal(err)
	}

	// Matching set: ok, no error.
	var buf bytes.Buffer
	if err := checkPins(&buf, pinsFile, []string{dir}); err != nil {
		t.Fatalf("matching set should pass: %v\n%s", err, buf.String())
	}
	if !strings.Contains(buf.String(), "pins: ok") {
		t.Errorf("expected an ok line, got:\n%s", buf.String())
	}

	// Changed bytes (a re-pin is required): must fail and name the file.
	if err := os.WriteFile(pk, []byte(strings.Replace(oneRule, "exe: sh", "exe: bash", 1)), 0o600); err != nil {
		t.Fatal(err)
	}
	buf.Reset()
	if err := checkPins(&buf, pinsFile, []string{dir}); err == nil {
		t.Fatalf("changed pack should fail the pin check")
	}
	if !strings.Contains(buf.String(), "changed") || !strings.Contains(buf.String(), "a.yaml") {
		t.Errorf("drift report should name the changed file:\n%s", buf.String())
	}

	// A new, unpinned pack: must fail.
	writeFile(t, dir, "b.yaml", "name: q\nrules: [{id: q.rule, severity: low, match: {type: process.exec, exe: ls}}]\n")
	buf.Reset()
	if err := checkPins(&buf, pinsFile, []string{dir}); err == nil {
		t.Fatal("an unpinned pack should fail the pin check")
	}
	if !strings.Contains(buf.String(), "unpinned") {
		t.Errorf("drift report should flag the unpinned pack:\n%s", buf.String())
	}
}

// writeKeyPair writes a base64 Ed25519 key pair the way `agentids-manifest
// keygen` does, returning the private-key path.
func writeKeyPair(t *testing.T, dir string) string {
	t.Helper()
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	keyPath := filepath.Join(dir, "ops.key")
	if err := os.WriteFile(keyPath, []byte(base64.StdEncoding.EncodeToString(priv)+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(keyPath+".pub", []byte(base64.StdEncoding.EncodeToString(pub)+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	return keyPath
}

// TestPinSignatureRoundTrip is the trust half of pinning: a signed pin verifies
// against its public key; a tampered pin, a wrong key, or a missing signature
// all fail. A pin whose provenance is unproven must never be trusted.
func TestPinSignatureRoundTrip(t *testing.T) {
	dir := t.TempDir()
	writeFile(t, dir, "a.yaml", oneRule)
	pins, err := rules.PinSet([]string{dir})
	if err != nil {
		t.Fatal(err)
	}
	pinsFile := filepath.Join(dir, "packs.pins")
	if err := os.WriteFile(pinsFile, rules.FormatPins(pins), 0o600); err != nil {
		t.Fatal(err)
	}

	keyPath := writeKeyPair(t, dir)
	priv, err := provenance.LoadPrivateKey(keyPath)
	if err != nil {
		t.Fatal(err)
	}
	sig, err := provenance.SignDetached(priv, rules.FormatPins(pins))
	if err != nil {
		t.Fatal(err)
	}
	sigFile := pinsFile + ".sig"
	if err := os.WriteFile(sigFile, []byte(sig+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	var buf bytes.Buffer
	// Correct key: ok. (LoadPublicKey is given the *private* key path on
	// purpose — it must derive the public half rather than demand a split file.)
	if err := checkPinSignature(&buf, pinsFile, keyPath, ""); err != nil {
		t.Fatalf("valid signature should verify: %v", err)
	}
	if !strings.Contains(buf.String(), "signature: ok") {
		t.Errorf("expected an ok line, got:\n%s", buf.String())
	}

	// Tampered pin file: the signature no longer covers it.
	if err := os.WriteFile(pinsFile, append(rules.FormatPins(pins), []byte("# tampered\n")...), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := checkPinSignature(&buf, pinsFile, keyPath, ""); err == nil {
		t.Error("a tampered pin file should fail signature verification")
	}

	// Wrong key: rejected.
	other := writeKeyPair(t, t.TempDir())
	if err := checkPinSignature(&buf, pinsFile, other, ""); err == nil {
		t.Error("a signature from another key must not verify")
	}

	// Missing signature file: a clear error, not a silent pass.
	if err := checkPinSignature(&buf, pinsFile, keyPath, filepath.Join(dir, "missing.sig")); err == nil {
		t.Error("a missing signature file must be an error")
	}
}

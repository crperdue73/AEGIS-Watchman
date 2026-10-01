package collector

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"os"
	"path/filepath"
	"testing"

	"github.com/CRPerdue/watchman/internal/provenance"
	"github.com/CRPerdue/watchman/internal/rules"
	"github.com/CRPerdue/watchman/internal/schema"
)

// pinnedRulesDir builds a temp rules dir (a copy of the shipped pack) and a pin
// file that matches it, so a test can then drift one or the other.
func pinnedRulesDir(t *testing.T) (dir, pinsPath string) {
	t.Helper()
	src, err := os.ReadFile("../../rules/carbonato.yaml")
	if err != nil {
		t.Fatal(err)
	}
	dir = t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "carbonato.yaml"), src, 0o644); err != nil {
		t.Fatal(err)
	}
	pins, err := rules.PinSet([]string{dir})
	if err != nil {
		t.Fatal(err)
	}
	pinsPath = filepath.Join(t.TempDir(), "packs.pins")
	if err := os.WriteFile(pinsPath, rules.FormatPins(pins), 0o644); err != nil {
		t.Fatal(err)
	}
	return dir, pinsPath
}

func TestBehaviorEngineEnforcesPins(t *testing.T) {
	dir, pinsPath := pinnedRulesDir(t)
	cfg := &Config{Behavior: Behavior{RulesDir: dir, Pins: pinsPath}}

	if _, err := NewBehaviorEngine(cfg, schema.Host{ID: "h"}, nil, &fakeSubmitter{}); err != nil {
		t.Fatalf("a matching set must start: %v", err)
	}

	// Drift the pack (one appended byte): refuse to start.
	f, err := os.OpenFile(filepath.Join(dir, "carbonato.yaml"), os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := f.WriteString("\n# drifted\n"); err != nil {
		t.Fatal(err)
	}
	f.Close()
	if _, err := NewBehaviorEngine(cfg, schema.Host{ID: "h"}, nil, &fakeSubmitter{}); err == nil {
		t.Fatal("a drifted pack must refuse to start")
	}
}

func TestBehaviorEngineRejectsUnpinnedPack(t *testing.T) {
	dir, pinsPath := pinnedRulesDir(t)
	// A new pack the pin file does not account for.
	extra := "name: extra\nrules: [{id: extra.rule, severity: low, match: {type: process.exec, exe: ls}}]\n"
	if err := os.WriteFile(filepath.Join(dir, "extra.yaml"), []byte(extra), 0o644); err != nil {
		t.Fatal(err)
	}
	cfg := &Config{Behavior: Behavior{RulesDir: dir, Pins: pinsPath}}
	if _, err := NewBehaviorEngine(cfg, schema.Host{ID: "h"}, nil, &fakeSubmitter{}); err == nil {
		t.Fatal("an unpinned pack must refuse to start")
	}
}

func TestBehaviorEngineVerifiesPinSignature(t *testing.T) {
	dir, pinsPath := pinnedRulesDir(t)

	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	keyPath := filepath.Join(t.TempDir(), "ops.key")
	if err := os.WriteFile(keyPath, []byte(base64.StdEncoding.EncodeToString(priv)+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(keyPath+".pub", []byte(base64.StdEncoding.EncodeToString(pub)+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	pinBytes, err := os.ReadFile(pinsPath)
	if err != nil {
		t.Fatal(err)
	}
	sig, err := provenance.SignDetached(priv, pinBytes)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(pinsPath+".sig", []byte(sig+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	cfg := &Config{Behavior: Behavior{RulesDir: dir, Pins: pinsPath, PinPubKey: keyPath + ".pub"}}
	if _, err := NewBehaviorEngine(cfg, schema.Host{ID: "h"}, nil, &fakeSubmitter{}); err != nil {
		t.Fatalf("a signed pin must start: %v", err)
	}

	// Tamper the pin file: the signature no longer covers it, so refuse — and
	// refuse for the signature, not just the (also-broken) drift.
	if err := os.WriteFile(pinsPath, append(pinBytes, []byte("# tampered\n")...), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := NewBehaviorEngine(cfg, schema.Host{ID: "h"}, nil, &fakeSubmitter{}); err == nil {
		t.Fatal("a tampered pin must refuse to start")
	}
}

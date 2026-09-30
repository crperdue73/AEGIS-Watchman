package collector

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The shipped pack, relative to this package's directory.
func shippedRulesDir() string { return filepath.Join("..", "..", "rules") }

// TestCheckBehaviorRulesOK: the shipped pack validates, and the counts are real.
func TestCheckBehaviorRulesOK(t *testing.T) {
	cfg := &Config{}
	cfg.Behavior.RulesDir = shippedRulesDir()
	packs, n, err := CheckBehaviorRules(cfg)
	if err != nil {
		t.Fatalf("CheckBehaviorRules: %v", err)
	}
	if packs != 1 || n < 1 {
		t.Errorf("got %d pack(s), %d rule(s), want 1 pack and >=1 rule", packs, n)
	}
}

// TestCheckBehaviorRulesUnsetIsNoop: no rules_dir is not an error — Layer 3 is
// simply off, and the check is a no-op.
func TestCheckBehaviorRulesUnsetIsNoop(t *testing.T) {
	cfg := &Config{}
	packs, n, err := CheckBehaviorRules(cfg)
	if err != nil || packs != 0 || n != 0 {
		t.Errorf("unset rules_dir = (%d,%d,%v), want (0,0,nil)", packs, n, err)
	}
}

// TestCheckBehaviorRulesEmptyDirIsError: an empty pack dir is the quiet "looks
// protected, detects nothing" case — the check refuses it, exactly as startup.
func TestCheckBehaviorRulesEmptyDirIsError(t *testing.T) {
	cfg := &Config{}
	cfg.Behavior.RulesDir = t.TempDir()
	if _, _, err := CheckBehaviorRules(cfg); err == nil {
		t.Fatal("an empty rules dir must be an error (Layer 3 would detect nothing)")
	}
}

// TestCheckBehaviorRulesMalformedPackIsError: -dry-run must catch a bad pack, so
// the most common first-run failure surfaces before the first live run.
func TestCheckBehaviorRulesMalformedPackIsError(t *testing.T) {
	dir := t.TempDir()
	bad := "name: bad\nrules:\n  - id: bad.rule\n    severity: nope\n"
	if err := os.WriteFile(filepath.Join(dir, "bad.yaml"), []byte(bad), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg := &Config{}
	cfg.Behavior.RulesDir = dir
	if _, _, err := CheckBehaviorRules(cfg); err == nil {
		t.Fatal("a malformed pack must be an error")
	}
}

// TestCheckBehaviorRulesPinDriftIsError: the pin is enforced by the check too,
// so a pack set that does not match its approved pin fails -dry-run.
func TestCheckBehaviorRulesPinDriftIsError(t *testing.T) {
	dir := t.TempDir()
	pack := "name: p\nversion: \"1\"\nrules:\n  - id: p.x\n    severity: low\n    match:\n      type: process.exec\n"
	if err := os.WriteFile(filepath.Join(dir, "p.yaml"), []byte(pack), 0o600); err != nil {
		t.Fatal(err)
	}
	pin := filepath.Join(t.TempDir(), "packs.pins")
	// A hash that cannot match the pack above.
	bogus := "0000000000000000000000000000000000000000000000000000000000000000  p@1  p.yaml\n"
	if err := os.WriteFile(pin, []byte(bogus), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg := &Config{}
	cfg.Behavior.RulesDir = dir
	cfg.Behavior.Pins = pin
	_, _, err := CheckBehaviorRules(cfg)
	if err == nil {
		t.Fatal("pin drift must be an error")
	}
	if !strings.Contains(err.Error(), "does not match pins") {
		t.Errorf("error = %v, want a pin-mismatch message", err)
	}
}

// TestLocalCollectorExampleConfigWorks guards the config the quickstart tells a
// stranger to run: it must load and its rules must check. The local example uses
// paths relative to the repository root (that is how the docs run it), so this
// resolves it the same way a user would.
func TestLocalCollectorExampleConfigWorks(t *testing.T) {
	root, err := filepath.Abs(filepath.Join("..", ".."))
	if err != nil {
		t.Fatal(err)
	}
	wd, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	// No t.Parallel in this package, so a process-wide chdir is safe here; it is
	// restored on the way out even if the test fails.
	if err := os.Chdir(root); err != nil {
		t.Fatal(err)
	}
	defer func() { _ = os.Chdir(wd) }()

	cfg, err := LoadConfig(filepath.Join(root, "examples", "collector.local.yaml"))
	if err != nil {
		t.Fatalf("examples/collector.local.yaml does not load: %v", err)
	}
	packs, n, err := CheckBehaviorRules(cfg)
	if err != nil {
		t.Fatalf("examples/collector.local.yaml rules do not check: %v", err)
	}
	if packs < 1 || n < 1 {
		t.Errorf("local example validated to %d pack(s), %d rule(s)", packs, n)
	}
}

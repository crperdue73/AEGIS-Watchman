package collector

import (
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/crperdue73/AEGIS-Watchman/internal/schema"
)

func TestLoadConfigDefaults(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "c.yaml")
	yaml := `
agent_roots:
  - path: /home/*/.openclaw/workspace-*
    include: ["SOUL.md", "skills/**/*.md"]
    exclude: ["**/*.tmp"]
report:
  endpoint: http://central.example/api/v1/events
`
	if err := os.WriteFile(p, []byte(yaml), 0o644); err != nil {
		t.Fatal(err)
	}
	cfg, err := LoadConfig(p)
	if err != nil {
		t.Fatalf("LoadConfig: %v", err)
	}
	if cfg.Hash != "sha256" {
		t.Errorf("hash default = %q", cfg.Hash)
	}
	if cfg.Watch.Mode != "poll" {
		t.Errorf("watch default = %q", cfg.Watch.Mode)
	}
	if cfg.Watch.PollInterval != 60*time.Second {
		t.Errorf("poll default = %v", cfg.Watch.PollInterval)
	}
	if cfg.Report.Interval != 30*time.Second {
		t.Errorf("report interval default = %v", cfg.Report.Interval)
	}
	if len(cfg.AgentRoots) != 1 {
		t.Fatalf("agent roots = %d", len(cfg.AgentRoots))
	}
}

func TestLoadConfigRejectsNoRoots(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "c.yaml")
	if err := os.WriteFile(p, []byte("hash: sha256\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadConfig(p); err == nil {
		t.Error("expected error for config with no agent_roots")
	}
}

func TestLoadConfigRejectsBadHash(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "c.yaml")
	yaml := "agent_roots:\n  - path: /x\nhash: md5\n"
	if err := os.WriteFile(p, []byte(yaml), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadConfig(p); err == nil {
		t.Error("expected error for unsupported hash")
	}
}

func TestCollectAllGlobsRoots(t *testing.T) {
	base := t.TempDir()
	// Two agent workspaces under base.
	for _, name := range []string{"workspace-a", "workspace-b"} {
		root := filepath.Join(base, name)
		if err := os.MkdirAll(filepath.Join(root, "skills"), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(root, "SOUL.md"), []byte("# "+name+"\n"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	cfg := &Config{
		AgentRoots: []AgentRoot{{
			Path:    filepath.Join(base, "workspace-*"),
			Include: []string{"SOUL.md"},
		}},
		Hash: "sha256",
	}
	got, err := CollectAll(cfg)
	if err != nil {
		t.Fatalf("CollectAll: %v", err)
	}
	if len(got) != 2 {
		t.Fatalf("want 2 collected roots, got %d", len(got))
	}
	// Deterministic (sorted) order.
	if got[0].Root >= got[1].Root {
		t.Errorf("roots not sorted: %s, %s", got[0].Root, got[1].Root)
	}
	for _, c := range got {
		if len(c.Inventory.Files) != 1 || c.Inventory.Files[0].Path != "SOUL.md" {
			t.Errorf("root %s inventory = %+v", c.Root, c.Inventory.Files)
		}
	}
}

func TestCollectAllNoMatchIsError(t *testing.T) {
	cfg := &Config{
		AgentRoots: []AgentRoot{{Path: "/nonexistent/workspace-*", Include: []string{"SOUL.md"}}},
		Hash:       "sha256",
	}
	if _, err := CollectAll(cfg); err == nil {
		t.Error("expected error when a glob matches nothing")
	}
}

func TestLoadConfigBehaviorRulesDir(t *testing.T) {
	dir := t.TempDir()
	rulesDir := filepath.Join(dir, "rules")
	if err := os.MkdirAll(rulesDir, 0o755); err != nil {
		t.Fatal(err)
	}
	auditLog := filepath.Join(dir, "audit.log")
	if err := os.WriteFile(auditLog, []byte("# empty feed\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	p := filepath.Join(dir, "c.yaml")
	yaml := `
agent_roots:
  - path: /x
behavior:
  rules_dir: ` + rulesDir + `
  audit_log: ` + auditLog + `
`
	if err := os.WriteFile(p, []byte(yaml), 0o644); err != nil {
		t.Fatal(err)
	}
	cfg, err := LoadConfig(p)
	if err != nil {
		t.Fatalf("LoadConfig: %v", err)
	}
	if cfg.Behavior.RulesDir != rulesDir {
		t.Errorf("rules_dir = %q", cfg.Behavior.RulesDir)
	}
	if cfg.Behavior.AuditLog != auditLog {
		t.Errorf("audit_log = %q", cfg.Behavior.AuditLog)
	}
}

// A configured audit_log that does not exist is a startup error, not a silent
// blind spot: the collector would otherwise enable Layer 3 and tail nothing.
func TestLoadConfigRejectsMissingAuditLog(t *testing.T) {
	dir := t.TempDir()
	rulesDir := filepath.Join(dir, "rules")
	if err := os.MkdirAll(rulesDir, 0o755); err != nil {
		t.Fatal(err)
	}
	p := filepath.Join(dir, "c.yaml")
	yaml := `
agent_roots:
  - path: /x
behavior:
  rules_dir: ` + rulesDir + `
  audit_log: ` + filepath.Join(dir, "no-such-audit.log") + `
`
	if err := os.WriteFile(p, []byte(yaml), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadConfig(p); err == nil {
		t.Error("expected error when behavior.audit_log does not exist")
	}
}

// An empty audit_log stays legal: it is the documented "rules loaded but idle"
// mode for validating a pack with no live feed. The missing-path rejection must
// not fail this case.
func TestLoadConfigAllowsEmptyAuditLog(t *testing.T) {
	dir := t.TempDir()
	rulesDir := filepath.Join(dir, "rules")
	if err := os.MkdirAll(rulesDir, 0o755); err != nil {
		t.Fatal(err)
	}
	p := filepath.Join(dir, "c.yaml")
	yaml := `
agent_roots:
  - path: /x
behavior:
  rules_dir: ` + rulesDir + `
`
	if err := os.WriteFile(p, []byte(yaml), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadConfig(p); err != nil {
		t.Fatalf("LoadConfig: %v", err)
	}
}

// audit_log is only meaningful when Layer 3 is on. With no rules_dir the
// collector runs Layers 1/2 only, and an audit_log (even a missing one) is not
// evaluated — so it must not fail validation.
func TestLoadConfigIgnoresAuditLogWithoutRulesDir(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "c.yaml")
	yaml := `
agent_roots:
  - path: /x
behavior:
  audit_log: ` + filepath.Join(dir, "no-such-audit.log") + `
`
	if err := os.WriteFile(p, []byte(yaml), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadConfig(p); err != nil {
		t.Fatalf("LoadConfig with no rules_dir: %v", err)
	}
}

func TestLoadConfigRejectsMissingRulesDir(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "c.yaml")
	yaml := `
agent_roots:
  - path: /x
behavior:
  rules_dir: ` + filepath.Join(dir, "nope") + `
`
	if err := os.WriteFile(p, []byte(yaml), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadConfig(p); err == nil {
		t.Error("expected error when behavior.rules_dir does not exist")
	}
}

func TestNewBehaviorEngineDisabledIsNil(t *testing.T) {
	cfg := &Config{}
	be, err := NewBehaviorEngine(cfg, schema.Host{ID: "h"}, nil, nil)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if be != nil {
		t.Error("engine should be nil when rules_dir is empty")
	}
	// A nil engine's Poll must be safe to call (the collector calls it
	// unconditionally each cycle).
	if got := be.Poll(); got != nil {
		t.Errorf("nil engine Poll = %v", got)
	}
}

func TestNewBehaviorEngineRejectsBadPack(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "bad.yaml"), []byte("name: bad\nrules:\n  - id: nope\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	cfg := &Config{Behavior: Behavior{RulesDir: dir}}
	if _, err := NewBehaviorEngine(cfg, schema.Host{ID: "h"}, nil, nil); err == nil {
		t.Error("expected error for a malformed rule pack")
	}
}

// A collector config with a bad eBPF block must fail validation at startup,
// not silently ignore the operator's intent.
func TestConfigRejectsBadEBPF(t *testing.T) {
	c := &Config{}
	c.Behavior.EBPF.Enabled = true
	c.Behavior.EBPF.WatchPaths = []string{""}
	if err := c.validate(); err == nil {
		t.Error("empty ebpf watch path should fail validation")
	}
}

func writeConfig(t *testing.T, body string) *Config {
	t.Helper()
	cfg, err := tryLoad(t, body)
	if err != nil {
		t.Fatalf("LoadConfig: %v", err)
	}
	return cfg
}

// tryLoad writes body to a temp config and returns LoadConfig's result.
func tryLoad(t *testing.T, body string) (*Config, error) {
	t.Helper()
	p := filepath.Join(t.TempDir(), "c.yaml")
	if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	return LoadConfig(p)
}

// behavior.pins must be coherent: needs rules_dir, and the file must exist.
func TestConfigPinsValidation(t *testing.T) {
	dir := t.TempDir()
	rulesDir := filepath.Join(dir, "rules")
	if err := os.MkdirAll(rulesDir, 0o755); err != nil {
		t.Fatal(err)
	}
	pins := filepath.Join(dir, "packs.pins")
	if err := os.WriteFile(pins, []byte("# empty\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	refuse := map[string]string{
		"pins without rules_dir":  "agent_roots:\n  - path: /x\nbehavior:\n  pins: " + pins + "\n",
		"missing pins file":       "agent_roots:\n  - path: /x\nbehavior:\n  rules_dir: " + rulesDir + "\n  pins: " + filepath.Join(dir, "nope.pins") + "\n",
		"pin_pubkey without pins": "agent_roots:\n  - path: /x\nbehavior:\n  rules_dir: " + rulesDir + "\n  pin_pubkey: k.pub\n",
	}
	for name, body := range refuse {
		if _, err := tryLoad(t, body); err == nil {
			t.Errorf("%s: expected a validation error", name)
		}
	}

	// A coherent block: accepted.
	cfg := writeConfig(t, "agent_roots:\n  - path: /x\nbehavior:\n  rules_dir: "+rulesDir+"\n  pins: "+pins+"\n")
	if cfg.Behavior.Pins != pins {
		t.Errorf("pins = %q, want %q", cfg.Behavior.Pins, pins)
	}
}

// TestConfigAcceptsCoherentPinsAndPubKey pins config.go:181 in the ACCEPT direction.
//
// TestConfigPinsValidation above checks that pin_pubkey WITHOUT pins is refused -- but it never
// builds the pair that is supposed to WORK, so flipping the :181 guard to != passed the suite:
// the invalid config was still refused, just by the neighbouring reads, while a fully valid
// config (pins + pin_pubkey, both files present) was rejected with the nonsense message
// "behavior.pin_pubkey requires behavior.pins" even though pins was set.
//
// This is the direction that matters operationally. Pins + a signing key is the hardened
// configuration -- signed rule packs, the thing an operator turns on deliberately. A build that
// refuses it with a misleading reason is a build that gets downgraded to unsigned packs to make
// the agent start, which is the opposite of what the operator asked for.
func TestConfigAcceptsCoherentPinsAndPubKey(t *testing.T) {
	dir := t.TempDir()
	rulesDir := filepath.Join(dir, "rules")
	if err := os.MkdirAll(rulesDir, 0o755); err != nil {
		t.Fatal(err)
	}
	pins := filepath.Join(dir, "packs.pins")
	if err := os.WriteFile(pins, []byte("# empty\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	pub := filepath.Join(dir, "pack.pub")
	if err := os.WriteFile(pub, []byte("k\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	cfg, err := tryLoad(t, "agent_roots:\n  - path: /x\nbehavior:\n  rules_dir: "+rulesDir+
		"\n  pins: "+pins+"\n  pin_pubkey: "+pub+"\n")
	if err != nil {
		t.Fatalf("a coherent pins+pin_pubkey block was refused: %v", err)
	}
	if cfg.Behavior.Pins != pins {
		t.Errorf("Pins = %q, want %q", cfg.Behavior.Pins, pins)
	}
	if cfg.Behavior.PinPubKey != pub {
		t.Errorf("PinPubKey = %q, want %q", cfg.Behavior.PinPubKey, pub)
	}
}

// TestConfigRefusesMissingPubKeyFile pins config.go:184 -- the os.Stat check on pin_pubkey.
//
// The mutant (err != nil -> err == nil) inverts the check: a pin_pubkey path that DOES NOT EXIST
// is accepted, and one that does exist is refused. Accepting a missing key is the dangerous half:
// the agent starts with signing configuration it cannot possibly verify against, so every rule
// pack is either silently unverified or the whole chain is dead while the config claims it is on.
// A trust root that fails open is worse than no trust root, because nobody goes looking for it.
func TestConfigRefusesMissingPubKeyFile(t *testing.T) {
	dir := t.TempDir()
	rulesDir := filepath.Join(dir, "rules")
	if err := os.MkdirAll(rulesDir, 0o755); err != nil {
		t.Fatal(err)
	}
	pins := filepath.Join(dir, "packs.pins")
	if err := os.WriteFile(pins, []byte("# empty\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	body := "agent_roots:\n  - path: /x\nbehavior:\n  rules_dir: " + rulesDir +
		"\n  pins: " + pins + "\n  pin_pubkey: " + filepath.Join(dir, "absent.pub") + "\n"
	if _, err := tryLoad(t, body); err == nil {
		t.Fatal("a pin_pubkey pointing at a file that does not exist was accepted; the agent " +
			"would start claiming signed-pack verification it cannot perform")
	}
}

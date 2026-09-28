package collector

import (
	"os"
	"path/filepath"
	"testing"
	"time"
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

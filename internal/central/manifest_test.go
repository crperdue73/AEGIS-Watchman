package central

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/crperdue73/AEGIS-Watchman/internal/collector"
	"github.com/crperdue73/AEGIS-Watchman/internal/provenance"
	"github.com/crperdue73/AEGIS-Watchman/internal/schema"
)

// writeManifest signs an approval manifest for root and drops it in dir.
func writeManifest(t *testing.T, dir, agentID, root string, files map[string]string, priv ed25519.PrivateKey) string {
	t.Helper()
	m := provenance.BuildManifest(agentID, root, files)
	if err := m.Sign(priv); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(dir, agentID+".manifest.json")
	if err := m.SaveManifest(path); err != nil {
		t.Fatal(err)
	}
	return path
}

func writeTrustKeys(t *testing.T, dir string, named map[string]string) string {
	t.Helper()
	raw, _ := json.Marshal(named)
	path := filepath.Join(dir, "trust-keys.json")
	if err := os.WriteFile(path, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func testInventory(root string, files map[string]string) *collector.Inventory {
	inv := &collector.Inventory{Root: root}
	for p, h := range files {
		inv.Files = append(inv.Files, collector.FileState{Path: p, SHA256: h})
	}
	return inv
}

func TestManifestRegistryLoadsAndVerifiesSignatures(t *testing.T) {
	pub, priv, _ := ed25519.GenerateKey(rand.Reader)
	dir := t.TempDir()
	writeManifest(t, dir, "vivi", "/w/vivi", map[string]string{"SOUL.md": "aaa"}, priv)
	keys := []provenance.TrustKey{{Name: "ops", Key: pub}}

	reg, err := NewManifestRegistry(dir, keys)
	if err != nil {
		t.Fatalf("load registry: %v", err)
	}
	if _, ok := reg.ForRoot("/w/vivi"); !ok {
		t.Error("manifest for /w/vivi not found")
	}
	// Trailing slash must still match.
	if _, ok := reg.ForRoot("/w/vivi/"); !ok {
		t.Error("trailing-slash root did not match")
	}
	if _, ok := reg.ForRoot("/w/other"); ok {
		t.Error("unexpected manifest match for an unlisted root")
	}
}

func TestManifestRegistryRejectsForgedManifest(t *testing.T) {
	pub, _, _ := ed25519.GenerateKey(rand.Reader)
	_, attackerPriv, _ := ed25519.GenerateKey(rand.Reader)
	dir := t.TempDir()
	// Signed by the attacker, not our trusted key.
	writeManifest(t, dir, "vivi", "/w/vivi", map[string]string{"SOUL.md": "aaa"}, attackerPriv)

	_, err := NewManifestRegistry(dir, []provenance.TrustKey{{Name: "ops", Key: pub}})
	if err == nil {
		t.Fatal("registry accepted a manifest signed by an untrusted key")
	}
}

func TestManifestRegistryNoTrustKeysStillLoads(t *testing.T) {
	_, priv, _ := ed25519.GenerateKey(rand.Reader)
	dir := t.TempDir()
	writeManifest(t, dir, "vivi", "/w/vivi", map[string]string{"SOUL.md": "aaa"}, priv)
	reg, err := NewManifestRegistry(dir, nil)
	if err != nil {
		t.Fatalf("load with no trust keys: %v", err)
	}
	if _, ok := reg.ForRoot("/w/vivi"); !ok {
		t.Error("manifest not loaded when no trust keys configured")
	}
}

func TestLoadTrustKeys(t *testing.T) {
	pub, _, _ := ed25519.GenerateKey(rand.Reader)
	dir := t.TempDir()
	path := writeTrustKeys(t, dir, map[string]string{
		"ops-2026-q3": base64.StdEncoding.EncodeToString(pub),
	})
	keys, err := LoadTrustKeys(path)
	if err != nil {
		t.Fatal(err)
	}
	if len(keys) != 1 || keys[0].Name != "ops-2026-q3" {
		t.Errorf("bad trust keys: %+v", keys)
	}
	// Missing file is not an error.
	if k, err := LoadTrustKeys(filepath.Join(dir, "missing.json")); err != nil || k != nil {
		t.Errorf("missing trust-keys should be nil,nil; got %v,%v", k, err)
	}
	// Empty path is not an error.
	if k, err := LoadTrustKeys(""); err != nil || k != nil {
		t.Errorf("empty trust-keys path should be nil,nil; got %v,%v", k, err)
	}
}

func TestServiceEmitsProvenanceForUnapprovedDrop(t *testing.T) {
	pub, priv, _ := ed25519.GenerateKey(rand.Reader)
	dir := t.TempDir()
	// Approved: SOUL.md + IDENTITY.md only.
	writeManifest(t, dir, "vivi", "/w/vivi", map[string]string{
		"SOUL.md": "aaa", "IDENTITY.md": "bbb",
	}, priv)
	reg, err := NewManifestRegistry(dir, []provenance.TrustKey{{Name: "ops", Key: pub}})
	if err != nil {
		t.Fatal(err)
	}

	svc := NewService(NewMemoryStore())
	svc.SetManifests(reg)

	host := schema.Host{ID: "h1", Name: "builder-01"}
	agent := schema.Agent{ID: "vivi", Root: "/w/vivi"}

	// First ingest: approved files + one DROPPED skill (unlisted).
	inv := testInventory("/w/vivi", map[string]string{
		"SOUL.md":            "aaa",
		"IDENTITY.md":        "bbb",
		"skills/c2/SKILL.md": "zzz",
	})
	res, err := svc.Ingest(context.Background(), host, agent, inv)
	if err != nil {
		t.Fatal(err)
	}
	// FirstSeen, so no Layer 1 drift — but Layer 2 must still fire.
	if !res.FirstSeen {
		t.Error("expected FirstSeen on first ingest")
	}
	var l2 []schema.Event
	for _, e := range res.Events {
		if e.Event == schema.EventProvenanceUntrusted {
			l2 = append(l2, e)
		}
	}
	if len(l2) != 1 {
		t.Fatalf("expected 1 provenance event on the dropped skill, got %d: %+v", len(l2), res.Events)
	}
	if l2[0].File.Path != "skills/c2/SKILL.md" {
		t.Errorf("wrong path: %s", l2[0].File.Path)
	}
	if l2[0].Severity != schema.SeverityHigh {
		t.Errorf("dropped skill should be high severity, got %s", l2[0].Severity)
	}
	if l2[0].File.SHA256After != "zzz" || l2[0].File.SHA256Before != "" {
		t.Errorf("hashes wrong: %+v", l2[0].File)
	}
}

func TestServiceProvenanceSilentWhenClean(t *testing.T) {
	pub, priv, _ := ed25519.GenerateKey(rand.Reader)
	dir := t.TempDir()
	writeManifest(t, dir, "vivi", "/w/vivi", map[string]string{"SOUL.md": "aaa"}, priv)
	reg, _ := NewManifestRegistry(dir, []provenance.TrustKey{{Name: "ops", Key: pub}})

	svc := NewService(NewMemoryStore())
	svc.SetManifests(reg)

	inv := testInventory("/w/vivi", map[string]string{"SOUL.md": "aaa"})
	res, err := svc.Ingest(context.Background(), schema.Host{ID: "h1"}, schema.Agent{ID: "vivi", Root: "/w/vivi"}, inv)
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Events) != 0 {
		t.Errorf("clean workspace should produce no events, got %+v", res.Events)
	}
}

func TestServiceProvenanceRequireApproval(t *testing.T) {
	pub, _, _ := ed25519.GenerateKey(rand.Reader)
	dir := t.TempDir()
	reg, _ := NewManifestRegistry(dir, []provenance.TrustKey{{Name: "ops", Key: pub}}) // empty dir
	reg.requireApproval = true

	svc := NewService(NewMemoryStore())
	svc.SetManifests(reg)

	inv := testInventory("/w/unapproved", map[string]string{"SOUL.md": "aaa", "IDENTITY.md": "bbb"})
	res, err := svc.Ingest(context.Background(), schema.Host{ID: "h1"}, schema.Agent{ID: "x", Root: "/w/unapproved"}, inv)
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Events) != 2 {
		t.Fatalf("require_approval should flag every file for an unapproved agent, got %+v", res.Events)
	}
	for _, e := range res.Events {
		if e.Event != schema.EventProvenanceUntrusted {
			t.Errorf("expected provenance.untrusted, got %s", e.Event)
		}
	}
}

func TestServiceWithoutManifestsSkipsLayer2(t *testing.T) {
	svc := NewService(NewMemoryStore())
	inv := testInventory("/w/vivi", map[string]string{"SOUL.md": "aaa"})
	res, err := svc.Ingest(context.Background(), schema.Host{ID: "h1"}, schema.Agent{ID: "vivi", Root: "/w/vivi"}, inv)
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Events) != 0 {
		t.Errorf("no manifests -> no Layer 2 events, got %+v", res.Events)
	}
}

func TestServiceProvenanceRunsOnEveryIngest(t *testing.T) {
	pub, priv, _ := ed25519.GenerateKey(rand.Reader)
	dir := t.TempDir()
	writeManifest(t, dir, "vivi", "/w/vivi", map[string]string{"SOUL.md": "aaa"}, priv)
	reg, _ := NewManifestRegistry(dir, []provenance.TrustKey{{Name: "ops", Key: pub}})
	svc := NewService(NewMemoryStore())
	svc.SetManifests(reg)

	host := schema.Host{ID: "h1"}
	agent := schema.Agent{ID: "vivi", Root: "/w/vivi"}

	// Baseline ingest: clean.
	if res, err := svc.Ingest(context.Background(), host, agent, testInventory("/w/vivi", map[string]string{"SOUL.md": "aaa"})); err != nil || len(res.Events) != 0 {
		t.Fatalf("clean baseline: events=%v err=%v", res, err)
	}
	// Second ingest: a new file appears AND it is not in the manifest. Both
	// layers fire: Layer 1 sees the file as an addition (identity.drift), Layer 2
	// sees it as unapproved (provenance.untrusted). SOUL.md is unchanged, so it
	// contributes nothing.
	res, err := svc.Ingest(context.Background(), host, agent, testInventory("/w/vivi", map[string]string{
		"SOUL.md":           "aaa",
		"skills/x/SKILL.md": "new",
	}))
	if err != nil {
		t.Fatal(err)
	}
	var classes []string
	for _, e := range res.Events {
		classes = append(classes, e.Event+":"+e.File.Path)
	}
	if len(res.Events) != 2 {
		t.Fatalf("expected 2 events (drift + provenance), got %+v", res.Events)
	}
	want := map[string]bool{
		"identity.drift:skills/x/SKILL.md":       false,
		"provenance.untrusted:skills/x/SKILL.md": false,
	}
	for _, c := range classes {
		if _, ok := want[c]; !ok {
			t.Errorf("unexpected event %q", c)
		}
		want[c] = true
	}
	for c, seen := range want {
		if !seen {
			t.Errorf("missing event %q", c)
		}
	}
}

func TestConfigManifestRegistry(t *testing.T) {
	pub, priv, _ := ed25519.GenerateKey(rand.Reader)
	dir := t.TempDir()
	writeManifest(t, dir, "vivi", "/w/vivi", map[string]string{"SOUL.md": "aaa"}, priv)
	keyPath := writeTrustKeys(t, dir, map[string]string{"ops": base64.StdEncoding.EncodeToString(pub)})

	cfg := DefaultConfig()
	cfg.Manifests = Manifests{Dir: dir, TrustKeys: keyPath, RequireApproval: true}
	reg, err := cfg.ManifestRegistry()
	if err != nil {
		t.Fatal(err)
	}
	if reg == nil {
		t.Fatal("expected a registry")
	}
	if !reg.requireApproval {
		t.Error("require_approval not propagated")
	}

	// Empty dir -> nil registry, no error.
	cfg2 := DefaultConfig()
	if reg, err := cfg2.ManifestRegistry(); reg != nil || err != nil {
		t.Errorf("empty dir should disable Layer 2: reg=%v err=%v", reg, err)
	}

	// trust_keys without dir is a config error.
	cfg3 := DefaultConfig()
	cfg3.Manifests.TrustKeys = keyPath
	if err := cfg3.validate(); err == nil {
		t.Error("expected validation error for trust_keys without dir")
	}
}

func TestConfigManifestRegistryBadTrustKeys(t *testing.T) {
	dir := t.TempDir()
	bad := filepath.Join(dir, "keys.json")
	_ = os.WriteFile(bad, []byte(`{"ops":"not-base64!!"}`), 0o600)
	cfg := DefaultConfig()
	cfg.Manifests = Manifests{Dir: dir, TrustKeys: bad}
	if _, err := cfg.ManifestRegistry(); err == nil {
		t.Error("expected error for invalid trust key")
	}
}

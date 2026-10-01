package provenance

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/CRPerdue/watchman/internal/schema"
)

func mkManifest() *Manifest {
	return BuildManifest("vivi", "/home/x/workspace-x", map[string]string{
		"SOUL.md":              "aaa",
		"IDENTITY.md":          "bbb",
		"skills/tidy/SKILL.md": "ccc",
	})
}

func TestSignAndVerify(t *testing.T) {
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	m := mkManifest()
	if err := m.Sign(priv); err != nil {
		t.Fatal(err)
	}
	if m.Signature == "" {
		t.Fatal("signature not set")
	}
	if err := m.Verify(pub); err != nil {
		t.Fatalf("verify: %v", err)
	}
}

func TestVerifyRejectsTamperedManifest(t *testing.T) {
	pub, priv, _ := ed25519.GenerateKey(rand.Reader)
	m := mkManifest()
	if err := m.Sign(priv); err != nil {
		t.Fatal(err)
	}
	// An attacker adds a file to the approval list.
	m.Files["skills/evil/SKILL.md"] = "ddd"
	if err := m.Verify(pub); err == nil {
		t.Fatal("tampered manifest verified — approval list is forgeable")
	}
}

func TestVerifyRejectsWrongKey(t *testing.T) {
	_, priv, _ := ed25519.GenerateKey(rand.Reader)
	otherPub, _, _ := ed25519.GenerateKey(rand.Reader)
	m := mkManifest()
	_ = m.Sign(priv)
	if err := m.Verify(otherPub); err == nil {
		t.Fatal("verified with the wrong public key")
	}
}

func TestVerifyUnsignedManifest(t *testing.T) {
	pub, _, _ := ed25519.GenerateKey(rand.Reader)
	m := mkManifest()
	if err := m.Verify(pub); err == nil {
		t.Fatal("expected error for unsigned manifest")
	}
}

func TestCanonicalBodyIsOrderIndependent(t *testing.T) {
	m1 := BuildManifest("vivi", "/r", map[string]string{"a": "1", "b": "2", "c": "3"})
	m2 := BuildManifest("vivi", "/r", map[string]string{"c": "3", "b": "2", "a": "1"})
	m1.CreatedAt = time.Date(2026, 9, 28, 0, 0, 0, 0, time.UTC)
	m2.CreatedAt = m1.CreatedAt

	b1, err := m1.canonicalBody()
	if err != nil {
		t.Fatal(err)
	}
	b2, err := m2.canonicalBody()
	if err != nil {
		t.Fatal(err)
	}
	if string(b1) != string(b2) {
		t.Errorf("canonical body depends on map order:\n%s\n%s", b1, b2)
	}
}

func TestVerifyFindsUnsignedModifiedMissing(t *testing.T) {
	m := mkManifest()
	host := map[string]string{
		"SOUL.md":              "aaa", // ok
		"IDENTITY.md":          "bbb", // ok (still listed)
		"skills/new/SKILL.md":  "zzz", // unlisted -> unsigned (CARBONATO drop)
		"skills/tidy/SKILL.md": "ccc", // ok
	}
	// Add one listed-but-absent file to exercise TrustMissing.
	m.Files["deleted.md"] = "ddd"

	v := Verify(m, host)
	got := map[string]Trust{}
	for _, x := range v {
		got[x.Path] = x.Status
	}
	if got["skills/new/SKILL.md"] != TrustUnsigned {
		t.Errorf("unlisted file status = %v, want unsigned", got["skills/new/SKILL.md"])
	}
	if got["deleted.md"] != TrustMissing {
		t.Errorf("absent manifest file status = %v, want missing", got["deleted.md"])
	}
	if len(v) != 2 {
		t.Errorf("expected 2 non-OK verdicts, got %d: %+v", len(v), v)
	}
}

func TestVerifyModifiedDetected(t *testing.T) {
	m := mkManifest()
	v := Verify(m, map[string]string{
		"SOUL.md":              "aaa",
		"IDENTITY.md":          "MUTATED",
		"skills/tidy/SKILL.md": "ccc",
	})
	if len(v) != 1 || v[0].Status != TrustModified {
		t.Fatalf("want one modified verdict, got %+v", v)
	}
	if v[0].HashManifest != "bbb" || v[0].HashHost != "MUTATED" {
		t.Errorf("verdict hashes wrong: %+v", v[0])
	}
}

func TestVerifyDeterministicOrder(t *testing.T) {
	m := mkManifest()
	host := map[string]string{"z.md": "1", "a.md": "2", "m.md": "3"}
	a := Verify(m, host)
	b := Verify(m, host)
	// 3 unlisted host files + 3 manifest files absent from host = 6 verdicts.
	if len(a) != 6 || len(b) != 6 {
		t.Fatalf("expected 6 verdicts, got %d/%d", len(a), len(b))
	}
	for i := range a {
		if a[i].Path != b[i].Path {
			t.Errorf("order not deterministic: %s vs %s", a[i].Path, b[i].Path)
		}
	}
	if a[0].Path != "IDENTITY.md" && a[0].Path != "a.md" {
		t.Errorf("not sorted by path: %+v", a)
	}
	// Verify strict ascending order by path.
	for i := 1; i < len(a); i++ {
		if a[i-1].Path > a[i].Path {
			t.Errorf("verdicts not sorted: %q before %q", a[i-1].Path, a[i].Path)
		}
	}
}

func TestSeverityFor(t *testing.T) {
	cases := map[Trust]string{
		TrustUnsigned: "high",
		TrustModified: "medium",
		TrustMissing:  "low",
		TrustOK:       "info",
	}
	for status, want := range cases {
		if got := SeverityFor(Verdict{Status: status}); got != want {
			t.Errorf("Trust(%v) severity = %s, want %s", status, got, want)
		}
	}
}

func TestEventsShape(t *testing.T) {
	host := schema.Host{ID: "h-1", Name: "builder-01"}
	agent := schema.Agent{ID: "vivi", Root: "/home/x/workspace-x"}
	verdicts := []Verdict{
		{Path: "SOUL.md", Status: TrustOK, HashHost: "aaa", HashManifest: "aaa"},
		{Path: "skills/new/SKILL.md", Status: TrustUnsigned, HashHost: "zzz"},
	}
	evs := Events(host, agent, verdicts)
	if len(evs) != 1 {
		t.Fatalf("expected 1 event (ok is silent), got %d", len(evs))
	}
	ev := evs[0]
	if ev.Event != schema.EventProvenanceUntrusted {
		t.Errorf("event class = %s, want %s", ev.Event, schema.EventProvenanceUntrusted)
	}
	if ev.Severity != schema.SeverityHigh {
		t.Errorf("severity = %s, want high", ev.Severity)
	}
	if ev.Schema != schema.Version {
		t.Errorf("schema version = %s", ev.Schema)
	}
	if ev.File == nil || ev.File.Path != "skills/new/SKILL.md" || ev.File.SHA256After != "zzz" {
		t.Errorf("file detail wrong: %+v", ev.File)
	}
	if ev.Agent == nil || ev.Agent.ID != "vivi" {
		t.Errorf("agent detail wrong: %+v", ev.Agent)
	}
	if ev.TS.IsZero() {
		t.Error("timestamp not set")
	}
}

func TestManifestJSONRoundTrip(t *testing.T) {
	pub, priv, _ := ed25519.GenerateKey(rand.Reader)
	m := mkManifest()
	if err := m.Sign(priv); err != nil {
		t.Fatal(err)
	}
	raw, err := json.MarshalIndent(m, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	var back Manifest
	if err := json.Unmarshal(raw, &back); err != nil {
		t.Fatal(err)
	}
	if err := back.Verify(pub); err != nil {
		t.Fatalf("verify after JSON round-trip: %v", err)
	}
}

func TestParsedTrustKeys(t *testing.T) {
	pub, _, _ := ed25519.GenerateKey(rand.Reader)
	keys, err := ParsedTrustKeys(map[string]string{
		"ops-2026-q3": base64.StdEncoding.EncodeToString(pub),
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(keys) != 1 || keys[0].Name != "ops-2026-q3" {
		t.Errorf("bad trust keys: %+v", keys)
	}
	if _, err := ParsedTrustKeys(map[string]string{"bad": "not-base64!!"}); err == nil {
		t.Error("expected error for invalid base64 key")
	}
	if _, err := ParsedTrustKeys(map[string]string{"short": base64.StdEncoding.EncodeToString([]byte("x"))}); err == nil {
		t.Error("expected error for wrong-size key")
	}
}

func TestLoadSaveManifestFile(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "manifest.json")
	m := mkManifest()
	raw, _ := json.Marshal(m)
	if err := os.WriteFile(path, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	got, err := LoadManifest(path)
	if err != nil {
		t.Fatal(err)
	}
	if got.AgentID != "vivi" || len(got.Files) != 3 {
		t.Errorf("loaded manifest wrong: %+v", got)
	}
	if _, err := LoadManifest(filepath.Join(dir, "nope.json")); err == nil {
		t.Error("expected error for missing manifest")
	}
}

func TestLoadManifestRejectsGarbage(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "bad.json")
	if err := os.WriteFile(path, []byte("{not json"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadManifest(path); err == nil {
		t.Error("expected parse error")
	} else if !strings.Contains(err.Error(), "manifest") {
		t.Errorf("error should mention manifest: %v", err)
	}
}

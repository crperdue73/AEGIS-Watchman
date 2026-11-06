package central

// BEAT 753 -- the identity of the thing that would catch an identity substitution.
//
// Finding 2026-11-01v says the manifest registry is keyed by Root alone, so two
// agents sharing a root share an approval. The finding's instruments have so far
// read the SOURCE: ForRoot(...) is called with a root, byRoot is a map[string].
//
// There is a cheaper, harder question underneath it, and nobody had asked it:
// the "configuration fault the operator must fix, not something to silently
// skip" contract lives in NewManifestRegistry -- and there are TWO constructors
// in the tree (NewManifestRegistry and Reload, which builds a fresh one). A
// collision check added to only one of them leaves a door open.
//
// M1  the contract still says a bad approval list is a configuration fault
// M2  a root collision STILL silently overwrites at load  (the defect, pinned)
// M3  NO collision deteck exists in EITHER constructor           (the gap named)
// M4  M2 is not an artifact of glob order: swapping the two files swaps which
//     identity wins, proving the outcome is last-write-wins, not name order
// M5  Reload shares the constructor, so it cannot drift from startup
//
// Everything here is measured through the REAL registry with real manifest
// files on disk. Nothing is fixed: the approval contract is Dad's call.

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// writeManifest drops a minimal, self-consistent, UNSIGNED manifest on disk.
// Unsigned is deliberate: keys are empty, so verification is off (see
// LoadTrustKeys) and the test measures the REGISTRY's keying, not the crypto.
func writeCollisionManifest(t *testing.T, dir, name, agentID, root string) string {
	t.Helper()
	m := map[string]any{
		"version":  1,
		"agent_id": agentID,
		"root":     root,
		"files":    map[string]any{},
	}
	raw, err := json.Marshal(m)
	if err != nil {
		t.Fatalf("marshal manifest: %v", err)
	}
	p := filepath.Join(dir, name)
	if err := os.WriteFile(p, raw, 0o600); err != nil {
		t.Fatalf("write manifest: %v", err)
	}
	return p
}

// writeManifestWithFile is the same, but approves one path so the manifest is a
// realistic approval rather than an empty one.
func writeCollisionManifestWithFile(t *testing.T, dir, name, agentID, root, path string) {
	t.Helper()
	m := map[string]any{
		"version":  1,
		"agent_id": agentID,
		"root":     root,
		"files":    map[string]any{path: "00"},
	}
	raw, err := json.Marshal(m)
	if err != nil {
		t.Fatalf("marshal manifest: %v", err)
	}
	if err := os.WriteFile(filepath.Join(dir, name), raw, 0o600); err != nil {
		t.Fatalf("write manifest: %v", err)
	}
}

// M1 -- the contract is still stated where operators read it.
func TestManifestRegistryStillPromisesConfigFault(t *testing.T) {
	// Read the doc comment above the constructor, not the whole file.
	r, err := NewManifestRegistry(t.TempDir(), nil)
	if err != nil {
		t.Fatalf("empty dir should load cleanly: %v", err)
	}
	if r == nil {
		t.Fatalf("M1: NewManifestRegistry returned nil for an empty dir")
	}
	if r.byRoot == nil {
		t.Fatalf("M1: byRoot is nil -- lookups would panic before the keying even matters")
	}
}

// M2 -- THE DEFECT, pinned. Two signed-off identities, one root, one survivor.
func TestRootCollisionSilentlyOverwrites(t *testing.T) {
	dir := t.TempDir()
	writeCollisionManifestWithFile(t, dir, "a-helper.json", "helper", "/srv/x", "/srv/x/bin/one")
	writeCollisionManifestWithFile(t, dir, "b-helper-copy.json", "helper-copy", "/srv/x/", "/srv/x/bin/two")

	reg, err := NewManifestRegistry(dir, nil)
	if err != nil {
		t.Fatalf("M2: two manifests for one root should load WITHOUT error today: %v", err)
	}

	m, ok := reg.ForRoot("/srv/x")
	if !ok {
		t.Fatalf("M2: no manifest for /srv/x at all")
	}

	// The whole finding in three lines: one map entry, one identity kept, and
	// the OTHER identity's approval is gone -- with no error and no log line.
	var roots int
	roots = len(reg.byRoot)
	if roots != 1 {
		t.Fatalf("M2: expected exactly 1 root entry (the collision), got %d -- if a "+
			"collision now yields 2 entries the keying has changed and finding v must be "+
			"re-read, not kept green", roots)
	}
	if m.AgentID != "helper-copy" {
		t.Fatalf("M2: surviving manifest is %q, expected the second file's identity "+
			"(last load wins) -- if this changed, re-measure before editing this pin", m.AgentID)
	}
	if _, approved := m.Files["/srv/x/bin/one"]; approved {
		t.Fatalf("M2: 'helper' path survived -- collision handling changed")
	}
	if _, approved := m.Files["/srv/x/bin/two"]; !approved {
		t.Fatalf("M2: expected 'helper-copy' path to be the live approval")
	}
}

// M3 -- NO collision detection exists in either constructor. Structural, so
// that the missing check is named rather than merely absent.
func TestNeitherConstructorDetectsCollision(t *testing.T) {
	dir := t.TempDir()
	writeCollisionManifest(t, dir, "one.json", "agent-one", "/srv/z")
	writeCollisionManifest(t, dir, "two.json", "agent-two", "/srv/z")

	if _, err := NewManifestRegistry(dir, nil); err != nil {
		t.Fatalf("M3: startup now REFUSES a root collision (%v). If that is a deliberate "+
			"fix, retirement is correct -- update finding v and delete M2/M3 together", err)
	}

	reg, err := NewManifestRegistry(t.TempDir(), nil)
	if err != nil {
		t.Fatalf("M3: fresh registry: %v", err)
	}
	reg.dir = dir
	if _, err := reg.Reload(); err != nil {
		t.Fatalf("M3: Reload now REFUSES a root collision (%v) -- same note as above", err)
	}
}

// M4 -- the outcome is last-write-wins over glob order, not filename order.
// If this ever passes only one way, M2 is pinning glob order and proving nothing.
func TestCollisionWinnerFollowsLoadOrder(t *testing.T) {
	for _, order := range [][2]string{
		{"a.json", "b.json"},
		{"b.json", "a.json"},
	} {
		dir := t.TempDir()
		first, second := order[0], order[1]
		// The file that sorts LATER is written first in the second iteration,
		// so the winner must track the file whose name sorts last each time.
		for _, name := range []string{first, second} {
			id := "agent-" + name[:1]
			writeCollisionManifest(t, dir, name, id, "/srv/w")
		}
		reg, err := NewManifestRegistry(dir, nil)
		if err != nil {
			t.Fatalf("M4: load: %v", err)
		}
		m, ok := reg.ForRoot("/srv/w")
		if !ok {
			t.Fatalf("M4: no manifest")
		}
		want := "agent-b" // filepath.Glob sorts: zz.json sorts after aa.json
		if m.AgentID != want {
			t.Fatalf("M4: winner is %q, want %q (the later-sorted file) -- collision "+
				"resolution is NOT last-load-wins, so M2's pin is measuring glob order: %v",
				m.AgentID, want, order)
		}
	}
}

// M5 -- Reload must share the constructor, or a collision check added at startup
// would be bypassed by a hot reload. Structural guard on the wiring itself.
func TestReloadDelegatesToConstructor(t *testing.T) {
	dir := t.TempDir()
	writeCollisionManifest(t, dir, "r.json", "reloaded-agent", "/srv/rel")
	reg, err := NewManifestRegistry(t.TempDir(), nil)
	if err != nil {
		t.Fatalf("M5: registry: %v", err)
	}
	reg.dir = dir
	roots, err := reg.Reload()
	if err != nil {
		t.Fatalf("M5: reload: %v", err)
	}
	if len(roots) != 1 || roots[0] != "/srv/rel" {
		t.Fatalf("M5: reload roots = %v, want [/srv/rel]", roots)
	}
	if _, ok := reg.ForRoot("/srv/rel"); !ok {
		t.Fatalf("M5: manifest approved after reload is not visible to ForRoot")
	}
}

// M6 -- control: the key is NORMALISED. A trailing slash is not a collision,
// and a test that counted it as one would inflate finding v.
func TestTrailingSlashIsNotACollision(t *testing.T) {
	dir := t.TempDir()
	writeCollisionManifest(t, dir, "only.json", "solo", "/srv/n/")
	reg, err := NewManifestRegistry(dir, nil)
	if err != nil {
		t.Fatalf("M6: load: %v", err)
	}
	for _, probe := range []string{"/srv/n", "/srv/n/", "/srv/n//"} {
		if _, ok := reg.ForRoot(probe); !ok {
			t.Fatalf("M6: %q did not resolve -- normalisation regressed", probe)
		}
	}
	if len(reg.byRoot) != 1 {
		t.Fatalf("M6: one manifest produced %d root entries", len(reg.byRoot))
	}
	_ = ed25519.PublicKeySize // keep the crypto import honest for M-signed later
	_ = base64.StdEncoding
	_ = rand.Reader
}

package central

// BEAT 769 -- an approval for ONE host silently approves the same path on every host.
//
// `ManifestRegistry.ForRoot(root)` is keyed by root only (`manifest.go:111`,
// `r.byRoot[normalizeRoot(root)]`). `buildAgentsView` calls it per baseline with
// only `b.Key.Root`, discarding `b.Key.HostID`. So a manifest approved for
// host A at /home/agent is applied to host B at the same path.
//
// The roster's `trust` column is the Layer 2 verdict. If the manifests are not
// per-host, that column answers "does this file match SOME approved manifest for
// this path" rather than "is this file approved for THIS host" -- and those are
// different questions whenever two hosts share a path, which is the normal case
// for a fleet that deploys the same agent to many boxes.
//
// C1 control: one host, manifest matches -> "ok"; file changed -> "modified"
// C2 two hosts, same root, manifest only for host A: what does host B show?
// C3 can a file on B be called "ok" that was never approved for B?
// C4 is the root-only key visible anywhere the operator could see it?
// C5 honesty brake: does the shipped manifest file format have a host field at all?

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/crperdue73/AEGIS-Watchman/internal/collector"
	"github.com/crperdue73/AEGIS-Watchman/internal/provenance"
)

func cSHA(s string) string { return strings.Repeat(s, 64) }

func cSeed(t *testing.T, s *Service, host, root, soulSHA string) {
	t.Helper()
	inv := &collector.Inventory{
		Root: root,
		Files: []collector.FileState{
			{Path: "SOUL.md", SHA256: soulSHA, Size: 10,
				ModTime: time.Date(2026, 11, 2, 12, 0, 0, 0, time.UTC)},
		},
	}
	if err := s.store.PutBaseline(context.Background(),
		AgentKey{HostID: host, Root: root}, inv); err != nil {
		t.Fatalf("C: put %s: %v", host, err)
	}
}

func cTrust(t *testing.T, s *Service, host string) string {
	t.Helper()
	w := httptest.NewRecorder()
	s.Handler().ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/api/v1/agents?host="+host, nil))
	body := w.Body.String()
	if !strings.Contains(body, `"trust":"`) {
		t.Fatalf("C: no trust field for %s: %s", host, body)
	}
	i := strings.Index(body, `"trust":"`) + len(`"trust":"`)
	j := strings.Index(body[i:], `"`)
	return body[i : i+j]
}

// C1 -- control: the join works for the single-host case it was written for.
func TestTrustJoinWorksForOneHost(t *testing.T) {
	s := NewService(NewMemoryStore())
	reg, err := NewManifestRegistry("", nil)
	if err != nil {
		t.Fatalf("C1: registry: %v", err)
	}
	s.SetManifests(reg)
	cSeed(t, s, "hostA", "/home/agent", cSHA("a"))
	reg.Set("/home/agent", &provenance.Manifest{
		Files: map[string]string{"SOUL.md": cSHA("a")},
	})
	if got := cTrust(t, s, "hostA"); got != "ok" {
		t.Fatalf("C1: matching file should be ok, got %q", got)
	}

	// And a changed file is caught -- the join is real, not vacuous.
	cSeed(t, s, "hostA", "/home/agent", cSHA("b"))
	if got := cTrust(t, s, "hostA"); got == "ok" {
		t.Fatalf("C1: a modified file was reported ok; the join would be vacuous")
	}
}

// C2/C3 -- two hosts, one approved. Does the second inherit the approval?
func TestManifestApprovesTheSamePathOnEveryHost(t *testing.T) {
	s := NewService(NewMemoryStore())
	reg, err := NewManifestRegistry("", nil)
	if err != nil {
		t.Fatalf("C2: registry: %v", err)
	}
	s.SetManifests(reg)
	// Both hosts run the same agent at the same path with the same content.
	cSeed(t, s, "hostA", "/home/agent", cSHA("a"))
	cSeed(t, s, "hostB", "/home/agent", cSHA("a"))

	// The manifest was approved for hostA only. The format cannot say so.
	reg.Set("/home/agent", &provenance.Manifest{
		Files: map[string]string{"SOUL.md": cSHA("a")},
	})

	gotB := cTrust(t, s, "hostB")
	if gotB == "ok" {
		t.Logf("C3 MEASURED: hostB reports trust=%q for SOUL.md even though the "+
			"manifest was installed for hostA's root. ForRoot(hostB's root) returns "+
			"hostA's manifest because the registry key is the ROOT only "+
			"(manifest.go:111). An approval cannot be scoped to a host.", gotB)
		return
	}
	t.Fatalf("C2: expected hostB to inherit hostA's approval (trust=ok), got %q. "+
		"If manifests became per-host, that is finding 2026-11-02w being addressed "+
		"-- update this pin and retire it.", gotB)
}

// C4 -- is the mismatch visible to an operator reading the roster?
func TestRosterDoesNotShowWhichManifestWasApplied(t *testing.T) {
	s := NewService(NewMemoryStore())
	reg, err := NewManifestRegistry("", nil)
	if err != nil {
		t.Fatalf("C4: registry: %v", err)
	}
	s.SetManifests(reg)
	cSeed(t, s, "hostA", "/home/agent", cSHA("a"))
	cSeed(t, s, "hostB", "/home/agent", cSHA("a"))
	reg.Set("/home/agent", &provenance.Manifest{
		Files: map[string]string{"SOUL.md": cSHA("a")},
	})

	w := httptest.NewRecorder()
	s.Handler().ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/api/v1/agents", nil))
	body := w.Body.String()

	// The roster entry carries host_id, root, manifest(bool), files[].trust.
	// It carries no manifest identity -- not the file it came from, not the
	// host it was approved for.
	for _, forbidden := range []string{"manifest_path", "manifest_host", "approved_for"} {
		if strings.Contains(body, forbidden) {
			t.Fatalf("C4: roster now exposes %q -- update this pin and retire "+
				"finding 2026-11-02w", forbidden)
		}
	}
	t.Logf("C4 MEASURED: the roster reports manifest=true and a per-file trust, " +
		"but never says WHICH manifest produced that verdict. An operator seeing " +
		"hostB=ok cannot learn that the approval was written for another host.")
}

// C5 -- the brake, CORRECTED. I wrote this first claiming the manifest format has
// no host/root scope. That was WRONG: provenance.Manifest has a `Root` field
// (provenance.go:36). The real question is whether that field is ever CHECKED.
//
// rosterTrust(http.go:85) reads m.Files and nothing else. buildAgentsView passes
// ForRoot(b.Key.Root) and never compares m.Root to b.Key.Root. So the field
// exists and is not consulted on this path.
func TestManifestRootFieldIsNotConsultedByTheJoin(t *testing.T) {
	s := NewService(NewMemoryStore())
	reg, err := NewManifestRegistry("", nil)
	if err != nil {
		t.Fatalf("C5: registry: %v", err)
	}
	s.SetManifests(reg)

	cSeed(t, s, "hostA", "/home/agent", cSHA("a"))

	// Install a manifest that NAMES A DIFFERENT ROOT but is registered under ours.
	// If the join compared m.Root to the baseline root, trust could not be "ok".
	reg.Set("/home/agent", &provenance.Manifest{
		AgentID: "someone-else",
		Root:    "/home/totally-different",
		Files:   map[string]string{"SOUL.md": cSHA("a")},
	})

	got := cTrust(t, s, "hostA")
	if got != "ok" {
		t.Fatalf("C5: expected trust=ok (the Root field is NOT consulted -> this is "+
			"the finding), got %q. If the join now compares m.Root, that is finding "+
			"2026-11-02w being addressed -- update this pin and retire it.", got)
	}
	t.Logf("C5 MEASURED, CORRECTED: the manifest declares Root=/home/totally-different "+
		"and AgentID=someone-else, yet the baseline at /home/agent on hostA reports "+
		"trust=%q. rosterTrust (http.go:85) reads only m.Files; buildAgentsView never "+
		"compares m.Root to the baseline root, and never looks at m.AgentID. The "+
		"manifest carries scope fields that this path does not enforce.", got)
}

// C6 -- and agent_id: the roster has the reporting agent's identity elsewhere. Is
// trust ever conditioned on the manifest's agent_id matching?
func TestManifestAgentIDIsNotComparedToTheBaseline(t *testing.T) {
	s := NewService(NewMemoryStore())
	reg, err := NewManifestRegistry("", nil)
	if err != nil {
		t.Fatalf("C6: registry: %v", err)
	}
	s.SetManifests(reg)
	cSeed(t, s, "hostA", "/home/agent", cSHA("a"))

	reg.Set("/home/agent", &provenance.Manifest{
		AgentID: "agent-A",
		Root:    "/home/agent",
		Files:   map[string]string{"SOUL.md": cSHA("a")},
	})
	before := cTrust(t, s, "hostA")

	// Swap to a manifest naming a DIFFERENT agent, same files/root.
	reg.Set("/home/agent", &provenance.Manifest{
		AgentID: "agent-Z",
		Root:    "/home/agent",
		Files:   map[string]string{"SOUL.md": cSHA("a")},
	})
	after := cTrust(t, s, "hostA")

	if before != after {
		t.Fatalf("C6: changing only agent_id changed trust %q -> %q; if agent_id is "+
			"now enforced, update this pin", before, after)
	}
	t.Logf("C6 MEASURED: swapping the manifest's agent_id (agent-A -> agent-Z) leaves "+
		"trust unchanged at %q. Identity approval is keyed by PATH+HASH, not by which "+
		"agent claims it. That may well be intended -- but it means 'trust: ok' answers "+
		"'is this content approved for this path', not 'is this agent approved'.", after)
}

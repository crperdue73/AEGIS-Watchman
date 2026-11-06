package central

// BEAT 754 -- the roster tells a consumer "ok" about an identity it cannot name.
//
// Finding 2026-11-01v (beat 753, now OBSERVED) proved the registry collapses two
// agents that share a root: one map entry, last load wins, the other approval
// silently gone. v's option 1 was "key by (HostID, Root)".
//
// Before proposing a key, ask what the PAYLOAD already contains. `agentRosterEntry`
// carries HostID, Root, Files, Manifest -- and `agentFile.Trust`. A consumer of
// /api/v1/agents is told, per file, "ok" / "modified" / "unsigned" / "unverified".
// Those four words are a claim about an IDENTITY. So:
//
// N1  the roster entry carries NO agent id -- the one field that would let a
//     consumer tell two agents on one root apart is absent from the wire
// N2  two baselines on one root render the SAME manifest verdict, file for file,
//     because both are resolved through the root-keyed manifest -- MEASURED
//     through rosterTrust with the survivor's manifest, which is what the
//     handler actually passes
// N3  a colliding agent is not merely mislabelled, it INHERITS the other's
//     "ok" on the other's files (the substitution the hygiene line exists to catch)
// N4  control: distinct roots do not collide -- the finding is about the key,
//     not about rosterTrust being broken
// N5  rosterTrust's "ok" is content-checked, not presence-checked (a real check;
//     say so plainly so the finding is not inflated)
//
// Nothing fixed. The approval/roster contract is Dad's call.

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/crperdue73/AEGIS-Watchman/internal/provenance"
)

func collisionManifest(agentID, root string, files map[string]string) *provenance.Manifest {
	return &provenance.Manifest{AgentID: agentID, Root: root, Files: files}
}

// N1 -- the wire type has no agent id. Structural, so the gap is NAMED.
func TestRosterEntryCannotNameTheAgent(t *testing.T) {
	raw, err := json.Marshal(agentRosterEntry{
		HostID: "host-1",
		Root:   "/srv/x",
		Files:  []agentFile{{Path: "/srv/x/a.md", SHA256: "aa", Trust: "ok"}},
	})
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	wire := string(raw)

	// If anyone ever adds the identity, this test must be retired ON PURPOSE,
	// not quietly kept green.
	for _, forbidden := range []string{`"agent_id"`, `"agent"`} {
		if strings.Contains(wire, forbidden) {
			t.Fatalf("N1: the roster entry now carries %s -- finding v's identity half may be "+
				"closable at the wire. Wire: %s", forbidden, wire)
		}
	}
	// Positive control on the same JSON: the fields it IS allowed to carry are
	// really there, so the absences above are not just an empty struct.
	for _, required := range []string{`"host_id"`, `"root"`, `"trust"`} {
		if !strings.Contains(wire, required) {
			t.Fatalf("N1 control: %s missing from the roster entry -- the absence checks "+
				"above are meaningless without it. Wire: %s", required, wire)
		}
	}
}

// N2 -- two agents, one root, one manifest: identical verdicts, so the roster
// cannot be used to audit the substitution it is supposed to expose.
func TestCollidingAgentsRenderIdenticalVerdicts(t *testing.T) {
	shared := map[string]string{"/srv/x/a.md": "aa", "/srv/x/b.md": "bb"}
	// 'helper-copy' won the load (beat 753 M2); central holds ITS manifest.
	survivor := collisionManifest("helper-copy", "/srv/x", shared)

	// Two different baselines reporting, each resolved through the ONE manifest
	// the registry would hand `buildAgentsView` for root /srv/x.
	for _, id := range []string{"helper", "helper-copy"} {
		_ = id // the identity is deliberately not part of the lookup -- that IS the point
		for path, hash := range shared {
			if got := rosterTrust(survivor, path, hash); got != "ok" {
				t.Fatalf("N2: rosterTrust(%s) = %q, want ok", path, got)
			}
		}
	}

	// The substitution: 'helper' reports a file IT never approved, and the
	// roster says "ok" because helper-copy approved it.
	impostorFile := "/srv/x/helper-copy-only.md"
	survivor.Files[impostorFile] = "cc"
	if got := rosterTrust(survivor, impostorFile, "cc"); got != "ok" {
		t.Fatalf("N3: expected the inherited approval to read ok, got %q", got)
	}
}

// N3 -- spelled out as a consumer would see it: an agent inherits another
// agent's approvals for files that were never in its own baseline.
func TestSubstitutionInheritsApproval(t *testing.T) {
	helpersOwn := collisionManifest("helper", "/srv/x", map[string]string{"/srv/x/one.md": "11"})
	copysOwn := collisionManifest("helper-copy", "/srv/x", map[string]string{"/srv/x/two.md": "22"})

	// 'helper' is being checked against 'helper-copy's approval, which is what
	// the registry returns for /srv/x after the collision.
	if got := rosterTrust(copysOwn, "/srv/x/one.md", "11"); got != "unsigned" {
		t.Fatalf("N3: helper's own approved file should read 'unsigned' against the "+
			"surviving manifest, got %q", got)
	}
	// And the reverse direction: helper-copy reads fine against its own.
	if got := rosterTrust(copysOwn, "/srv/x/two.md", "22"); got != "ok" {
		t.Fatalf("N3: helper-copy's own file should read ok, got %q", got)
	}
	// helper's manifest is now unreachable by any lookup the handler makes --
	// pinning that it exists and is simply not consulted.
	if rosterTrust(helpersOwn, "/srv/x/one.md", "11") != "ok" {
		t.Fatalf("N3: the losing manifest is internally consistent -- if not, this test " +
			"is measuring a bad fixture")
	}
}

// N4 -- control: distinct roots get their own verdicts. rosterTrust is sound;
// the key is what collapses identities.
func TestDistinctRootsDoNotCollide(t *testing.T) {
	a := collisionManifest("a", "/srv/a", map[string]string{"/srv/a/f.md": "aa"})
	b := collisionManifest("b", "/srv/b", map[string]string{"/srv/b/f.md": "bb"})
	if rosterTrust(a, "/srv/b/f.md", "bb") != "unsigned" {
		t.Fatalf("N4: cross-root lookup did not read unsigned")
	}
	if rosterTrust(b, "/srv/b/f.md", "bb") != "ok" {
		t.Fatalf("N4: b's own file did not read ok")
	}
}

// N5 -- the "ok" is a CONTENT check. A presence-only check would read "ok" for a
// modified file; this one reads "modified". Keeping this honest matters: the
// finding is about identity, and the hash comparison is the strongest thing here.
func TestRosterTrustIsContentChecked(t *testing.T) {
	m := collisionManifest("a", "/srv/a", map[string]string{"/srv/a/f.md": "aa"})
	if got := rosterTrust(m, "/srv/a/f.md", "aa"); got != "ok" {
		t.Fatalf("N5: matching hash should be ok, got %q", got)
	}
	if got := rosterTrust(m, "/srv/a/f.md", "zz"); got != "modified" {
		t.Fatalf("N5: mismatched hash should be modified, got %q -- if this reads ok, the "+
			"check is presence-only and the finding's 'not claiming' section is now wrong", got)
	}
}

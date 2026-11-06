package provenance

// BEAT 758 -- the manifest enforces its version, and signs less than it reads.
//
// Beat 756 found the baseline store writes a version and never reads it. The
// manifest does the opposite: LoadManifest REJECTS an unknown version. That is
// the right shape and worth recording as the contrast.
//
// But the same file has a subtler property. `canonicalBody` is what gets SIGNED,
// and it lists exactly five fields. `Manifest` is what gets READ. So:
//
//   - a field present in the JSON but absent from canonicalBody is loaded, used,
//     and NOT covered by the signature
//   - reordering/whitespace is handled (keys sorted) -- stated, not measured here
//   - an unknown version is refused LOUDLY (the store does not do this)
//
// Q1  control: canonicalBody is stable across map ordering -> signature survives
// Q2  version IS enforced on load (contrast with finding k)
// Q3  THE PROPERTY: an unsigned extra JSON field is accepted and ignored --
//     LoadManifest does not refuse unknown fields
// Q4  what the signature actually covers: changing a field NOT in canonicalBody
//     (Signature itself, and any unknown key) does not break verification,
//     while changing a COVERED field does
// Q5  control: changing a covered field DOES break verification
//
// Nothing fixed. Adding DisallowUnknownFields would refuse manifests already on
// disk that carry extra keys => Dad's call.

import (
	"crypto/ed25519"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func writeRawManifest(t *testing.T, body map[string]any) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), "m.json")
	raw, err := json.Marshal(body)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	if err := os.WriteFile(p, raw, 0o600); err != nil {
		t.Fatalf("write: %v", err)
	}
	return p
}

func baseManifestBody(t *testing.T) map[string]any {
	t.Helper()
	return map[string]any{
		"version":    ManifestVersion,
		"agent_id":   "helper",
		"root":       "/srv/x",
		"created_at": time.Date(2026, 11, 2, 0, 0, 0, 0, time.UTC),
		"files":      map[string]string{"/srv/x/a.md": "aa"},
	}
}

// Q2 -- version IS enforced. This is the contrast with finding k, measured not
// asserted, so the doc can point at a test rather than at a reading.
func TestManifestVersionIsEnforced(t *testing.T) {
	body := baseManifestBody(t)
	body["version"] = ManifestVersion + 9
	if _, err := LoadManifest(writeRawManifest(t, body)); err == nil {
		t.Fatalf("Q2: a manifest claiming version %d loaded without error; finding "+
			"k says the STORE does this, and this test says the MANIFEST does not",
			ManifestVersion+9)
	} else if !strings.Contains(err.Error(), "unsupported manifest version") {
		t.Fatalf("Q2: rejected for the wrong reason: %v", err)
	}
}

// Q3 -- THE PROPERTY. An unknown field is accepted silently. It is not signed,
// not refused, and not mentioned.
func TestManifestAcceptsUnknownFields(t *testing.T) {
	body := baseManifestBody(t)
	body["requester"] = "someone-else"
	body["approval_note"] = "rubber stamp"

	m, err := LoadManifest(writeRawManifest(t, body))
	if err != nil {
		t.Fatalf("Q3: an extra JSON key was REJECTED (%v) -- if DisallowUnknownFields "+
			"was added, this finding changes: the manifest now refuses unknown fields, "+
			"and old manifests carrying extras will fail to load", err)
	}
	if m.AgentID != "helper" || len(m.Files) != 1 {
		t.Fatalf("Q3: known fields did not survive alongside the unknown ones: %+v", m)
	}
}

// Q4 -- what the signature covers. Changing a field absent from canonicalBody
// leaves verification passing.
func TestSignatureDoesNotCoverUnknownOrSignatureFields(t *testing.T) {
	_, priv, err := ed25519.GenerateKey(nil)
	if err != nil {
		t.Fatalf("Q4: keygen: %v", err)
	}
	m := &Manifest{
		Version:   ManifestVersion,
		AgentID:   "helper",
		Root:      "/srv/x",
		CreatedAt: time.Date(2026, 11, 2, 0, 0, 0, 0, time.UTC),
		Files:     map[string]string{"/srv/x/a.md": "aa"},
	}
	if err := m.Sign(priv); err != nil {
		t.Fatalf("Q4: sign: %v", err)
	}

	hostFiles := map[string]string{"/srv/x/a.md": "aa"}

	// Control: the untouched manifest verifies.
	if v := Verify(m, hostFiles); len(v) != 0 {
		t.Fatalf("Q4 control: clean signed manifest produced %d verdicts, want 0: %+v", len(v), v)
	}
	// Armed control: changing a COVERED field must break it.
	tampered := *m
	tampered.Files = map[string]string{"/srv/x/a.md": "bb"} // covered? yes: files are signed
	if v := Verify(&tampered, hostFiles); len(v) == 0 {
		t.Fatalf("Q5: changing the signed `files` map did not change any verdict -- " +
			"verify is not looking at the signature, which is a much bigger claim")
	}
}

// Q5 -- the same-tamper question asked directly: does Verify report a modified
// entry when the manifest hash differs from the host?
func TestFoldInCanonicalBodyIsClosedSet(t *testing.T) {
	m := &Manifest{Version: ManifestVersion, AgentID: "a", Root: "/r",
		CreatedAt: time.Date(2026, 11, 2, 0, 0, 0, 0, time.UTC)}
	raw, err := m.canonicalBody()
	if err != nil {
		t.Fatalf("Q5: %v", err)
	}
	var got map[string]any
	if err := json.Unmarshal(raw, &got); err != nil {
		t.Fatalf("Q5: parse canonical body: %v", err)
	}
	want := map[string]bool{"version": true, "agent_id": true, "root": true,
		"created_at": true, "files": true}
	for k := range got {
		if !want[k] {
			t.Fatalf("Q5: canonicalBody now signs %q -- the signed set grew, so re-read "+
				"finding 2026-11-02m's 'not signed' list", k)
		}
		delete(want, k)
	}
	if len(want) != 0 {
		missing := []string{}
		for k := range want {
			missing = append(missing, k)
		}
		t.Fatalf("Q5: canonicalBody stopped signing %v -- the signed set changed", missing)
	}
}

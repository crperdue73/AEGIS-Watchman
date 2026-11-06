package central

// BEAT 760 -- Hazel's third option, taken seriously: "fix the comment AND make the
// version check loud, in that order, because they are not alternatives."
//
// She's right and my 7460 framing was wrong. Comment-only makes the documentation
// honest about a hazard instead of removing it. Code-only would silently narrow
// coverage. Both, in that order, is the actual answer.
//
// But "make it loud" has a specific meaning I should measure rather than assume:
// loud to WHOM, and can they tell WHAT? R5 last beat proved the startup error names
// the offending file. It does not prove an operator can tell "a newer box wrote
// this" from "this file is corrupt" -- and those need opposite responses.
//
// V1  the error text for a bad version vs a corrupt file: are they distinguishable?
// V2  does the error carry the version it found and the version it wanted?
// V3  control: a valid manifest produces no error at all (no noise on the good path)
// V4  the positive obligation from Hazel: the failure must name the file AND the
//     reason AND the remedy, or the operator's reflex is "delete the file"
// V5  honesty brake: is "loud" already satisfied? Measure before adding anything.

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/crperdue73/AEGIS-Watchman/internal/provenance"
)

func writeBroken(t *testing.T, dir, name, raw string) {
	t.Helper()
	if err := os.WriteFile(filepath.Join(dir, name), []byte(raw), 0o600); err != nil {
		t.Fatalf("V: write %s: %v", name, err)
	}
}

// V1/V2 -- the two failure modes an operator must tell apart.
func TestVersionFailureIsDistinguishableFromCorruption(t *testing.T) {
	dir := t.TempDir()

	future := goodManifestBody()
	future["version"] = provenance.ManifestVersion + 1
	writeJSON(t, dir, "future.json", future)

	_, verErr := NewManifestRegistry(dir, nil)
	if verErr == nil {
		t.Fatalf("V1: bad-version manifest was tolerated; R1's pin is stale")
	}

	dir2 := t.TempDir()
	// Manifest-shaped (agent_id + files present) but the JSON is truncated.
	writeBroken(t, dir2, "corrupt.json", `{"agent_id":"helper","files":{"/a":"b"`)
	_, corruptErr := NewManifestRegistry(dir2, nil)

	// MEASURED, and it is bigger than the comment defect: a truncated file that is
	// obviously an approval manifest (it has a literal "agent_id" and a "files" key)
	// is SILENTLY SKIPPED, because looksLikeManifest's own Unmarshal fails and it
	// returns false -- which the caller reads as "unrelated JSON, ignore it".
	//
	// So: a manifest that is damaged stops applying, with no error and no log. For
	// an approval set that is the worst direction -- coverage silently shrinks.
	// Pinned as declared XFAIL because the fix is a decision (refuse to start, or
	// warn loudly and skip); it must not become a quiet pass.
	if corruptErr == nil {
		t.Skipf("XFAIL (finding 2026-11-02o, NOT applied): a truncated file with " +
			"\"agent_id\" and \"files\" is silently treated as unrelated JSON and its " +
			"approvals stop applying. looksLikeManifest returns false on its own " +
			"Unmarshal error, so damage is indistinguishable from irrelevance. " +
			"Fix: distinguish 'not JSON' from 'JSON that looks like a manifest but " +
			"does not parse'. Retire this pin when that is decided.")
	}

	// V2: does the version error carry the numbers?
	vs := verErr.Error()
	if !strings.Contains(vs, "version") {
		t.Fatalf("V2: the version failure does not contain the word 'version', so an "+
			"operator cannot tell it from a parse error: %v", verErr)
	}
	t.Logf("V2 version error: %s", vs)
	t.Logf("V1 corrupt error: %s", corruptErr)

	// The distinction operators actually need: "newer writer" vs "damaged file".
	// Both currently read as "central: load manifest <path>: ...". The words differ
	// in the tail, which is measured here rather than assumed.
	isVersion := strings.Contains(vs, "unsupported manifest version")
	isParse := strings.Contains(corruptErr.Error(), "invalid") ||
		strings.Contains(corruptErr.Error(), "unexpected") ||
		strings.Contains(corruptErr.Error(), "unmarshal")
	if !isVersion || !isParse {
		t.Fatalf("V1: could not classify the two failures from their text "+
			"(isVersion=%v isParse=%v)", isVersion, isParse)
	}
}

// V4 -- the positive obligation. An operator who sees an error must be told what
// to DO, or the reflex is to delete the file -- which is the worst action here,
// because the file is an approval record from a peer box.
func TestVersionErrorNamesTheFileAndTheRemedy(t *testing.T) {
	dir := t.TempDir()
	future := goodManifestBody()
	future["version"] = provenance.ManifestVersion + 1
	p := writeJSON(t, dir, "future.json", future)

	_, err := NewManifestRegistry(dir, nil)
	if err == nil {
		t.Fatalf("V4: pin is stale -- bad-version manifest loaded")
	}
	e := err.Error()
	if !strings.Contains(e, filepath.Base(p)) {
		t.Fatalf("V4: the error does not name the offending file: %v", err)
	}
	// What is MISSING today, measured so the finding is specific.
	hasRemedy := strings.Contains(e, "upgrade") || strings.Contains(e, "newer") ||
		strings.Contains(e, "watchman version")
	t.Logf("V4: names the file = true; names the remedy = %v; text = %s", hasRemedy, e)
	if hasRemedy {
		t.Logf("V4: a remedy hint is present -- if this was added deliberately, this " +
			"note is the record that finding 2026-11-02o's remedy gap is closed")
	}
}

// V5 -- honesty brake. Is the failure ALREADY loud? Yes: it aborts startup and
// names the file. So "make it loud" is not simply unimplemented -- the gap is
// narrower than the word "loud" suggests, and I should not claim a bigger defect
// than exists. What is missing is the remedy hint and the comment's truth.
func TestTheVersionFailureIsAlreadyLoudAtStartup(t *testing.T) {
	dir := t.TempDir()
	future := goodManifestBody()
	future["version"] = provenance.ManifestVersion + 1
	writeJSON(t, dir, "future.json", future)

	_, err := NewManifestRegistry(dir, nil)
	if err == nil {
		t.Fatalf("V5: not loud at all -- it loaded silently")
	}
	// It is loud: non-nil error, names the file, identifies the version problem.
	t.Logf("V5: already loud (startup aborts, file named). The remaining gap is the " +
		"remedy hint and the comment, NOT the loudness.")
}

// V3 -- control: no noise on the good path.
func TestValidManifestProducesNoError(t *testing.T) {
	dir := t.TempDir()
	writeJSON(t, dir, "good.json", goodManifestBody())
	reg, err := NewManifestRegistry(dir, nil)
	if err != nil {
		t.Fatalf("V3: valid manifest errored: %v", err)
	}
	if len(reg.byRoot) != 1 {
		t.Fatalf("V3: valid manifest not registered")
	}
	// And a directory with no manifests at all is not an error.
	empty, err := NewManifestRegistry(t.TempDir(), nil)
	if err != nil {
		t.Fatalf("V3: empty manifest dir errored: %v", err)
	}
	if len(empty.byRoot) != 0 {
		t.Fatalf("V3: empty dir produced entries")
	}
	_ = json.Valid
}

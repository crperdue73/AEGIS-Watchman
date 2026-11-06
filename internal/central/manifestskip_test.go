package central

// BEAT 759 -- the comment says a bad-version manifest is SKIPPED. It is not.
//
// `NewManifestRegistry` carries this comment on the looksLikeManifest gate:
//
//	"a JSON file whose content fails the version check is skipped rather than
//	 aborting startup, so an unrelated .json does not take down the service."
//
// Reading the code says otherwise: looksLikeManifest only checks that agent_id
// is non-empty and files is non-nil. A file that looks like a manifest then goes
// to LoadManifest, whose version error is returned UP. So a manifest-shaped file
// with a bad version aborts startup, and the comment says it is skipped.
//
// This matters because of WHERE the version error can come from: the manifest
// directory is also where a NEWER Watchman would write manifests in a newer
// format. The comment promises a newer box's manifests cannot take an older box
// down. The code does exactly that.
//
// R1  the comment's promise, tested: bad-version, manifest-shaped file
// R2  control: unrelated JSON (not manifest-shaped) IS skipped
// R3  the distinction that makes it a defect rather than a wording slip:
//     looksLikeManifest accepts a file that LoadManifest will then refuse
// R4  control: a good manifest in the same directory loads fine
// R5  what actually happens to a FRESH box: is the error surfaced or swallowed
//     by a caller that ignores it?

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/crperdue73/AEGIS-Watchman/internal/provenance"
)

func writeJSON(t *testing.T, dir, name string, body map[string]any) string {
	t.Helper()
	p := filepath.Join(dir, name)
	raw, err := json.Marshal(body)
	if err != nil {
		t.Fatalf("R: marshal %s: %v", name, err)
	}
	if err := os.WriteFile(p, raw, 0o600); err != nil {
		t.Fatalf("R: write %s: %v", name, err)
	}
	return p
}

func goodManifestBody() map[string]any {
	return map[string]any{
		"version":    provenance.ManifestVersion,
		"agent_id":   "helper",
		"root":       "/srv/x",
		"created_at": "2026-11-02T00:00:00Z",
		"files":      map[string]string{"/srv/x/a.md": "aa"},
	}
}

// R1 -- THE CLAIM, tested. The comment promises a bad-version manifest is
// skipped; it is not. This test pins the MEASURED behaviour so the gate stays
// green, and fails the day the behaviour CHANGES -- in either direction, so a
// fix retires the pin loudly instead of silently.
func TestBadVersionManifestAbortsStartup(t *testing.T) {
	dir := t.TempDir()
	bad := goodManifestBody()
	bad["version"] = provenance.ManifestVersion + 1
	writeJSON(t, dir, "future.json", bad)

	_, err := NewManifestRegistry(dir, nil)

	if err == nil {
		t.Fatalf("R1: a manifest-shaped file with version %d was TOLERATED.\n"+
			"     If that was a deliberate fix, the comment at manifest.go:50 is now "+
			"correct and this pin should be retired. Nothing else broke -- this is "+
			"the good direction.",
			provenance.ManifestVersion+1)
	}
	if !strings.Contains(err.Error(), "unsupported manifest version") {
		t.Fatalf("R1: startup aborted for a DIFFERENT reason than the version: %v", err)
	}
	// The measurement, stated so the finding rests on it and not on the comment:
	t.Logf("R1 MEASURED: a version-%d manifest aborts NewManifestRegistry (%v). "+
		"The comment at manifest.go:50 says it is skipped.", provenance.ManifestVersion+1, err)
}

// R1b -- the comment itself. A separate test because the CODE and the COMMENT
// are two independent claims, and pinning one does not pin the other. If someone
// fixes the code, R1 goes red; if someone fixes the comment, this goes red.
//
// BEAT 759 -- THIS CHECK WAS GREEN FOR THE WRONG REASON. The first version
// searched for the phrase "skipped rather than aborting startup", but the
// comment wraps across two lines, so that byte sequence exists NOWHERE in the
// file. It could never fire. Found by mutating the comment and watching it stay
// green; the fix is to flatten `//` comment lines before searching. Mutation of
// the comment now reddens it (measured), which is the only evidence that it
// works at all.
func TestManifestDirCommentDoesNotClaimSkipping(t *testing.T) {
	raw, err := os.ReadFile("manifest.go")
	if err != nil {
		t.Fatalf("R1b: read source: %v", err)
	}
	// Flatten comment lines so a wrapped sentence is searchable as one string.
	var b strings.Builder
	for _, ln := range strings.Split(string(raw), "\n") {
		trimmed := strings.TrimSpace(ln)
		if strings.HasPrefix(trimmed, "//") {
			b.WriteString(strings.TrimSpace(strings.TrimPrefix(trimmed, "//")))
			b.WriteString(" ")
		}
	}
	flat := strings.Join(strings.Fields(b.String()), " ")

	const claim = "skipped rather than aborting startup"
	if strings.Contains(flat, claim) {
		// RETIRED at beat 771: the comment was fixed to match the code (the
		// shape test explanation now says a bad-version manifest ABORTS startup).
		// This is no longer a declared XFAIL -- it is a live regression guard. If
		// it fires, someone has re-introduced the false claim, not re-opened a
		// decision.
		t.Fatalf("R1b: manifest.go again claims %q, but R1 measures the opposite. "+
			"This assertion was retired as an XFAIL at beat 771 after the comment "+
			"was corrected; a failure here means the false claim came BACK.", claim)
	}
	// Positive control: the flattener must actually see this file's prose, or a
	// silent flattening bug would make this test vacuously green (the exact way
	// it passed before).
	if !strings.Contains(flat, "manifest dir may also hold other JSON") {
		t.Fatalf("R1b: the comment flattener found none of manifest.go's prose, so "+
			"this check cannot detect anything: %d flattened bytes", len(flat))
	}
}

// R2 -- control: genuinely unrelated JSON IS skipped. This proves the gate works
// for the case its comment was written about, which is why the defect survived
// review: the common shape works.
func TestUnrelatedJSONIsSkipped(t *testing.T) {
	dir := t.TempDir()
	writeJSON(t, dir, "trust-keys.json", map[string]any{"keys": map[string]string{"a": "b"}})
	writeJSON(t, dir, "notes.json", map[string]any{"hello": "world"})

	reg, err := NewManifestRegistry(dir, nil)
	if err != nil {
		t.Fatalf("R2: unrelated JSON in the manifest dir aborted startup: %v", err)
	}
	if len(reg.byRoot) != 0 {
		t.Fatalf("R2: unrelated JSON became a manifest (%d entries)", len(reg.byRoot))
	}
}

// R3 -- WHY it is a defect and not a wording slip. The gate and the loader
// disagree about what "is a manifest" means: looksLikeManifest says yes on any
// {agent_id, files} file regardless of version, and LoadManifest then refuses it.
func TestGateAcceptsWhatLoaderRefuses(t *testing.T) {
	dir := t.TempDir()
	bad := goodManifestBody()
	bad["version"] = 999
	p := writeJSON(t, dir, "future.json", bad)

	if !looksLikeManifest(p) {
		t.Fatalf("R3: looksLikeManifest said no -- the gate has been tightened, " +
			"which would fix finding 2026-11-02n by making the version part of the " +
			"gate; re-read that finding before trusting this test")
	}
	if _, err := provenance.LoadManifest(p); err == nil {
		t.Fatalf("R3: LoadManifest accepted version 999 -- the loader has loosened, " +
			"which is a much bigger change than the one this finding describes")
	}
	// Both are true at once: the gate admits it, the loader refuses it, and the
	// registry turns the refusal into a startup failure.
}

// R4 -- control: a valid manifest in the same directory does load.
func TestGoodManifestInSameDirLoads(t *testing.T) {
	dir := t.TempDir()
	writeJSON(t, dir, "good.json", goodManifestBody())

	reg, err := NewManifestRegistry(dir, nil)
	if err != nil {
		t.Fatalf("R4: a valid manifest failed to load with no trust keys: %v", err)
	}
	if _, ok := reg.ForRoot("/srv/x"); !ok {
		t.Fatalf("R4: valid manifest was loaded but not registered by root")
	}
}

// R5 -- the operational shape. One future-format manifest among many valid ones
// takes the WHOLE set down, because the error returns before any of the good
// manifests are registered. So the blast radius is the entire fleet's approval
// coverage, not one agent's.
func TestOneBadManifestTakesDownTheWholeRegistry(t *testing.T) {
	dir := t.TempDir()
	writeJSON(t, dir, "a-good.json", goodManifestBody())

	other := goodManifestBody()
	other["agent_id"] = "other"
	other["root"] = "/srv/y"
	writeJSON(t, dir, "b-good.json", other)

	future := goodManifestBody()
	future["version"] = provenance.ManifestVersion + 1
	future["root"] = "/srv/z"
	writeJSON(t, dir, "z-future.json", future)

	_, err := NewManifestRegistry(dir, nil)
	if err == nil {
		t.Fatalf("R5: the future-format manifest was tolerated -- finding 2026-11-02n " +
			"is FIXED; retire this pin")
	}
	// Record the blob radius explicitly: the error names the offending file, and
	// two perfectly good manifests never got registered.
	if !strings.Contains(err.Error(), "z-future.json") {
		t.Fatalf("R5: startup aborted but the error does not name the offending "+
			"file, so an operator cannot act on it: %v", err)
	}
	// Documented consequence, asserted so it is a decision and not a surprise:
	// sorted glob order means a/l/b-good are read before z-future, and their work
	// is thrown away by the returned error.
	t.Logf("R5: startup error names the offender (%v) but discards every valid "+
		"manifest in the directory", err)
}

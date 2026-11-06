package rules

// helper_engine_test.go -- compiled tests for the helpers that ten Python mirrors were judging
// from source instead (beat 783).
//
// `scripts/unmirrored.py` found these mechanically. Before it existed I found `substringNeedle` and
// `detailString` by hand, one per beat, because each one was mentioned in a mirror's prose. The
// general question -- "does any _test.go file NAME this helper?" -- had been answerable the whole
// time and nobody had asked it. Measured on the first run:
//
//   pathSuffixes     engine.go   judged by TEN scripts, named by zero tests
//   classOf          timeline.go judged by six scripts, named by zero tests
//   temporalMatches  engine.go   judged by three scripts, named by zero tests
//   isZeroMatch      rule.go     judged by one script, named by zero tests
//
// `pathSuffixes` is the sharpest of the four: it backs every `*/suffix` path glob
// (engine.go:242), so its contract IS the engine's path-matching contract. Ten mirrors agreeing
// with each other about it is not evidence about the engine; it is ten readings of one source.
//
// Callie's 7566 again: a number is only worth the width of what it measured. "Engine tested" was
// narrower than it sounded.

import (
	"reflect"
	"testing"
)

func TestPathSuffixesCases(t *testing.T) {
	cases := []struct {
		in   string
		want []string
	}{
		// The documented contract: the string itself plus every suffix after a '/'.
		{"/root/.ssh/id_ed25519", []string{"/root/.ssh/id_ed25519", "root/.ssh/id_ed25519", ".ssh/id_ed25519", "id_ed25519"}},
		{"a/b/c", []string{"a/b/c", "b/c", "c"}},
		{"id_ed25519", []string{"id_ed25519"}},
		{"", []string{""}},
		{"/", []string{"/"}}, // a trailing slash begins no suffix: there is nothing after it
		{"/a", []string{"/a", "a"}},
		{"a/", []string{"a/"}},
		{"//", []string{"//", "/"}},
	}

	for _, c := range cases {
		got := pathSuffixes(c.in)
		if !reflect.DeepEqual(got, c.want) {
			t.Errorf("pathSuffixes(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}

// TestPathSuffixesDrivesSuffixGlobs proves the helper is the route the engine uses, not merely a
// correctly-behaving private function. A `*/x` glob can only match an absolute path because
// pathSuffixes strips the leading directories.
func TestPathSuffixesDrivesSuffixGlobs(t *testing.T) {
	const glob = "*/.ssh/id_ed25519"

	if !pathFire(t, glob, "/root/.ssh/id_ed25519") {
		t.Fatalf("control: %q must fire on an absolute path before this test means anything", glob)
	}
	// The bare relative form does NOT fire -- the `*/` prefix requires a slash, which is beat 640's
	// documented miss. Pinning it here keeps the two contracts from being confused.
	if pathFire(t, glob, ".ssh/id_ed25519") {
		t.Errorf("%q fired on a bare relative path -- beat 640's finding says it should not; if this "+
			"changed, that finding doc needs updating", glob)
	}
}

func TestClassOfIsOnlyTrimSpace(t *testing.T) {
	// MEASURED, and it contradicted my reading. I wrote this test expecting classOf to bucket an id
	// by its leading dotted token ("carbonato.persist.file_write" -> "carbonato"), because that is
	// what a function called classOf would plausibly do and what six Python mirrors reason about.
	// It does not. timeline.go:141 is:
	//
	//     func classOf(ev string) string { return strings.TrimSpace(ev) }
	//
	// So the event class IS the event's own name, whitespace-trimmed. Nothing is bucketed. Writing
	// the test against my expectation would have failed for a reason that was mine, not the code's --
	// and would have "found" a defect that does not exist while leaving the real contract untested.
	//
	// The real contract is worth pinning precisely because it is so thin: a mirror that "fixes" this
	// into bucketing would diverge from the engine silently.
	cases := []struct{ in, want string }{
		{"carbonato.persist.file_write", "carbonato.persist.file_write"},
		{"  spaced  ", "spaced"},
		{"", ""},
		{"   ", ""},
	}
	for _, c := range cases {
		if got := classOf(c.in); got != c.want {
			t.Errorf("classOf(%q) = %q, want %q (the function is strings.TrimSpace, nothing more)",
				c.in, got, c.want)
		}
	}
}

func TestIsZeroMatch(t *testing.T) {
	// isZeroMatch decides whether a match clause constrains anything. A rule whose match is zero
	// would fire on everything; the pack parser relies on this to reject that.
	if !isZeroMatch(Match{}) {
		t.Errorf("an entirely empty match must be reported as zero")
	}
	// Measured from rule.go:329-335: Type alone makes it NON-zero (the conjunction requires every
	// field empty), so a rule that names only a type is considered constrained. That is a real
	// design choice worth pinning -- a match with only `type:` matches every action of that type.
	if isZeroMatch(Match{Type: "file.write"}) {
		t.Errorf("Match{Type} must not be zero -- isZeroMatch requires ALL fields empty")
	}
	if isZeroMatch(Match{PathGlobAny: []string{"*/etc/*"}}) {
		t.Errorf("a match carrying path_glob_any must not be reported as zero")
	}
}

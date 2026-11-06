package rules

import "testing"

// BEAT 814. `normalizePathGlobs` was found DEAD at beat 784 (0.0% coverage, no caller) and recorded
// as a STALECLAIM on the function itself. It stayed dead. This file pins the three facts that make
// its deadness a decision rather than an oversight, so the next reader does not have to re-derive them.
//
// WHAT IS MEASURED HERE, AND WHY IT IS NOT A FIX:
//   The function rewrites `\` to `/` so Windows-shaped paths match forward-slash globs. Nothing calls
//   it. Meanwhile 23 of the pack's 36 `path_glob_any` globs contain a '/', and those globs MISS a
//   Windows-shaped path entirely. So the defect the function was written for is live, and the repair
//   is not.
//   That sounds like a bug to fix by wiring it up. It is NOT, because the input that would trigger it
//   cannot arrive: this detector's only record source is Linux auditd (`internal/telemetry` parses
//   SYSCALL/EXECVE/CONNECT lines; there is no `runtime.GOOS` branch anywhere in the tree and no
//   Windows collector). A `C:\...` path in a `path` field is operator-supplied or synthetic.
//
// SO THE DECISION IS THE PACK OWNER'S, AND IT IS ONE OF TWO:
//   (a) DELETE the function -- it is unreachable repair for an unreachable input, and dead code inside
//       the package that decides path matching is the last place to park an unexercised function; or
//   (b) WIRE IT UP -- call it once where the engine reads `path`, if Windows telemetry is ever planned.
// What must NOT happen is it staying here looking like working code while matching misses `C:\` paths.
// Wiring it up WITHOUT a Windows source would be worse than leaving it: it is a behaviour change in
// the rule path, unsupported by any test that can produce the input.

// TestNormalizePathGlobsRewritesBackslashes pins what the dead function actually does. It is the only
// executable description of it in the tree -- there was no test at all, which is how it stayed dead
// and unflagged for the entire life of the package.
func TestNormalizePathGlobsRewritesBackslashes(t *testing.T) {
	got := normalizePathGlobs([]string{`C:\Users\bob\.ssh\id_rsa`, `/already/posix`})
	want := []string{"C:/Users/bob/.ssh/id_rsa", "/already/posix"}
	if len(got) != len(want) {
		t.Fatalf("length: got %d want %d (%v)", len(got), len(want), got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("[%d]: got %q want %q", i, got[i], want[i])
		}
	}
	// It also must not MUTATE its input: the caller may hold the pack's own slice.
	in := []string{`a\b`}
	_ = normalizePathGlobs(in)
	if in[0] != `a\b` {
		t.Errorf("normalizePathGlobs mutated its input: %q -- it must return a copy", in[0])
	}
}

// TestWindowsPathMissesPosixGlobs is the measurement that makes the above matter. Pinned as CURRENT
// BEHAVIOUR, not as desired behaviour: if someone wires the function up, this test goes red, and that
// red is the signal that the change happened -- not an accident to be silenced.
func TestWindowsPathMissesPosixGlobs(t *testing.T) {
	winPath := `C:\Users\bob\.ssh\id_rsa`
	// A glob WITH a slash cannot reach a backslash path.
	if anyGlobMatches([]string{`*/.ssh/id_rsa`}, winPath) {
		t.Errorf("a slash glob now matches a Windows path -- normalizePathGlobs has been wired up; " +
			"update this test and the decision recorded in normalizepathglobs_test.go")
	}
	// A glob WITHOUT a slash still hits, via the substring fallback -- so the miss is not total, and
	// that partial coverage is exactly why this was easy to miss.
	if !anyGlobMatches([]string{`*id_rsa*`}, winPath) {
		t.Errorf("the contains-fallback no longer matches a Windows path -- that fallback is " +
			"load-bearing for endpoint rules (beat 644); do not remove it casually")
	}
	// And the same path, normalised, DOES match -- proving the function is the missing link rather
	// than the glob being wrong.
	norm := normalizePathGlobs([]string{winPath})[0]
	if !anyGlobMatches([]string{`*/.ssh/id_rsa`}, norm) {
		t.Errorf("normalised path %q still misses */.ssh/id_rsa -- then normalizePathGlobs is not "+
			"the fix and this whole decision needs re-opening", norm)
	}
}

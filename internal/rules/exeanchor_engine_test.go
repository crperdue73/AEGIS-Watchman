package rules

// exeanchor_engine_test.go — the thing `scripts/exeanchor.py:25` said should replace it.
//
// That file has carried this sentence since 2026-10-08:
//
//   "No Go toolchain on this host: the engine semantics are mirrored from the cited lines, not
//    compiled. When the toolchain returns, this should be replaced by a Go test that calls the
//    real `matches`."
//
// The toolchain returned 2026-11-01 (beat 740). Beat 780's new `scripts/staleclaim.py` flagged the
// sentence as STALE, which is exactly what it was built to do, and this file is the honest
// response: not deleting the annotation, but delivering the artifact it asked for.
//
// `exeanchor.py`'s central claim is that `exe_any` is LITERAL, not glob — `exe_any: ["*/crontab"]`
// silently matches nothing because `exeMatches` (engine.go:188) compares exact-or-basename with no
// globbing. That claim decides whether an operator's rule does anything at all, so it should be
// demonstrated by the compiled matcher, not by a transcription of it.
//
// The Python probe is NOT retired. It also checks pack lint (mode 4) and argv routing, which this
// test does not; each now covers the half it is good at, and the shared half is cross-checked.

import "testing"

func TestExeAnchorGlobIsDead(t *testing.T) {
	e := argvPack(t, `["*"]`)

	// The claim: a glob in an exe anchor is not interpreted as a glob.
	//
	// BEAT 780: my first version of this test used `*/crontab` as the glob and PASSED against a
	// mutant that added `path.Match` to exeMatches. The test looked like it proved the claim and
	// proved nothing. Cause: `path.Match("*/crontab", "crontab")` is FALSE regardless, because the
	// pattern requires a `/` and a basename has none — so that input cannot distinguish literal-only
	// from globbing. A case must be one where the glob WOULD match if globbing were enabled.
	// `cr*tab` is that case: it matches `crontab` under path.Match and not under equality.
	if exeMatches("cr*tab", "/usr/bin/crontab") {
		t.Errorf("exeMatches(\"cr*tab\", \"/usr/bin/crontab\") = true — GLOBS NOW WORK in exe " +
			"anchors (path.Match against the basename succeeded). scripts/exeanchor.py and any " +
			"pack relying on literal-only anchors need review.")
	}
	if exeMatches("*/crontab", "/usr/bin/crontab") {
		t.Errorf("exeMatches(\"*/crontab\", \"/usr/bin/crontab\") = true — GLOBS NOW WORK in exe " +
			"anchors. scripts/exeanchor.py and any pack relying on literal-only anchors need review.")
	}
	if !exeMatches("crontab", "/usr/bin/crontab") {
		t.Errorf("exeMatches(\"crontab\", \"/usr/bin/crontab\") = false — basename matching is " +
			"broken; the literal form stops working and exe_any becomes useless.")
	}
	if !exeMatches("/usr/bin/crontab", "/usr/bin/crontab") {
		t.Errorf("exeMatches exact form failed")
	}
	if exeMatches("crontab", "/usr/bin/notcrontab") {
		t.Errorf("exeMatches matched a basename that is not equal — substring behaviour leaked in")
	}
	if exeMatches("", "/usr/bin/crontab") {
		t.Errorf("exeMatches(\"\", ...) = true — an empty anchor would match everything")
	}
	_ = e
}

// TestExeAnchorRuleFiresOnLiteralOnly proves the end-to-end consequence: a rule whose exe anchor
// uses a glob never fires, while the literal spelling of the same rule does. This is the operator
// -visible half — the reason exeanchor.py exists.
func TestExeAnchorRuleFiresOnLiteralOnly(t *testing.T) {
	globRule := mustPack(t, `
name: exe-glob-anchor
rules:
  - id: exe.anchor.glob
    severity: high
    match:
      type: process.exec
      exe_any: ["*/crontab"]
`)
	litRule := mustPack(t, `
name: exe-literal-anchor
rules:
  - id: exe.anchor.literal
    severity: high
    match:
      type: process.exec
      exe_any: ["crontab"]
`)

	fireOn := func(e *Engine, exe string) bool {
		return fire(e, exe, exe, "-l")
	}

	if fireOn(NewEngine(globRule), "/usr/bin/crontab") {
		t.Errorf("a glob exe anchor FIRED — the anchor is no longer literal")
	}
	if !fireOn(NewEngine(litRule), "/usr/bin/crontab") {
		t.Errorf("a literal basename exe anchor did not fire — exe_any is broken")
	}
}

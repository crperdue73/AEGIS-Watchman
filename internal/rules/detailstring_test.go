package rules

import (
	"testing"

	"github.com/crperdue73/AEGIS-Watchman/internal/schema"
)

// BEAT 810. `detailString` and `detailStringSlice` are the SOLE readers of every exe, path,
// endpoint and argv value the engine judges -- 8 call sites in engine.go, including the exe check
// at line 114, the argv glob at 132, the path at 139 and the endpoint at 146. No Go test called
// either one; `scripts/unmirrored.py` scored them as "judged by a source-reading mirror", i.e. the
// only thing checking them was a Python transcription I wrote myself (beat 809 made that visible by
// fixing the tool's over-reporting: 8 unmirrored helpers -> 42).
//
// These tests assert MEASURED behaviour, not desired behaviour. Where the measurement shows a shape
// that could go wrong, the failure message says what went wrong and who has to decide -- a product
// decision belongs to the pack owner, made on evidence, not discovered later (beat 808).

func TestDetailStringAcceptsStringAndBytes(t *testing.T) {
	// Both representations are real: in-process producers build Go strings, and []byte shows up
	// wherever a value round-trips through a byte-oriented boundary. Both must read through.
	cases := []struct {
		name string
		in   any
		want string
	}{
		{"string", "bash", "bash"},
		{"bytes", []byte("bash"), "bash"},
	}
	for _, c := range cases {
		a := schema.Action{Detail: map[string]any{"exe": c.in}}
		if got := detailString(a, "exe"); got != c.want {
			t.Errorf("%s: detailString = %q, want %q", c.name, got, c.want)
		}
	}
}

func TestDetailStringReturnsEmptyForEveryOtherShape(t *testing.T) {
	// MEASURED: anything that is not string or []byte reads as the EMPTY STRING, which is also what
	// an ABSENT key reads as. So "the rule saw no exe" and "the rule saw an exe it could not
	// understand" are indistinguishable at every call site.
	//
	// This matters in one direction only, and that direction is worth naming: engine.go:158 treats
	// `detailString(action, k) == ""` as "field absent, skip the rule". A present-but-unreadable
	// value therefore causes a rule to be SKIPPED rather than to fire. Fail-open, not fail-closed.
	// Not attacker-reachable today -- see TestDetailStringSliceDropsNonStringElements for the
	// measurement of why -- and NOT fixed: whether an unreadable field should suppress a rule or
	// trip it is the pack owner's call.
	shapes := []struct {
		name string
		in   any
	}{
		{"int", 42},
		{"float", 1.5},
		{"bool", true},
		{"nil", nil},
		{"nested map", map[string]any{"a": "b"}},
		{"slice", []string{"bash"}},
	}
	for _, s := range shapes {
		a := schema.Action{Detail: map[string]any{"exe": s.in}}
		if got := detailString(a, "exe"); got != "" {
			t.Errorf("%s: detailString = %q, want \"\" -- behaviour changed: a non-string detail now "+
				"reads through, and every call site that tests for \"\" just changed meaning. "+
				"Re-check engine.go:158 (skip-on-empty) before accepting this", s.name, got)
		}
	}
	// Absent key and nil map must agree with the above, or callers cannot treat them alike.
	if got := detailString(schema.Action{Detail: map[string]any{}}, "exe"); got != "" {
		t.Errorf("absent key: detailString = %q, want \"\"", got)
	}
	if got := detailString(schema.Action{Detail: nil}, "exe"); got != "" {
		t.Errorf("nil map: detailString = %q, want \"\"", got)
	}
}

func TestDetailStringSliceReadsBothSliceShapes(t *testing.T) {
	// []string is what an in-process producer builds; []any is what JSON decoding produces. The
	// engine must give the SAME answer for both, or a rule behaves differently in-process than it
	// does on wire-decoded input -- the cross-backend trap (a fixture where the two implementations
	// never diverge proves nothing).
	want := []string{"bash", "-c", "evil"}
	for _, c := range []struct {
		name string
		in   any
	}{
		{"[]string", []string{"bash", "-c", "evil"}},
		{"[]any", []any{"bash", "-c", "evil"}},
	} {
		a := schema.Action{Detail: map[string]any{"argv": c.in}}
		got := detailStringSlice(a, "argv")
		if len(got) != len(want) {
			t.Fatalf("%s: len = %d, want %d (%#v)", c.name, len(got), len(want), got)
		}
		for i := range want {
			if got[i] != want[i] {
				t.Errorf("%s: [%d] = %q, want %q (whole slice %#v)", c.name, i, got[i], want[i], got)
			}
		}
	}
}

func TestDetailStringSliceDropsNonStringElements(t *testing.T) {
	// MEASURED, and this is the one that deserves a name: a []any with a NON-STRING element loses
	// that element SILENTLY and every later element SHIFTS DOWN ONE POSITION.
	//
	//	argv = []any{"bash", 42, "evil"}  ->  []string{"bash", "evil"}
	//
	// A rule that says "argv[1] must be -c" now reads "evil". Whether that is a bypass depends on
	// the rule; the mechanism is that a value the engine cannot read is deleted rather than
	// reported.
	//
	// REACHABILITY, measured 2026-11-04 (beat 810): NOT reachable from the auditd path. The
	// producer at internal/telemetry/telemetry.go:133 builds `[]any` by appending Go strings only,
	// so every element is a string. The engine is constructed and driven in
	// internal/collector/behavior.go:159-160, reading a local auditd tail via FileSource -- NOT from
	// JSON decoded off the wire, which is the only way a non-string element could enter.
	//
	// So this is a latent shape, not a live hole. Pinned rather than fixed: the choice between
	// "drop silently" and "reject the action" is the pack owner's, and if a second telemetry source
	// (the eBPF adapter, docs/R2-EBPF-DESIGN.md) ever produces non-string elements this test is
	// what will notice.
	a := schema.Action{Detail: map[string]any{"argv": []any{"bash", 42, "evil"}}}
	got := detailStringSlice(a, "argv")
	want := []string{"bash", "evil"} // 42 dropped; "evil" shifted from index 2 to index 1
	if len(got) != len(want) || got[0] != want[0] || got[1] != want[1] {
		t.Fatalf("detailStringSlice = %#v, want %#v -- behaviour changed: a non-string element is no "+
			"longer dropped, or no longer shifts later elements. If non-string elements are now "+
			"rejected instead, that is a BETTER behaviour than the one pinned here: update this test "+
			"and check whether any rule relied on the old shift", got, want)
	}
}

func TestDetailStringSliceTreatsAStringAsOneElement(t *testing.T) {
	// MEASURED: a single string becomes a ONE-ELEMENT slice, it is NOT split on whitespace.
	//
	//	argv = "bash -c evil"  ->  []string{"bash -c evil"}
	//
	// This is the defensible reading (the engine cannot know whether the space was quoted), but it
	// is also the shape that hides a payload from an argv-element glob: `anyArgvMatches` tests every
	// element, so no element ever equals "-c". The JOINED-line pass at engine.go:211 is what saves
	// phrase globs here, and that is exactly the mechanism beat 650 found the fixtures were
	// supplying by hand.
	//
	// Pinned, not fixed: splitting a string on spaces would be WRONG for a genuinely quoted argument
	// containing spaces, and the engine has no quote information at this layer. Named so the next
	// person sees the trade instead of rediscovering it.
	a := schema.Action{Detail: map[string]any{"argv": "bash -c evil"}}
	got := detailStringSlice(a, "argv")
	if len(got) != 1 || got[0] != "bash -c evil" {
		t.Fatalf("detailStringSlice = %#v, want exactly [\"bash -c evil\"] -- behaviour changed: a "+
			"bare string is now split or dropped. If it is now split on whitespace, quoted arguments "+
			"containing spaces are now broken; if it is now nil, every single-string argv rule has "+
			"gone blind", got)
	}
}

func TestDetailStringSliceUnreadableShapeReadsAsAbsent(t *testing.T) {
	// A non-slice, non-string shape reads as nil -- which is what an ABSENT key reads as. Callers
	// cannot tell "no argv" from "argv I could not parse", same conflation as detailString's "".
	for _, in := range []any{7, 1.5, true, map[string]any{"a": "b"}} {
		a := schema.Action{Detail: map[string]any{"argv": in}}
		if got := detailStringSlice(a, "argv"); got != nil {
			t.Errorf("argv=%#v: detailStringSlice = %#v, want nil -- behaviour changed: an unreadable "+
				"shape now yields a slice, so callers that test for nil just changed meaning", in, got)
		}
	}
	if got := detailStringSlice(schema.Action{Detail: nil}, "argv"); got != nil {
		t.Errorf("nil map: detailStringSlice = %#v, want nil", got)
	}
}

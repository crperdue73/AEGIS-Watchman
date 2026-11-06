package rules

// detail_engine_test.go -- the compiled counterpart to `scripts/presencecheck_test.py`.
//
// That script's header carried this (beat 781 found it, beat 782 resolves it):
//
//   "No Go toolchain on this host. `detail_string` below is a transcription of engine.go:307 and the
//    transcription is unverified."
//
// Measured at beat 782: `grep detailString internal/rules/*_test.go` returned ZERO files. Every
// path, endpoint, and exe the engine ever judges is read by `detailString` or `detailStringSlice`
// (engine.go:105, 123, 130, 137, 144, 149, 157, 173 -- nine call sites), and no test in the tree
// named either function. `go test ./...` was green because the fixtures supply `map[string]any`
// values that happen to decode correctly, not because anyone asserted the reader's contract.
//
// Same shape as beat 781's `substringNeedle`: coverage by accident, behind an honest note that said
// the coverage was absent. This file makes the contract a compiled fact.
//
// Callie's 7566 named the general form better than I did: "a number is only worth the width of what
// it measured." A green suite is a width claim, and this is where mine was narrower than it sounded.

import (
	"testing"

	"github.com/crperdue73/AEGIS-Watchman/internal/schema"
)

func TestDetailStringCases(t *testing.T) {
	cases := []struct {
		name string
		det  map[string]any
		key  string
		want string
	}{
		{"string", map[string]any{"path": "/etc/passwd"}, "path", "/etc/passwd"},
		{"bytes", map[string]any{"path": []byte("/etc/shadow")}, "path", "/etc/shadow"},
		{"int is not a string", map[string]any{"path": 42}, "path", ""},
		{"bool is not a string", map[string]any{"path": true}, "path", ""},
		{"nil value", map[string]any{"path": nil}, "path", ""},
		{"slice is not a string", map[string]any{"path": []any{"a", "b"}}, "path", ""},
		{"missing key", map[string]any{"other": "x"}, "path", ""},
		{"nil map", nil, "path", ""},
		{"empty string is a value, not an absence", map[string]any{"path": ""}, "path", ""},
	}

	for _, c := range cases {
		got := detailString(schema.Action{Detail: c.det}, c.key)
		if got != c.want {
			t.Errorf("%s: detailString(detail=%v, %q) = %q, want %q", c.name, c.det, c.key, got, c.want)
		}
	}
}

func TestDetailStringSliceCases(t *testing.T) {
	cases := []struct {
		name string
		det  map[string]any
		want []string
		n    int
	}{
		{"[]string passes through", map[string]any{"argv": []string{"pip", "install"}}, []string{"pip", "install"}, 2},
		{"[]any from JSON decoding", map[string]any{"argv": []any{"pip", "install"}}, []string{"pip", "install"}, 2},
		{"non-string elements are dropped, not fatal", map[string]any{"argv": []any{"a", 7, nil, "b"}}, []string{"a", "b"}, 2},
		{"a bare string becomes a one-element slice", map[string]any{"argv": "solo"}, []string{"solo"}, 1},
		{"wrong type yields nil", map[string]any{"argv": 12}, nil, 0},
		{"missing key yields nil", map[string]any{}, nil, 0},
		{"nil map yields nil", nil, nil, 0},
		{"empty []any yields an empty non-nil slice", map[string]any{"argv": []any{}}, []string{}, 0},
	}

	for _, c := range cases {
		got := detailStringSlice(schema.Action{Detail: c.det}, "argv")
		if len(got) != c.n {
			t.Errorf("%s: detailStringSlice(...) = %v (len %d), want len %d", c.name, got, len(got), c.n)
			continue
		}
		for i := range c.want {
			if got[i] != c.want[i] {
				t.Errorf("%s: [%d] = %q, want %q", c.name, i, got[i], c.want[i])
			}
		}
	}
}

// TestDetailStringDrivesEveryActionFamily is the load-bearing part: it proves the readers are not
// merely correct in isolation but are the actual route the engine uses for each action type. If
// someone "optimises" an action to read `action.Detail["path"].(string)` directly, a wire-decoded
// action carrying []byte would silently stop matching, and every existing fixture would still pass
// because the fixtures build typed maps by hand.
func TestDetailStringDrivesEveryActionFamily(t *testing.T) {
	const glob = "*/etc/shadow*"

	// file.access via string
	if !pathFire(t, glob, "/etc/shadow") {
		t.Fatalf("control: the fixture must fire before this test means anything")
	}

	// file.access via []byte -- the wire-decoded shape. A direct type assertion would drop this.
	p := mustPack(t, `
name: detail-semantics
rules:
  - id: detail.semantics.probe
    severity: medium
    match:
      type: file.access
      path_glob_any: ["`+glob+`"]
`)
	e := NewEngine(p)
	act := schema.Action{
		Type:   "file.access",
		Detail: map[string]any{"path": []byte("/etc/shadow")},
	}
	evs := e.Match(schema.Host{ID: "h1"}, nil, act, nil, timeNow())
	if len(evs) == 0 {
		t.Errorf("a []byte path no longer reaches the matcher — detailString's []byte branch is gone, " +
			"and wire-decoded file.access actions have silently stopped matching")
	}
}

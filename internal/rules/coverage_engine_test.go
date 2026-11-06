package rules

// coverage_engine_test.go -- the nil-Pack guard, which `go test -cover` showed had never executed
// (beat 784: Pack.rules() at 66.7%, the `p == nil` branch at 0%).
//
// A guard that has never run is not a guard. This one sits in the PACK-LOADING path, and the
// failure mode without it is a nil dereference that takes down the collector while it is asking
// which rules are inert -- i.e. the check that tells you a rule can never fire, itself panicking.
//
// NewEngine (engine.go:23) already skips nil packs, so the guard is not reached through the normal
// construction path. That makes it *defensive*, not dead: ActionTypes and InertRules are exported
// and take a caller's []*Pack directly, so a caller can hand one a nil and nothing stops them.

import "testing"

func TestNilPackIsNotAPanic(t *testing.T) {
	// The exported signatures accept a nil *Pack in the slice. If Pack.rules() loses its guard,
	// this panics -- which is exactly what a caller building a pack list from a map of optional
	// overlays would hit in production.
	if got := ActionTypes([]*Pack{nil}); len(got) != 0 {
		t.Errorf("ActionTypes([nil]) = %v, want empty", got)
	}
	if got := InertRules([]*Pack{nil}, map[string]bool{}); len(got) != 0 {
		t.Errorf("InertRules([nil], {}) = %v, want empty", got)
	}

	// A nil pack alongside a real one must not swallow the real one.
	real := mustPack(t, `
name: nil-guard
rules:
  - id: guard.probe
    severity: low
    match:
      type: file.access
      path_glob_any: ["*/x"]
`)
	types := ActionTypes([]*Pack{nil, real})
	if len(types) != 1 || types[0] != "file.access" {
		t.Errorf("ActionTypes([nil, real]) = %v, want [file.access] -- a nil pack must be skipped, "+
			"not silently treated as the whole list", types)
	}

	// And the guard itself, called directly on a nil receiver.
	var p *Pack
	if got := p.rules(); got != nil {
		t.Errorf("(*Pack)(nil).rules() = %v, want nil", got)
	}
}

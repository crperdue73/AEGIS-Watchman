package central

import (
	"net/http"
	"reflect"
	"sort"
	"strings"
	"testing"
	"unsafe"
)

// BEAT 806. Complete replacement for the candidate probe in endpoints_surface_test.go.
//
// Last beat I measured that a probe cannot discover a route by accident: /api/v1/fleet/x and /x/y/z
// both report pattern "/" (the catch-all), so a route registered at an unguessed path stays
// invisible. I recorded the full fix as "reflection over unexported fields, or a router that can
// list routes. Dad's call."
//
// Measured this beat: the reflection path works, and cleanly. net/http.ServeMux keeps its
// registrations in an unexported `patterns []*pattern`, and each pattern carries `str` (the
// pattern text) and `loc` ("file.go:LINE", the registration site, populated by ServeMux's own
// registration). Reading it gives a COMPLETE list -- 7 patterns on the real service, every one
// traceable to its line in http.go -- not a sample.
//
// Why this is acceptable in a security tool despite `unsafe`:
//   - It is TEST-ONLY. `unsafe` never reaches the shipped binary; the tree had zero occurrences of
//     the import before this file and still has zero in any non-test file.
//   - The alternative -- swapping http.ServeMux for a third-party router that can list its routes --
//     changes the shipped HTTP stack, a far larger and riskier surface than a test helper.
//   - It CANNOT fail silently. See assertEnumerationWorks: if a future Go release renames the field
//     or changes the struct shape, this test fails loudly with instructions, instead of quietly
//     enumerating nothing and passing. That was the failure mode I explicitly refused to ship in
//     beat 648 (a test green for the wrong reason) and beat 645 (an importer that scored its own
//     ImportError as "the probe went red, therefore it works").

// muxPattern is one registration read out of a ServeMux.
type muxPattern struct {
	str string
	loc string
}

// enumerateMux returns every pattern registered on mux, or an error describing why it could not.
// It relies on the internal shape of net/http.ServeMux as of Go 1.22; callers MUST verify the
// result is non-empty and plausible (see assertEnumerationWorks) rather than trusting it blindly.
func enumerateMux(t *testing.T, mux *http.ServeMux) ([]muxPattern, error) {
	t.Helper()

	v := reflect.ValueOf(mux)
	if v.Kind() != reflect.Ptr || v.IsNil() {
		return nil, errEnum("enumerateMux: not a *http.ServeMux")
	}
	v = v.Elem()
	if v.Kind() != reflect.Struct {
		return nil, errEnum("enumerateMux: ServeMux is not a struct")
	}

	patField := v.FieldByName("patterns")
	if !patField.IsValid() {
		return nil, errEnum("enumerateMux: no `patterns` field on http.ServeMux (Go changed its internals?)")
	}
	// patterns is unexported; read it through unsafe. Test-only.
	patField = reflect.NewAt(patField.Type(), unsafe.Pointer(patField.UnsafeAddr())).Elem()
	if patField.Kind() != reflect.Slice {
		return nil, errEnum("enumerateMux: `patterns` is not a slice")
	}

	out := make([]muxPattern, 0, patField.Len())
	for i := 0; i < patField.Len(); i++ {
		p := patField.Index(i)
		for p.Kind() == reflect.Ptr {
			if p.IsNil() {
				break
			}
			p = p.Elem()
		}
		if p.Kind() != reflect.Struct {
			continue
		}
		var mp muxPattern
		for _, name := range []string{"str", "loc"} {
			f := p.FieldByName(name)
			if !f.IsValid() {
				continue
			}
			if f.Kind() != reflect.String {
				continue
			}
			f = reflect.NewAt(f.Type(), unsafe.Pointer(f.UnsafeAddr())).Elem()
			if name == "str" {
				mp.str = f.String()
			} else {
				mp.loc = f.String()
			}
		}
		out = append(out, mp)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].str < out[j].str })
	return out, nil
}

type errEnum string

func (e errEnum) Error() string { return string(e) }

// assertEnumerationWorks fails the test LOUDLY if the enumeration mechanism stopped working.
// Without this, a Go upgrade that renames `patterns` would make every route check vacuous: an
// empty list trivially satisfies "every served route is declared", which is the precise shape of
// a guard that passes because it stopped looking.
func assertEnumerationWorks(t *testing.T, got []muxPattern, err error) {
	t.Helper()
	if err != nil {
		t.Fatalf("mux enumeration broke: %v\n\n"+
			"This test reads net/http.ServeMux's unexported `patterns` field via reflection. If Go "+
			"changed it, update enumerateMux -- do NOT delete this test, and do NOT let it skip. "+
			"Without enumeration the route-surface check becomes vacuous and will pass while "+
			"checking nothing.", err)
	}
	if len(got) == 0 {
		t.Fatalf("mux enumeration returned ZERO patterns for a service that serves at least /healthz -- " +
			"the enumeration is broken, so any comparison against it would pass vacuously")
	}
	// Positive control: /healthz is registered unconditionally by NewService.
	found := false
	for _, p := range got {
		if p.str == "/healthz" {
			found = true
			break
		}
	}
	if !found {
		t.Fatalf("mux enumeration did not include /healthz, which NewService always registers -- "+
			"enumeration is unreliable, refusing to draw conclusions from it. Got: %v", got)
	}
	// Every pattern should carry a registration site; a missing loc means we read the wrong field
	// (e.g. a same-named field on a differently-shaped struct).
	for _, p := range got {
		if p.loc == "" {
			t.Fatalf("pattern %q has no registration loc -- we are reading the wrong field, or Go "+
				"stopped recording it", p.str)
		}
	}
}

// TestEveryServedRouteIsDeclared is the complete form of the check attempted in beat 805. It reads
// the ACTUAL registration list off the mux and requires every entry to appear in centralEndpoints,
// which is itself cross-checked against docs/deploy-central.md by TestCentralEndpointsMatchDoc.
// Between the two, a route cannot be served without being both declared and documented.
func TestEveryServedRouteIsDeclared(t *testing.T) {
	mux := NewService(NewMemoryStore()).Handler()

	patterns, err := enumerateMux(t, mux)
	assertEnumerationWorks(t, patterns, err)

	declared := make(map[string]bool, len(centralEndpoints))
	for _, ep := range centralEndpoints {
		declared[ep.path] = true
	}

	var undeclared []string
	for _, p := range patterns {
		// Strip a method or host prefix if one is ever used ("GET /x", "example.com/x").
		path := p.str
		if i := strings.LastIndex(path, " "); i >= 0 {
			path = path[i+1:]
		}
		if declared[path] {
			continue
		}
		undeclared = append(undeclared, p.str+"  ("+p.loc+")")
	}
	if len(undeclared) > 0 {
		t.Errorf("these routes are REGISTERED but not in centralEndpoints, so also not compared "+
			"against docs/deploy-central.md: %v", undeclared)
	}

	// The reverse direction -- declared but not registered -- is already covered by
	// TestCentralEndpointsMatchDoc step 1. Nothing is re-asserted here.
}

// TestEnumerationMatchesKnownSurface pins the exact registered surface. It is the counterweight to
// the test above: if enumeration silently degraded to returning only SUBSET of routes, the
// undeclared-route test would still pass, so the total is asserted independently from a fixed
// expectation. Adding a route to centralEndpoints requires updating this list -- deliberately, so the
// surface is a decision someone made rather than a side effect someone didn't notice.
func TestEnumerationMatchesKnownSurface(t *testing.T) {
	want := []string{
		"/",
		"/agents",
		"/api/v1/actions",
		"/api/v1/agents",
		"/api/v1/events",
		"/api/v1/fleet",
		"/healthz",
	}
	mux := NewService(NewMemoryStore()).Handler()
	patterns, err := enumerateMux(t, mux)
	assertEnumerationWorks(t, patterns, err)

	got := make([]string, 0, len(patterns))
	for _, p := range patterns {
		got = append(got, p.str)
	}
	if strings.Join(got, ",") != strings.Join(want, ",") {
		t.Errorf("the served HTTP surface changed.\n got (%d): %v\nwant (%d): %v\n"+
			"Adding a route is a real decision: update centralEndpoints, docs/deploy-central.md, "+
			"and this list together.", len(got), got, len(want), want)
	}
}

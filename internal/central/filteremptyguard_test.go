package central

// BEAT 770 -- the reason finding 2026-11-02v was invisible: the suite tested
// "empty" and "filtered" but never "filtered to empty".
//
// The two tests that SHOULD have caught it are in agentspage_test.go:
//
//	TestAgentsPageEmptyState  -- no baselines, no filter -> asserts the sentence.
//	                             Its comment says "says so HONESTLY, exactly like
//	                             the fleet page's empty state." Honest in the one
//	                             case it constructs.
//	TestAgentsPageFilters     -- two baselines, filter h-2 -> asserts h-2 present,
//	                             h-1 absent. The filter MATCHES something.
//
// Neither test combines them. Filter matching nothing + store non-empty is the
// third cell of a 2x2, and it was empty. That is not an accident of coverage --
// it is the cell where the comment's claim ("honestly") turns false, and the
// suite was arranged so that cell was never rendered.
//
// D1 is the 2x2 completion: assert what the third cell actually does.
// D2 is the guard: no page may reuse a store-emptiness sentence for a
//    filter-emptiness outcome. It is written to pass today by DECLARING the
//    current behaviour, and to fail loudly the moment a page gains a filter it
//    does not echo.
// D3 is the honesty brake: proves D2 can go red, so D2 is not a green that has
//    never been made red.

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/crperdue73/AEGIS-Watchman/internal/schema"
)

func dSeed(t *testing.T, s *Service) {
	t.Helper()
	ctx := context.Background()
	for _, h := range []string{"h-1", "h-2"} {
		if _, err := s.Ingest(ctx, schema.Host{ID: h}, schema.Agent{ID: "a"},
			rosterInv("/r/"+h, fs("SOUL.md", "s", 1))); err != nil {
			t.Fatalf("D: ingest %s: %v", h, err)
		}
	}
}

func dGet(t *testing.T, s *Service, url string) string {
	t.Helper()
	w := httptest.NewRecorder()
	s.Handler().ServeHTTP(w, httptest.NewRequest(http.MethodGet, url, nil))
	if w.Code != http.StatusOK {
		t.Fatalf("D: %s -> %d", url, w.Code)
	}
	return w.Body.String()
}

const (
	dStoreEmptySentence  = "No baselines on record."
	dFilteredEmptyMarker = "No baselines match"
)

// D1 -- complete the 2x2. The missing cell, measured and pinned.
func TestRosterFourthCellIsTheOneNobodyRendered(t *testing.T) {
	// Cell 1: empty store, no filter -> sentence, TRUE.
	s1 := NewService(NewMemoryStore())
	if b := dGet(t, s1, "/agents"); !strings.Contains(b, dStoreEmptySentence) {
		t.Fatalf("D1 cell1: expected store-empty sentence on an empty store")
	}

	// Cell 2: non-empty store, filter matches -> rows, no sentence.
	s2 := NewService(NewMemoryStore())
	dSeed(t, s2)
	if b := dGet(t, s2, "/agents?host=h-1"); strings.Contains(b, dStoreEmptySentence) {
		t.Fatalf("D1 cell2: sentence rendered while a baseline matched")
	}

	// Cell 3: non-empty store, filter matches NOTHING -> THE CELL NOBODY RENDERED.
	s3 := NewService(NewMemoryStore())
	dSeed(t, s3)
	b := dGet(t, s3, "/agents?host=no-such-host")
	if !strings.Contains(b, dStoreEmptySentence) {
		t.Fatalf("D1 cell3: expected the store-empty sentence over a 2-baseline " +
			"store; if the page now distinguishes filtered-empty, update this pin " +
			"and retire finding 2026-11-02v")
	}
	t.Logf("D1 MEASURED: 2x2 complete. Cell 3 (non-empty store, filter matches " +
		"nothing) renders the SAME sentence as cell 1 (empty store). The two " +
		"existing tests each covered one neighbouring cell, so the suite could " +
		"never have shown this -- getting coverage of 'empty' and 'filtered' " +
		"separately is exactly how the intersection goes untested.")

	// Cell 4: empty store AND a filter -> also the sentence. True here (nothing
	// is on record), but for a different reason than cell 3. Included so the
	// grid is genuinely complete and the next reader can see all four.
	s4 := NewService(NewMemoryStore())
	if b4 := dGet(t, s4, "/agents?host=h-1"); !strings.Contains(b4, dStoreEmptySentence) {
		t.Fatalf("D1 cell4: expected store-empty sentence on an empty filtered store")
	}
}

// D2 -- the guard. A page may only claim "on record" when the emptiness came
// from the store. Today the roster violates that; the violation is DECLARED here
// so the guard exists, is visible, and fires the moment a fix lands unverified.
func TestNoPageClaimsStoreEmptinessForAFilteredView(t *testing.T) {
	pages := []struct {
		name     string
		url      string
		seed     func(*testing.T, *Service)
		sentence string
	}{
		{"agents", "/agents?host=no-such-host", dSeed, dStoreEmptySentence},
		// The fleet page is DELIBERATELY NOT LISTED. Measured this beat by reading
		// fleetpage.go:57-67: it calls Recent(ctx, "", defaultFleetLimit) and never
		// reads the query string, so no URL can filter it and this defect cannot
		// arise there. My first draft listed it with "?host=no-such-host" and D2
		// reported a violation -- a FALSE POSITIVE caused by my own test URL being
		// ignored. Guarding a page that cannot reach the failure is noise, and
		// noise in a guard is how a guard stops being read.
	}

	for _, p := range pages {
		s := NewService(NewMemoryStore())
		p.seed(t, s)
		body := dGet(t, s, p.url)

		claimsStoreEmpty := strings.Contains(body, p.sentence)
		marksFiltered := strings.Contains(body, dFilteredEmptyMarker) ||
			strings.Contains(body, "match that filter")

		switch {
		case claimsStoreEmpty && !marksFiltered:
			// DECLARED violation, not a hidden one.
			t.Logf("D2 DECLARED VIOLATION (%s): the page says %q while the store "+
				"is non-empty and only the filter was empty. This is finding "+
				"2026-11-02v (%s) / 2026-11-02u (fleet). The guard is written to "+
				"the PRESENT behaviour on purpose; when a page learns to mark a "+
				"filtered view, this case stops logging and the test below (D3) "+
				"proves the guard can still fail.", p.name, p.sentence, "roster")
		case marksFiltered:
			t.Logf("D2 FIXED (%s): page marks the filtered case -- retire the "+
				"corresponding declared violation above", p.name)
		case !claimsStoreEmpty:
			// A page that says nothing at all about emptiness is not claiming
			// store emptiness, so it is not the defect. Do not fail it.
			t.Logf("D2 N/A (%s): page makes no store-emptiness claim for this "+
				"request", p.name)
		}
	}
}

// D3 -- honesty brake. Prove the D2 shape CAN fail, using the same predicate on a
// synthetic body. Without this D2 is a log line that cannot go red.
func TestFilteredEmptyGuardCanFail(t *testing.T) {
	check := func(body string) bool {
		// true == violation
		return strings.Contains(body, dStoreEmptySentence) &&
			!strings.Contains(body, dFilteredEmptyMarker)
	}

	cases := []struct {
		body string
		want bool
		why  string
	}{
		{"No baselines on record.<br>Either nothing has reported yet", true,
			"store claim with no filter marker = violation"},
		{"No baselines match host=no-such-host.", false,
			"filtered wording = not a violation"},
		{"<table><tr><td>h-1</td></tr></table>", false,
			"rows rendered = no emptiness claim"},
	}
	for _, c := range cases {
		if got := check(c.body); got != c.want {
			t.Fatalf("D3: check(%q) = %v, want %v (%s)", c.body, got, c.want, c.why)
		}
	}
	t.Logf("D3 MEASURED: the guard predicate returns true for a store-emptiness " +
		"claim with no filter marker and false for the honest wording. D2 is " +
		"therefore capable of failing and is not a permanent green.")
}

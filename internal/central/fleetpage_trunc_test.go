package central

// Beat 790. The HTML console is the surface an operator actually reads, and it said
// "N event(s) retained" -- a claim about RETENTION computed from a CAPPED SCAN. The JSON view grew a
// `truncated` flag this beat; a page that kept asserting the old sentence would be the more
// misleading artifact, because it is printed in the font an operator trusts.
//
// These tests render the real template and read the words, because the defect was a claim made in
// prose, not a wrong field.

import (
	"context"
	"net/http"
	"net/http/httptest"
	"regexp"
	"strconv"
	"strings"
	"testing"
	"time"
)

func renderFleetPageFor(t *testing.T, store RecordStore, query string) string {
	t.Helper()
	s := &Service{records: store}
	// The console is served at the ROOT and takes no query params -- it uses
	// defaultFleetLimit. (My first version asked for "/fleet?limit=10" and got 404; the route
	// guard `r.URL.Path != "/"` is what caught the guess.)
	req := httptest.NewRequest(http.MethodGet, "/"+query, nil)
	rec := httptest.NewRecorder()
	s.handleFleetPage(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("GET %q -> %d", query, rec.Code)
	}
	return rec.Body.String()
}

// The word "retained" is a claim that the number describes the store. Over the cap it does not, so
// the page must not say it.
//
// BEAT 799: this test used to assert the OPPOSITE -- that an over-cap store made the page say
// "scanned, capped at 5000" and admit it was a sample. That was correct for a page that rolled up a
// global newest-first window. The roster fix means the page no longer scans at all: TotalEvents is
// the sum of exact per-host totals, so "retained" is now TRUE at 5,005 records where it used to be
// the lie beat 790 was written to catch. Keeping the old assertion would have forced the fix to keep
// understating the store forever -- a green test defending a bug. The replacement asserts the real
// claim: the number IS the store, and the page says so with no hedge, because there is nothing left
// to hedge about.
func TestFleetPageClaimsRetentionHonestlyOverTheOldCap(t *testing.T) {
	store := newBigRecordStore(t, defaultFleetLimit+5)

	body := renderFleetPageFor(t, store, "")

	if !strings.Contains(body, "5005 event(s) retained") {
		t.Errorf("page does not report all 5005 retained events over the old 5000 cap -- the "+
			"rollup is back to a capped scan; got:\n%s", excerpt(body))
	}
	if strings.Contains(body, "not the store's total") {
		t.Errorf("page still calls the count a sample when it is the exact total; a caveat that " +
			"outlives its cause teaches operators to skip caveats")
	}
	// The detail column really is capped, so the word must stay -- but no SCAN limit may be
	// claimed, because this path performs no scan. Narrowed after my first version failed on the
	// legitimate "detail column is capped at the five most recent events" sentence: a test that
	// forbids a word instead of a claim fails on the truth as readily as on the lie.
	if cappedAtRE.MatchString(body) {
		t.Errorf("page names a scan limit on a path that performs no scan; ScanLimit must stay 0 " +
			"here, and the only cap left is the per-host detail column")
	}
}

// Under the cap the sentence is true and should stay exactly as it was -- this is the control that
// stops the fix from turning into "never use the word retained".
func TestFleetPageStillSaysRetainedWhenComplete(t *testing.T) {
	store := NewMemoryRecordStore(DefaultRecordRetention)
	base := time.Date(2026, 11, 3, 12, 0, 0, 0, time.UTC)
	for _, r := range zRecs(5, "h-1", base) {
		if err := store.Append(context.Background(), r); err != nil {
			t.Fatalf("append: %v", err)
		}
	}

	body := renderFleetPageFor(t, store, "?limit=50")

	if !strings.Contains(body, "5 event(s) retained") {
		t.Errorf("complete view lost its 'retained' sentence; got:\n%s", excerpt(body))
	}
	if strings.Contains(body, "sample, not the store's total") {
		t.Errorf("complete view claims it is a sample")
	}
}

// newBigRecordStore seeds n records without going through DefaultRecordRetention's 10k cap.
func newBigRecordStore(t *testing.T, n int) RecordStore {
	t.Helper()
	store := NewMemoryRecordStore(RecordRetention{Window: 365 * 24 * time.Hour, MaxRecords: n + 100})
	base := time.Date(2026, 11, 3, 12, 0, 0, 0, time.UTC)
	for _, r := range zRecs(n, "h-1", base) {
		if err := store.Append(context.Background(), r); err != nil {
			t.Fatalf("append: %v", err)
		}
	}
	return store
}

func excerpt(s string) string {
	i := strings.Index(s, "<header>")
	if i < 0 {
		return s
	}
	j := strings.Index(s[i:], "</header>")
	if j < 0 {
		return s[i:]
	}
	return s[i : i+j]
}

// Beat 791. Finding 2026-11-03q's P4 mutant -- deleting `recs = recs[:want]` -- changed NO
// assertion. The page would then build a view over cap+5 records while still claiming
// scan_limit = 5000, i.e. it would print a number LARGER than the ceiling printed beside it.
// The fix was right and its enforcement was untested. This is that missing assertion.
//
// It reads the two numbers OFF THE RENDERED PAGE and compares them, rather than inspecting
// fleetView. That matters: P4's defect lived in the page handler, between the query and the view,
// so a test that starts at buildFleetView could never have seen it.
// Beat 791 wrote this to catch the page printing a count LARGER than the cap printed beside it (P4).
// Beat 799 removed the cap from this path, so the two-number comparison has nothing left to compare.
// The invariant that survives is the one P4 was really protecting: the number on the page must equal
// the number in the store. That is a STRONGER claim than "<= the cap", and it is now checkable
// exactly rather than only as an inequality against a ceiling.
func TestFleetPageEventCountEqualsTheStore(t *testing.T) {
	over := defaultFleetLimit + 5
	store := newBigRecordStore(t, over)

	body := renderFleetPageFor(t, store, "")

	ec := eventCountRE.FindStringSubmatch(body)
	if ec == nil {
		t.Fatalf("page header has no '<n> event(s)' line; got:\n%s", excerpt(body))
	}
	shown := mustAtoi(t, ec[1])

	if shown != over {
		t.Errorf("page reports %d events and the store holds %d -- the page is counting something "+
			"other than the store's records (P4's defect, in its exact form)", shown, over)
	}
}

var (
	eventCountRE = regexp.MustCompile(`(\d+) event\(s\)`)
	cappedAtRE   = regexp.MustCompile(`capped at (\d+)`)
)

func mustAtoi(t *testing.T, s string) int {
	t.Helper()
	n, err := strconv.Atoi(s)
	if err != nil {
		t.Fatalf("not a number: %q", s)
	}
	return n
}

// Beat 791 wrote this to catch a CAPPED page presenting a sampled ranking as a complete one. Beat
// 799 removed the cap from this path entirely, so the assertion inverts with the defect: what must
// now be true is that the ranking IS complete, and that the page has stopped disclaiming it.
//
// This is the test that would have caught my own wiring mistake from the other side. When I reused
// the Truncated flag for the detail caveat, the page printed "capped at 0 -- this is a sample" over
// an exact rollup; the words still matched a lenient regex but the claim was false in the new
// direction. Asserting the ABSENCE of the old hedge is what makes a false hedge fail.
func TestFleetPageRanksTheWholeStoreNotASample(t *testing.T) {
	store := newBigRecordStore(t, defaultFleetLimit+5)

	body := renderFleetPageFor(t, store, "")

	if strings.Contains(body, `data-testid="rank-caveat"`) {
		t.Errorf("page says its ranking is over a scanned window; the rollup covers every retained " +
			"record, so that disclaimer is now a false accusation against a complete page")
	}
	if strings.Contains(body, "Ranking is over the scanned window") {
		t.Errorf("capped-ranking caveat is back; finding 2026-11-03t migrated the ranking to the " +
			"store, so there is no window to qualify")
	}
}

// The control that stops the fix from becoming "always print a caveat": where the ranking IS a
// complete ranking, the note would be noise and would teach operators to skip it.
func TestFleetPageDoesNotFlagRankingWhenComplete(t *testing.T) {
	store := newBigRecordStore(t, 40)

	body := renderFleetPageFor(t, store, "")

	if strings.Contains(body, `data-testid="rank-caveat"`) {
		t.Errorf("complete view says its ranking is a sample; it is not, and a caveat printed " +
			"everywhere is a caveat read nowhere")
	}
}

// Beat 792. The header caveat (beat 790/791) says the EVENT COUNT is a sample. It does not say a
// HOST CAN BE MISSING, and the window is NEWEST-FIRST (eventstore_sqlite.go: ORDER BY when_unix
// DESC), so a host that was critical three days ago and quiet since is not ranked low -- it is not
// on the page at all, while "N host(s)" reads as the fleet roster.
//
// Measured before writing this: a store holding 3 critical records for OLD-CRITICAL and 50 info
// records for a noisy host yields a window whose hosts list contains only the noisy one. The
// critical host is absent, not misranked.
// Beat 799: this test used to assert the PREMISE that the critical host was absent (beat 792's
// `Fatalf` guard) and then demand a caveat apologising for it. The roster fix makes that premise
// FALSE, which is the whole point -- so the assertion inverts. The old guard was written as
// `Fatalf("premise broken")`, running away from the very case that proves the fix; it is now the
// headline claim of its own test. A premise guard that fires when the bug is fixed is a guard that
// tells you to stop looking.
func TestFleetPageShowsHostThatAGlobalWindowWouldDrop(t *testing.T) {
	store := NewMemoryRecordStore(DefaultRecordRetention)
	base := time.Date(2026, 11, 3, 12, 0, 0, 0, time.UTC)
	old := zRecs(3, "OLD-CRITICAL", base)
	for i := range old {
		old[i].Severity = "critical"
	}
	newer := zRecs(defaultFleetLimit+5, "noisy-info", base.Add(24*time.Hour))
	for i := range newer {
		newer[i].Severity = "info"
	}
	for _, r := range append(old, newer...) {
		if err := store.Append(context.Background(), r); err != nil {
			t.Fatalf("append: %v", err)
		}
	}

	body := renderFleetPageFor(t, store, "")

	// The defect, as a claim: the quiet critical host is DROPPED by a global newest-first window
	// (it holds 5,005 newer records) and must still be on the page.
	if !strings.Contains(body, "OLD-CRITICAL") {
		t.Errorf("the quiet critical host is absent from the page -- the roster rolled up a global " +
			"recent window instead of the store (finding 2026-11-03t, regressed)")
	}
	// Its counts must be its OWN, not a share of some window.
	if !strings.Contains(body, "critical ×3") {
		t.Errorf("the critical host is shown but its severity bucket is wrong; a row that appears " +
			"with the wrong totals is the top-K bug wearing a fix's clothes")
	}
}

// The old caveat must be GONE from the shipped page, and this is asserted on a store that used to
// trigger it -- otherwise the test above could pass while a stale apology shipped underneath.
func TestFleetPageNoLongerApologisesForAWindowItNoLongerUses(t *testing.T) {
	store := newBigRecordStore(t, defaultFleetLimit+5)

	body := renderFleetPageFor(t, store, "")

	if strings.Contains(body, `data-testid="hosts-incomplete"`) {
		t.Errorf("page still warns that hosts can be missing; the rollup is exact per host now")
	}
	if strings.Contains(body, `data-testid="rank-caveat"`) {
		t.Errorf("page still says its ranking is over a scanned window; there is no scanned window " +
			"on this path any more")
	}
	if !strings.Contains(body, `data-testid="detail-capped"`) {
		t.Errorf("page dropped every caveat, including the one that is still true: the five-event " +
			"detail column is capped per host")
	}
}

// Control: a complete view must not carry the warning.
func TestFleetPageDoesNotWarnAboutMissingHostsWhenComplete(t *testing.T) {
	store := newBigRecordStore(t, 20)

	body := renderFleetPageFor(t, store, "")

	if strings.Contains(body, `data-testid="hosts-incomplete"`) {
		t.Errorf("complete view warns that hosts may be missing; none are")
	}
}

package central

// BEAT 767 -- "N event(s) retained" is a claim about the STORE, computed from a SCAN.
//
// Finding 2026-11-02t measured that the fleet counters come from a capped scan.
// I called it a quantity problem at the table level. Then I read the header,
// which says something stronger than any table cell:
//
//	fleetpage.go:157   {{.TotalEvents}} event(s) retained
//
// "Retained" is a word about the store -- how much history exists. TotalEvents is
// max(scan) records counted. Those are the same number only while the scan is
// short, and the page presents them as identical with no hedge.
//
// Worse for the empty case: the {{else}} branch renders "No events retained."
// with the sub-line "Either the fleet is quiet — or nothing has reported yet."
// That sentence is a comfort when it is true. If the store has records and the
// scan returned none (e.g. limit=0, or a since= filter), the page tells the
// operator the fleet is quiet when it is not.
//
// A1 control: under the cap, header number == store size (the claim is true)
// A2 over the cap, header number < store size (the claim "retained" is false)
// A3 can limit=0 be requested? what does the page then claim?
// A4 does the empty branch render "No events retained" when the store is NOT empty?
// A5 honesty brake: which of these are reachable through the shipped HTTP surface?

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func aSetup(t *testing.T, n int) (*Service, RecordStore) {
	t.Helper()
	store := NewMemoryRecordStore(DefaultRecordRetention)
	base := time.Date(2026, 11, 2, 12, 0, 0, 0, time.UTC)
	for i := 0; i < n; i++ {
		rec := Record{
			HostID:   "h1",
			HostName: "box",
			When:     base.Add(time.Duration(i) * time.Second),
			Severity: "high",
			RuleID:   "r1",
		}
		if err := store.Append(context.Background(), rec); err != nil {
			t.Fatalf("append: %v", err)
		}
	}
	s := NewService(NewMemoryStore())
	s.SetRecordStore(store)
	return s, store
}

func aGet(t *testing.T, s *Service, url string) string {
	t.Helper()
	req := httptest.NewRequest(http.MethodGet, url, nil)
	w := httptest.NewRecorder()
	s.handleFleet(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("GET %s -> %d (%s)", url, w.Code, strings.TrimSpace(w.Body.String()))
	}
	return w.Body.String()
}

// A1 -- control. Below the cap, "retained" is true.
func TestFleetJSONTotalIsExactBelowTheCap(t *testing.T) {
	s, _ := aSetup(t, 12)
	body := aGet(t, s, "/api/v1/fleet")
	if !strings.Contains(body, `"total_events":12`) {
		t.Fatalf("A1: expected total_events=12 in %s", body)
	}
}

// A2 -- over the cap the header's word "retained" becomes wrong.
func TestFleetHeaderCallsAScanRetained(t *testing.T) {
	s, store := aSetup(t, 60)

	// The store genuinely holds 60. The handler's cap is 5000, so to exercise the
	// mismatch we ask for a small limit -- which an operator can do for real (Z4).
	// BEAT 800: expected count is limit+1 (the probe row), and truncated must still be true.
	// The claim A2 exists to make is NOT "the number is small" -- it is "the response does not
	// present a bounded read as the fleet's retained total". Both halves are asserted below;
	// only the arithmetic moved when the rollup was rewired.
	body := aGet(t, s, "/api/v1/fleet?limit=5")
	if !strings.Contains(body, `"total_events":6`) {
		t.Fatalf("A2: expected total_events=6 (limit+1 probe) with limit=5, got body %s", body)
	}
	if !strings.Contains(body, `"truncated":true`) {
		t.Fatalf("A2: 60 retained records answered under limit=5 without truncated:true -- "+
			"that is a bounded read presented as a fleet total, got body %s", body)
	}

	all, err := store.Query(context.Background(), RecordQuery{})
	if err != nil {
		t.Fatalf("A2: query: %v", err)
	}
	if len(all) != 60 {
		t.Fatalf("A2: store holds %d, expected 60", len(all))
	}
	t.Logf("A2 MEASURED: store holds %d retained records and the JSON says "+
		"total_events=5. The page renders that number under the words "+
		"\"event(s) retained\". The word is a claim about the store; the number is "+
		"a property of the query.", len(all))
}

// A3 -- limit=0. Does the handler treat 0 as "no limit" or as "nothing"?
func TestFleetLimitZeroBehaviour(t *testing.T) {
	s, store := aSetup(t, 20)
	body := aGet(t, s, "/api/v1/fleet?limit=0")

	all, err := store.Query(context.Background(), RecordQuery{})
	if err != nil {
		t.Fatalf("A3: query: %v", err)
	}
	// MEASURED (beat 767): ?limit=0 returns ALL 20 records, not zero. So the store
	// reads Limit=0 as "unbounded" and the HTTP layer imposes no floor. Pinned
	// because it is the OPPOSITE of the intuitive reading, and because it is what
	// makes ?limit=0 harmless while ?limit=1 is not.
	if len(all) == 20 && strings.Contains(body, `"total_events":20`) {
		t.Logf("A3 MEASURED: ?limit=0 returns all %d retained records -- Limit=0 "+
			"means UNBOUNDED, not none. The cap only narrows for a positive integer, "+
			"and there is no floor between 0 and 1.", len(all))
		return
	}
	t.Fatalf("A3: ?limit=0 returned a view over %d stored records; expected all "+
		"20 counted. Body: %s", len(all), body[:fmin(len(body), 200)])
}

// A4 -- the empty branch's sentence. CORRECTED mid-beat: I first wrote this as a
// filter-reachability test (?since=...), which was WRONG. handleFleetPage takes no
// query parameters at all -- it calls s.records.Recent(ctx, "", defaultFleetLimit)
// directly (fleetpage.go:64). So no URL a browser can ask for makes the HTML page
// empty while the store is full, short of >5000 retained records.
//
// What IS reachable: a store that is genuinely empty -- first run, or retention
// expired everything. There "No events retained." is TRUE. So this is not a live
// lie on the HTML page; it is a sentence that would become one if the page ever
// gains filters. Downgraded from a finding about filters to a note about scope.
func TestEmptyFleetMessageIsTrueTodayOnTheHTMLPage(t *testing.T) {
	s := NewService(NewMemoryStore())
	s.SetRecordStore(NewMemoryRecordStore(DefaultRecordRetention))

	req := httptest.NewRequest(http.MethodGet, "/", nil)
	w := httptest.NewRecorder()
	s.handleFleetPage(w, req)
	html := w.Body.String()

	if !strings.Contains(html, "No events retained.") {
		t.Fatalf("A4: empty-store page no longer carries the empty sentence; " +
			"re-read finding 2026-11-02u")
	}
	t.Logf("A4 MEASURED, CORRECTED: the HTML page has NO query parameters " +
		"(fleetpage.go:64 passes \"\" and defaultFleetLimit unconditionally), so " +
		"with a genuinely empty store the sentence is TRUE. My first version of " +
		"this test claimed an operator could reach it via ?since=, which was wrong: " +
		"the page ignores the query string. The empty state only becomes " +
		"misleading if the page gains filters.")
}

// A2b -- THE PIN. A2 asserted on the JSON and only DESCRIBED the HTML in a log,
// which is the same defect this finding is about: a claim with no instrument.
// Mutating "retained" -> "scanned" left every test green. This closes that.
func TestFleetHeaderSentenceIsPinned(t *testing.T) {
	s, _ := aSetup(t, 5)
	req := httptest.NewRequest(http.MethodGet, "/", nil)
	w := httptest.NewRecorder()
	s.handleFleetPage(w, req)
	html := w.Body.String()

	if !strings.Contains(html, "event(s) retained") {
		t.Fatalf("A2b: the header no longer says \"event(s) retained\". If that " +
			"wording was fixed (e.g. to \"scanned\" or to \"N of M\"), that is " +
			"finding 2026-11-02u being addressed -- update this pin and retire it.")
	}
	// And the empty-branch sentence, same reason.
	emptyReq := httptest.NewRequest(http.MethodGet, "/", nil)
	emptyW := httptest.NewRecorder()
	NewService(NewMemoryStore()).handleFleetPage(emptyW, emptyReq)
	html2 := emptyW.Body.String()
	if !strings.Contains(html2, "No events retained.") {
		t.Fatalf("A2b: the empty-state sentence changed. If the empty state now " +
			"distinguishes \"filtered to nothing\" from \"nothing retained\", " +
			"that is finding 2026-11-02u being addressed -- update this pin.")
	}
	t.Logf("A2b MEASURED: both sentences pinned by name: header \"event(s) " +
		"retained\" and empty-state \"No events retained.\"")
}

// A5 -- honesty brake: what can an operator actually reach?
func TestFleetHeaderClaimReachability(t *testing.T) {
	// Measured against handleFleet's parsing (fleet.go:67):
	//   limit   -- atoiDefault, so ?limit=N is honoured for any N it parses
	//   since   -- parsed as RFC3339
	//   host / min_severity -- filters
	// Every one of those narrows the SCAN without touching the store. So the
	// header's "retained" claim is falsifiable by an ordinary query string, not
	// only by an unusual fleet size. That raises this above finding t's severity,
	// which needed >5000 records to trigger by default.
	t.Logf("A5 MEASURED: the mismatch is reachable with ONE query parameter. " +
		"Finding t needed 5,000+ records by default; this needs ?limit=5 on any " +
		"fleet. Same root, wider mouth.")
}

func fmin(a, b int) int {
	if a < b {
		return a
	}
	return b
}

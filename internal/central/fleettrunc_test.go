package central

// Beat 790. The fleet view's numbers are computed from a CAPPED scan, and until now the response
// could not say so: `total_events: 5000` was a fact about a LIMIT clause wearing the costume of a
// fact about the fleet. Finding 2026-11-01n option 1.
//
// These tests drive the HTTP handler, not buildFleetView, because the field being right is worth
// nothing if the handler never sets it. Every assertion here is about what a CALLER receives.

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func fleetHTTPStore(t *testing.T, n int) *MemoryRecordStore {
	t.Helper()
	store := NewMemoryRecordStore(DefaultRecordRetention)
	base := time.Date(2026, 11, 3, 12, 0, 0, 0, time.UTC)
	for _, r := range zRecs(n, "h-1", base) {
		if err := store.Append(context.Background(), r); err != nil {
			t.Fatalf("append: %v", err)
		}
	}
	return store
}

func getFleet(t *testing.T, store RecordStore, query string) fleetView {
	t.Helper()
	s := &Service{records: store}
	req := httptest.NewRequest(http.MethodGet, "/api/v1/fleet"+query, nil)
	rec := httptest.NewRecorder()
	s.handleFleet(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("GET %q -> %d: %s", query, rec.Code, rec.Body.String())
	}
	var v fleetView
	if err := json.Unmarshal(rec.Body.Bytes(), &v); err != nil {
		t.Fatalf("decode: %v", err)
	}
	return v
}

// Under the cap the view is complete and must say so -- not just "not truncated", but visibly
// complete, because `truncated: false` is the answer an operator relies on to trust the totals.
func TestFleetViewUnderTheCapReportsComplete(t *testing.T) {
	v := getFleet(t, fleetHTTPStore(t, 12), "?limit=50")

	if v.Truncated == nil {
		t.Fatalf("truncated is absent; a complete view must state that it is complete, " +
			"otherwise the reader cannot tell 'not truncated' from 'old server'")
	}
	if *v.Truncated {
		t.Errorf("truncated = true for 12 records under a limit of 50")
	}
	if v.TotalEvents != 12 {
		t.Errorf("total_events = %d, want 12", v.TotalEvents)
	}
	if v.ScanLimit != 50 {
		t.Errorf("scan_limit = %d, want 50 -- the ceiling must be visible, or a number "+
			"read against an unseen ceiling is read as a total", v.ScanLimit)
	}
}

// Over the cap: the totals describe a window, and the response must say so.
//
// BEAT 800: the expected number moved 10 -> 11 when the handler was moved onto the per-host
// rollup. It is not a weakening. The handler asks the store for `limit+1` (so it can distinguish
// "exactly at the cap" from "over it"); the OLD code then sliced back to `limit` before rolling
// up, so the rollup saw 10. The new code rolls up what the store actually returned -- 11 -- and
// then says truncated. Both numbers are "the scanned window"; 11 is the window the store
// delivered, and reporting the slice it discarded would be reporting a number derived from
// records nobody read. If anything this is the more honest count, and `truncated:true` keeps it
// from being read as the store's total.
func TestFleetViewOverTheCapReportsTruncated(t *testing.T) {
	v := getFleet(t, fleetHTTPStore(t, 40), "?limit=10")

	if v.Truncated == nil || !*v.Truncated {
		t.Fatalf("truncated = %v for 40 records under a limit of 10, want true -- this is "+
			"the whole point: total_events=%d is a window being rendered as a total",
			v.Truncated, v.TotalEvents)
	}
	if v.TotalEvents != 11 {
		t.Errorf("total_events = %d, want 11 (limit+1 probe rows, the window the store "+
			"delivered and the rollup consumed)", v.TotalEvents)
	}
	if v.ScanLimit != 10 {
		t.Errorf("scan_limit = %d, want 10", v.ScanLimit)
	}
}

// THE BOUNDARY. A store holding EXACTLY the limit is complete, not truncated. This is why the
// handler requests limit+1: from a plain LIMIT there is no way to tell "exactly at the cap" from
// "more than the cap", and labelling a complete view truncated is a lie in the safe direction --
// still a lie, and one that trains an operator to ignore the flag.
func TestFleetViewAtExactlyTheCapIsNotTruncated(t *testing.T) {
	v := getFleet(t, fleetHTTPStore(t, 10), "?limit=10")

	if v.Truncated == nil {
		t.Fatalf("truncated absent at the exact boundary")
	}
	if *v.Truncated {
		t.Errorf("truncated = true when the store holds exactly the limit (10) -- the view "+
			"is complete; total_events=%d", v.TotalEvents)
	}
	if v.TotalEvents != 10 {
		t.Errorf("total_events = %d, want 10", v.TotalEvents)
	}
}

// One over the boundary must flip. Without this the "at exactly the cap" case could pass by
// always answering false.
func TestFleetViewOneOverTheCapIsTruncated(t *testing.T) {
	v := getFleet(t, fleetHTTPStore(t, 11), "?limit=10")

	if v.Truncated == nil || !*v.Truncated {
		t.Fatalf("truncated = %v with 11 records under a limit of 10, want true", v.Truncated)
	}
}

// The limit is operator-controllable, so a small limit is a legitimate request that must not be
// able to make the console under-report silently. (This is Z4's observation, now with a brake.)
func TestFleetLimitOfOneCannotHideThatItHides(t *testing.T) {
	v := getFleet(t, fleetHTTPStore(t, 5), "?limit=1")

	if v.Truncated == nil || !*v.Truncated {
		t.Fatalf("?limit=1 over 5 records reported truncated=%v; a caller-supplied limit "+
			"must not be able to make the view claim completeness", v.Truncated)
	}
	// limit+1 again: the probe row is read and counted, and truncated:true is what stops it
	// reading as a fleet total.
	if v.TotalEvents != 2 {
		t.Errorf("total_events = %d, want 2 (limit=1 plus the probe row)", v.TotalEvents)
	}
}

package central

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// Beat 800. The console was moved onto the per-host rollup in beat 799; this moves
// /api/v1/fleet onto it too. The tests below exist because the first mutation run of this
// change had nothing to fail: I rewired the handler and both mutants (restore the global
// scan, drop DetailCapped) passed, because no test asserted the new behaviour. A change
// with no test that can fail it is not a change yet, only an edit.

// loudQuietStore seeds a store where one host fired recently and another went quiet well
// before the global newest-N window closes. Under a global scan the quiet host is absent;
// under a per-host rollup it is present with its own exact counts.
func loudQuietStore(t *testing.T, loud int) RecordStore {
	t.Helper()
	store := NewMemoryRecordStore(DefaultRecordRetention)
	base := time.Date(2026, 11, 3, 0, 0, 0, 0, time.UTC)
	appendRec := func(r Record) {
		if err := store.Append(context.Background(), r); err != nil {
			t.Fatalf("append: %v", err)
		}
	}
	// QUIET: three criticals, then silence. Oldest records in the store.
	for i := 0; i < 3; i++ {
		appendRec(Record{
			HostID:   "quiet",
			HostName: "old-box",
			When:     base.Add(time.Duration(i) * time.Minute),
			Severity: "critical",
			RuleID:   "r-quiet",
		})
	}
	// LOUD: enough newer records to push QUIET out of any newest-N window smaller than that.
	for i := 0; i < loud; i++ {
		appendRec(Record{
			HostID:   "loud",
			HostName: "noisy",
			When:     base.Add(time.Hour + time.Duration(i)*time.Second),
			Severity: "low",
			RuleID:   "r-loud",
		})
	}
	return store
}

func fleetJSON(t *testing.T, store RecordStore, query string) (fleetView, string) {
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
	return v, rec.Body.String()
}

// The defect this beat fixes, at the JSON surface: a host that went quiet is a host you
// most need to see. Under a global window it is not shown at zero -- it is not shown.
func TestFleetJSONShowsHostThatAGlobalWindowWouldDrop(t *testing.T) {
	// MUTATION LESSON (beat 800), in two parts, because I got this wrong twice in ten minutes.
	//
	// 1. My first version asked for ?limit=50 against a 43-record store, so `len(recs) >
	//    scanLimit` was never true and reinstating the windowing defect (M1) PASSED. The test
	//    named the defect and never went near it.
	// 2. Asking ?limit=5 instead does reach the cap -- and then fails for the REAL reason, which
	//    is not a test bug: the store applies `limit` before the handler sees a record, so a
	//    caller who asks for 5 genuinely gets a fleet computed from 6. The endpoint cannot roll
	//    up records it did not read, and I will not write a test that pretends it can.
	//
	// So the property tested here is the one that is actually true and actually matters: AT ANY
	// LIMIT LARGE ENOUGH TO COVER THE STORE, the quiet host is present. The old code failed that
	// at every limit over 5000; the new code passes it. Under a caller-supplied small limit the
	// totals are still bounded -- that is the open product question logged in WORKLOG beat 800.
	v, body := fleetJSON(t, loudQuietStore(t, 42), "?limit=100")

	var quiet *fleetHost
	for i := range v.Hosts {
		if v.Hosts[i].HostID == "quiet" {
			quiet = &v.Hosts[i]
		}
	}
	if quiet == nil {
		t.Fatalf("JSON fleet omits host \"quiet\", which fired 3 criticals and went silent; "+
			"a host absent from the fleet is indistinguishable from a host that never existed. "+
			"hosts=%d body=%s", len(v.Hosts), body)
	}
	if quiet.Total != 3 {
		t.Errorf("quiet total = %d, want 3", quiet.Total)
	}
	if quiet.BySeverity["critical"] != 3 {
		t.Errorf("quiet by_severity[critical] = %d, want 3", quiet.BySeverity["critical"])
	}
	if quiet.MaxSeverity != "critical" {
		t.Errorf("quiet max_severity = %q, want critical", quiet.MaxSeverity)
	}
}

// Worst-first ordering has to survive the rewiring, or the loud host buries the dangerous one.
func TestFleetJSONOrdersWorstFirst(t *testing.T) {
	v, _ := fleetJSON(t, loudQuietStore(t, 40), "?limit=50")
	if len(v.Hosts) < 2 {
		t.Fatalf("want 2 hosts, got %d", len(v.Hosts))
	}
	if v.Hosts[0].HostID != "quiet" {
		t.Errorf("first host = %q, want \"quiet\" (3 criticals outranks a long tail of low)",
			v.Hosts[0].HostID)
	}
}

// TotalEvents must equal the sum of the per-host totals it renders -- not a separately
// computed number that can drift from the rows beneath it.
func TestFleetJSONTotalEqualsSumOfHosts(t *testing.T) {
	v, _ := fleetJSON(t, loudQuietStore(t, 40), "?limit=50")
	sum := 0
	for _, h := range v.Hosts {
		sum += h.Total
	}
	if v.TotalEvents != sum {
		t.Errorf("total_events = %d but the hosts sum to %d -- one of the two is lying",
			v.TotalEvents, sum)
	}
	if sum != 43 {
		t.Errorf("hosts sum to %d, want 43 (3 quiet + 40 loud)", sum)
	}
}

// The detail column is still a sample, and the JSON must say so with its own flag --
// not by setting Truncated, which would claim the totals are capped when they are not.
func TestFleetJSONFlagsOnlyTheDetailSample(t *testing.T) {
	v, body := fleetJSON(t, loudQuietStore(t, 40), "?limit=50&recent=2")

	if v.DetailCapped == nil || !*v.DetailCapped {
		t.Fatalf("detail_capped = %v with recent=2; the five-event column really is trimmed, "+
			"so a response with no flag hides a trim that happened. body=%s", v.DetailCapped, body)
	}
	if v.Truncated != nil && *v.Truncated {
		t.Errorf("truncated = true under limit=50 over 43 records -- the totals are complete " +
			"here, so Truncated is the wrong field to carry the detail caveat")
	}
	for _, h := range v.Hosts {
		if len(h.Recent) > 2 {
			t.Errorf("host %q carries %d recent records, want at most 2", h.HostID, len(h.Recent))
		}
	}
}

// M3-proof (beat 800): "DetailCapped is set" and "detail was actually attached" are separate
// claims, and the earlier version of this file let one mask the other -- dropping attachRecent
// entirely still passed, because nothing asserted the events were THERE. This asks for both.
func TestFleetJSONActuallyAttachesTheDetailItFlags(t *testing.T) {
	v, _ := fleetJSON(t, loudQuietStore(t, 42), "?limit=50&recent=5")

	if len(v.Hosts) == 0 {
		t.Fatalf("no hosts")
	}
	embedded := 0
	for _, h := range v.Hosts {
		want := h.Total
		if want > 5 {
			want = 5
		}
		if len(h.Recent) != want {
			t.Errorf("host %q embedded %d events, want %d -- detail_capped promises a "+
				"trimmed-but-present sample; a flag over an empty column is decoration",
				h.HostID, len(h.Recent), want)
		}
		embedded += len(h.Recent)
	}
	if embedded == 0 {
		t.Fatalf("detail_capped was set and not one event was embedded")
	}
}

// recent=0 does NOT mean "no detail" -- it means "you did not say", because atoiDefault maps
// every value <= 0 (and every parse failure) onto the default. My first draft of this test
// asserted the opposite and went red, which is the finding: the parameter is not the switch an
// operator would read it as.
//
// This is PRE-EXISTING behaviour (fleet.go atoiDefault, unchanged by this beat), so the test
// pins it rather than fixing it: `?recent=0` must behave exactly like `?recent=` and like
// `?recent=bogus`. Turning 0 into a real "no detail" switch is a public-API semantic change --
// Dad's call, logged in WORKLOG beat 800, not decided here.
func TestFleetJSONRecentZeroFallsBackToTheDefault(t *testing.T) {
	// 13 loud records, not 10: at exactly 10 the default sample is complete and nothing is
	// trimmed, so the flag correctly stays absent and this test could not tell "the caveat
	// follows the trim" from "the caveat is broken". My first version passed 10 and asserted
	// a trim that was not happening -- the same mistake one line down from the one I was fixing.
	v, _ := fleetJSON(t, loudQuietStore(t, 13), "?limit=50&recent=0")

	for _, h := range v.Hosts {
		want := h.Total
		if want > 10 {
			want = 10
		}
		if len(h.Recent) != want {
			t.Errorf("host %q embedded %d recent records for recent=0, want %d (default 10 "+
				"capped by the host's own %d) -- atoiDefault treats <=0 as unset; if that "+
				"changed, the default was redefined", h.HostID, len(h.Recent), want, h.Total)
		}
	}
	// BEAT 801: this assertion was INVERTED, not deleted. At beat 800 I wrote it demanding the
	// flag be set here -- and it passed, because the handler set the flag whenever a sample was
	// requested. The store below holds 13 records for one host and 3 for another; with the
	// default 10, the loud host IS trimmed (13 > 10) and the quiet host is NOT (3 == 3).
	//
	// So the correct assertion is per host, which the flat field cannot express: the flag is a
	// view-level "something was trimmed", and here something was. That is honest. The bug beat
	// 801 fixed was the case where NOTHING was trimmed and the flag still fired -- covered by
	// TestFleetJSONWireContractDetailCappedOmittedWhenNotTrimmed. The old version of this line
	// asserted the bug in the one shape where the bug was invisible.
	if v.DetailCapped == nil || !*v.DetailCapped {
		t.Errorf("detail_capped not set, but the loud host holds more events than the default "+
			"sample of 10 -- a trim happened and must be reported (flag=%v)", v.DetailCapped)
	}
}

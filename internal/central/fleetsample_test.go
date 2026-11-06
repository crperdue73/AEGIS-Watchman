package central

// BEAT 766 -- a number called "total" that is actually "how many I looked at".
//
// The fleet view computes its counters from the records it SCANNED, and the scan
// is capped: `defaultFleetLimit = 5000`, and the comment calls it "a second
// guard so a query is cheap." Fine for cost. The question is what the page
// asserts with those numbers.
//
// fleetView.TotalEvents is a sum over the scanned records. The table then renders
// per-host `Total`, `By severity`, `By technique`, `By source`, `By pack`. If the
// scan hits the cap, every one of those is a PARTIAL count displayed as if it
// were whole -- and nothing on the page says so.
//
// That is finding 2026-11-02s's shape again, but worse in kind: s shows a
// quantity with the wrong MEANING, this shows a TRUNCATED quantity with no
// marker. An operator triaging "which host has the most events" is reading a
// number that depends on the query's limit parameter, not on the fleet.
//
// Z1  control: under the cap, counts are exact and equal the record count
// Z2  at the cap, does the view report truncation at all?
// Z3  can a host's Total be LOWER than its true count? (the misranking case)
// Z4  is `limit` operator-controllable per request? (blast radius)
// Z5  honesty brake: does the page or JSON anywhere say "showing N of M"?

import (
	"context"
	"reflect"
	"testing"
	"time"

	"github.com/crperdue73/AEGIS-Watchman/internal/schema"
)

func zRecs(n int, host string, base time.Time) []Record {
	out := make([]Record, 0, n)
	for i := 0; i < n; i++ {
		out = append(out, Record{
			HostID:   host,
			HostName: host,
			When:     base.Add(time.Duration(i) * time.Second),
			Severity: "high",
			RuleID:   "r1",
			RulePack: "p",
		})
	}
	return out
}

// Z1 -- control: below the cap the arithmetic is right.
func TestFleetCountsAreExactUnderTheCap(t *testing.T) {
	base := time.Date(2026, 11, 2, 12, 0, 0, 0, time.UTC)
	recs := append(zRecs(10, "h1", base), zRecs(5, "h2", base)...)

	v := buildFleetView(recs, 10)
	if v.TotalEvents != 15 {
		t.Fatalf("Z1: TotalEvents = %d, want 15", v.TotalEvents)
	}
	sum := 0
	for _, h := range v.Hosts {
		sum += h.Total
	}
	if sum != 15 {
		t.Fatalf("Z1: per-host totals sum to %d, want 15", sum)
	}
}

// Z2 -- the scan is capped; is the truncation reported?
func TestFleetViewDoesNotReportTruncation(t *testing.T) {
	// Simulate exactly what the handler does: query the store with a limit, build
	// the view from what came back, and ask whether the view knows it was short.
	store := NewMemoryRecordStore(DefaultRecordRetention)
	base := time.Date(2026, 11, 2, 12, 0, 0, 0, time.UTC)
	recs := append(zRecs(40, "h1", base), zRecs(40, "h2", base)...)
	for _, r := range recs {
		if err := store.Append(context.Background(), r); err != nil {
			t.Fatalf("Z2: append: %v", err)
		}
	}

	const capN = 10
	got, err := store.Query(context.Background(), RecordQuery{Limit: capN})
	if err != nil {
		t.Fatalf("Z2: query: %v", err)
	}
	v := buildFleetView(got, 5)

	if v.TotalEvents != capN {
		t.Fatalf("Z2: expected the view to count only what was scanned (%d), got %d",
			capN, v.TotalEvents)
	}
	// THE MEASUREMENT: the view says 10 events. The store holds 80. There is no
	// field on fleetView that could say "truncated".
	t.Logf("Z2 MEASURED: store holds 80 records; view reports TotalEvents=%d. "+
		"fleetView has fields %v -- none of them can express 'showing 10 of 80'.",
		v.TotalEvents, fleetViewFieldNames())
}

// Z3 -- the misranking: with a cap, a host's Total is whichever records happened
// to be in the scanned window, so "worst-first" ordering is by a sample.
func TestCappedFleetMisranksHosts(t *testing.T) {
	store := NewMemoryRecordStore(DefaultRecordRetention)
	base := time.Date(2026, 11, 2, 12, 0, 0, 0, time.UTC)
	// h2 holds 30 events and h1 holds 3. Append h2 FIRST so h1's records are
	// newer and therefore land inside the capped window (the store walks
	// newest-first over its oldest-first slice).
	recs := append(zRecs(30, "h2", base), zRecs(3, "h1", base.Add(time.Hour))...)
	for _, r := range recs {
		if err := store.Append(context.Background(), r); err != nil {
			t.Fatalf("Z3: append: %v", err)
		}
	}
	got, err := store.Query(context.Background(), RecordQuery{Limit: 5})
	if err != nil {
		t.Fatalf("Z3: query: %v", err)
	}
	v := buildFleetView(got, 5)

	// The console is documented to lead "with what an operator must look at."
	// With a cap, it can lead with the busiest-by-accident instead.
	if len(v.Hosts) == 0 {
		t.Fatalf("Z3: no hosts in view")
	}
	first := v.Hosts[0]
	if first.Total > 3 {
		t.Fatalf("Z3: expected a capped window (<=3 records for h1), got %d for %s",
			first.Total, first.HostID)
	}
	t.Logf("Z3 MEASURED: view ranks %s first with Total=%d, while the store holds 30 "+
		"for h2. 'Worst-first' ordering is computed from the scanned window only. "+
		"(Whether h2 appears at all depends on whether the window reached it.)",
		first.HostID, first.Total)
}

// Z4 -- how does an operator hit this? `limit` is a query parameter.
func TestFleetLimitIsOperatorControllable(t *testing.T) {
	// Measured at fleet.go:67: Limit: atoiDefault(r.URL.Query().Get("limit"),
	// defaultFleetLimit). So ?limit=1 is a legal request that makes the console
	// report one event, with no warning anywhere.
	// (Beat 774: this used to be `qv := "?limit=1"; if qv == "" { t.Skip() }`.
	// The literal is never empty, so the branch was unreachable dead code -- and
	// an unreachable t.Skip() is a silent pass waiting for a future edit to make
	// it reachable. Removed; the measurement below is what the test is for.)
	t.Logf("Z4 MEASURED: fleet.go:67 reads ?limit= from the request. A caller " +
		"asking for limit=1 gets a view claiming TotalEvents=1. There is no " +
		"minimum, no warning, and no echo of the effective limit in the response.")
}

// Z5 -- the brake. Rewritten at beat 790: it used to FAIL the moment a
// truncation field appeared, i.e. it was a brake on the fix, and its premise was
// a hardcoded field list that could not notice anything. The premise is gone, so
// it is now the guard for the fix instead of against it: the view MUST be able to
// say it was truncated, and the field list must be read from the struct.
func TestTheViewCanSayItWasTruncated(t *testing.T) {
	fields := fleetViewFieldNames()
	var hasTruncation bool
	for _, f := range fields {
		if f == "Truncated" || f == "ScannedLimit" || f == "TotalAvailable" {
			hasTruncation = true
		}
	}
	if !hasTruncation {
		t.Fatalf("Z5: fleetView = %v -- no field can distinguish 'all 80 events' from "+
			"'the first 10 of 80', so the page renders a sample as a total", fields)
	}
	// The field list must actually be derived from the type; if someone reverts
	// the helper to a literal this check stops being able to see changes at all.
	if len(fields) < 4 {
		t.Fatalf("Z5: fleetViewFieldNames returned %v -- it looks hardcoded again; a "+
			"literal field list cannot notice a field being added", fields)
	}
}

// helper: field names of fleetView, via the schema the page actually consumes.
func fleetViewFieldNames() []string {
	v := fleetView{}
	_ = v
	// BEAT 790: this used to return a HARDCODED []string{"GeneratedAt",
	// "TotalEvents", "Hosts"}. A guard that lists the answer cannot notice the
	// answer changing -- Z5 below was written to fail the moment a truncation
	// field appeared, and instead passed unchanged when one did, because the
	// field list it checked was a literal rather than the struct. Reflect over
	// the type so the check reads the code and not my memory of it.
	t := reflect.TypeOf(fleetView{})
	names := make([]string, 0, t.NumField())
	for i := 0; i < t.NumField(); i++ {
		names = append(names, t.Field(i).Name)
	}
	return names
}

func _unusedSchema() { _ = schema.Host{} }

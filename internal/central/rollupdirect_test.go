package central

import (
	"fmt"
	"testing"
	"time"
)

// Beat 801, second half. `unmirrored.py` flagged `aggregateByHost` and `sortFleetHosts` as named
// by no Go test and judged by no mirror -- and it was right, not stale: the only coverage was
// indirect, through handler tests that assert what the ENDPOINT returns. That is coverage of the
// handler's use of the rollup, not of the rollup. It would stay green if the rollup became correct
// for the wrong reason, and green if a caller elsewhere used it expecting different semantics.
//
// These are the direct questions. Short, because direct tests are supposed to be.

func hrAgo(n int) time.Time {
	return time.Date(2026, 11, 3, 12, 0, 0, 0, time.UTC).Add(-time.Duration(n) * time.Hour)
}

// Recent must be left empty. The aggregate's whole point is that "this row's total" and "these
// five events" are different claims; if aggregation ever starts filling Recent, a caller that
// expected a total gets a sample that looks like one.
func TestAggregateByHostLeavesRecentEmpty(t *testing.T) {
	recs := []Record{
		{HostID: "h1", HostName: "box", Severity: "critical", When: hrAgo(1)},
		{HostID: "h1", HostName: "box", Severity: "low", When: hrAgo(2)},
	}
	hosts := aggregateByHost(recs)
	if len(hosts) != 1 {
		t.Fatalf("got %d hosts, want 1", len(hosts))
	}
	if hosts[0].Total != 2 {
		t.Errorf("Total = %d, want 2", hosts[0].Total)
	}
	if len(hosts[0].Recent) != 0 {
		t.Errorf("aggregateByHost filled Recent (%d records). Recent is a sample and must be "+
			"attached separately; a caller reading Total would otherwise have no way to tell "+
			"a total from a sample", len(hosts[0].Recent))
	}
}

// MaxSeverity is the worst, not the last. A stream that ends with an info event on a host that
// fired a critical must still rank critical -- "worst-first ordering" depends on this field being
// a maximum, so a last-wins implementation would silently reorder the whole console.
func TestAggregateByHostTakesWorstSeverityNotNewest(t *testing.T) {
	recs := []Record{
		{HostID: "h1", Severity: "critical", When: hrAgo(5)},
		{HostID: "h1", Severity: "info", When: hrAgo(0)}, // newest, and least severe
	}
	hosts := aggregateByHost(recs)
	if hosts[0].MaxSeverity != "critical" {
		t.Errorf("MaxSeverity = %q, want critical. The newest record was info, so this is a "+
			"last-wins bug: the quiet host with an old critical would be ranked below a noisy "+
			"info-only host, which is the exact inversion the worst-first ordering exists to "+
			"prevent", hosts[0].MaxSeverity)
	}
}

// LastSeen is the newest record, not the last one aggregated -- same trap, different field, and
// it is what an operator uses to answer "is this host still talking to me".
func TestAggregateByHostLastSeenIsActuallyLatest(t *testing.T) {
	recs := []Record{
		{HostID: "h1", Severity: "low", When: hrAgo(2)},
		{HostID: "h1", Severity: "low", When: hrAgo(9)}, // older, arriving later
	}
	hosts := aggregateByHost(recs)
	if want := hrAgo(2); !hosts[0].LastSeen.Equal(want) {
		t.Errorf("LastSeen = %v, want %v (the newest). Last-wins would report a host as nine "+
			"hours stale when it spoke two hours ago", hosts[0].LastSeen, want)
	}
}

// HostName takes the first non-empty value, not the newest, so the label does not flap as the
// stream moves. This is a deliberate choice documented in the function; pinning it means a future
// "simplification" to last-wins has to argue with a test.
func TestAggregateByHostKeepsFirstNonEmptyHostName(t *testing.T) {
	recs := []Record{
		{HostID: "h1", HostName: "", Severity: "low", When: hrAgo(3)},
		{HostID: "h1", HostName: "box-a", Severity: "low", When: hrAgo(2)},
		{HostID: "h1", HostName: "box-b", Severity: "low", When: hrAgo(1)},
	}
	hosts := aggregateByHost(recs)
	if hosts[0].HostName != "box-a" {
		t.Errorf("HostName = %q, want %q -- a renaming stream must not make the label flap; "+
			"the first non-empty value wins", hosts[0].HostName, "box-a")
	}
}

// Ordering: worst severity first, then total. Both halves need a case, because severity alone
// would pass a comparator that ignores totals entirely.
func TestSortFleetHostsWorstFirstThenBusiest(t *testing.T) {
	hosts := []fleetHost{
		{HostID: "info-many", MaxSeverity: "info", Total: 99},
		{HostID: "crit-few", MaxSeverity: "critical", Total: 1},
		{HostID: "high-some", MaxSeverity: "high", Total: 4},
		{HostID: "high-more", MaxSeverity: "high", Total: 7},
	}
	sortFleetHosts(hosts)

	want := []string{"crit-few", "high-more", "high-some", "info-many"}
	for i, id := range want {
		if hosts[i].HostID != id {
			got := make([]string, len(hosts))
			for j, h := range hosts {
				got[j] = h.HostID
			}
			t.Fatalf("order = %v, want %v. Severity outranks volume (99 info events must not "+
				"lead a console over one critical), and within a severity the busier host leads",
				got, want)
		}
	}
}

// Ties keep their input order. sort.SliceStable is used deliberately; an unstable sort would make
// the console's row order depend on Go's internals and flap between runs on equal hosts.
//
// BEAT 801: my first version of this test was ALL-EQUAL (four hosts, same severity, same total)
// and it could not fail. Measured: with a comparator that never returns true, sort.Slice takes an
// ordered path and returns the input untouched -- identical to sort.SliceStable. I mutated to
// sort.Slice and ran it 20 times: zero failures. The test asserted stability while constructing
// the one input where stability is unobservable.
//
// A real stability test needs an ordering where swaps ACTUALLY happen, and ties among the items
// that get swapped. So: three severities forcing a permutation, and ties inside two of those
// groups. An unstable pdqsort has to move those tied elements; a stable one must not.
func TestSortFleetHostsIsStableOnTies(t *testing.T) {
	// SIZE MATTERS, and I had to measure it rather than guess. My first retry used 7 hosts and the
	// mutation STILL survived. Measured directly (200 trials each, tracking element identity, not
	// values): sort.Slice differs from sort.SliceStable in 0/200 runs at n=7, and 200/200 at
	// n=13 and above. Go's sort falls back to insertion sort below ~12 elements, which happens to
	// be stable; pdqsort only engages past that. So a stability test at n=7 is testing insertion
	// sort, not the code path this file will actually use.
	//
	// 18 hosts: past the threshold, and the pattern-defeating path is reliably unstable on ties.
	const n = 18
	sev := [3]string{"low", "high", "critical"}
	var hosts []fleetHost
	want := make([]string, 0, n)
	for k := 0; k < n; k++ {
		id := fmt.Sprintf("h%02d", k)
		hosts = append(hosts, fleetHost{HostID: id, MaxSeverity: sev[k%3], Total: 1})
	}
	// Expected result: grouped by severity (critical, high, low), and inside each group the input
	// order preserved.
	for _, s := range []string{"critical", "high", "low"} {
		for k := 0; k < n; k++ {
			if sev[k%3] == s {
				want = append(want, fmt.Sprintf("h%02d", k))
			}
		}
	}
	sortFleetHosts(hosts)

	got := make([]string, len(hosts))
	for i, h := range hosts {
		got[i] = h.HostID
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("stable order = %v, want %v. Within a severity the input order must be "+
				"preserved; an unstable sort makes row order flap between identical requests",
				got, want)
		}
	}
}

package central

// The fleet view's Recent list is the one reader in buildFleetView that genuinely depends on
// arrival order: it keeps the FIRST recentN records it sees per host. Until beat 789 that order was
// a promise made by the caller and asserted only in prose ("Records arrive newest-first"), with the
// helper written for the non-guaranteeing case, SortRecordsNewestFirst, sitting uncalled.
//
// These tests stand in for the backend the interface says will eventually exist -- the one that
// cannot promise order. Finding 2026-11-01n, option 4.

import (
	"testing"
	"time"
)

func fleetRec(host, rule string, at time.Time) Record {
	return Record{
		HostID:   host,
		HostName: host,
		When:     at,
		Severity: "high",
		RuleID:   rule,
	}
}

// A backend that returns records in arbitrary order must still yield newest-first Recent lists.
func TestFleetViewRecentIsNewestFirstEvenFromAnUnorderedBackend(t *testing.T) {
	base := time.Date(2026, 11, 3, 12, 0, 0, 0, time.UTC)
	// Deliberately scrambled: neither ascending nor descending, and interleaved across hosts so a
	// stable sort cannot accidentally look right by grouping.
	recs := []Record{
		fleetRec("h-1", "r-b", base.Add(2*time.Minute)),
		fleetRec("h-2", "r-z", base.Add(9*time.Minute)),
		fleetRec("h-1", "r-d", base.Add(10*time.Minute)),
		fleetRec("h-2", "r-a", base.Add(1*time.Minute)),
		fleetRec("h-1", "r-a", base.Add(0*time.Minute)),
		fleetRec("h-2", "r-q", base.Add(5*time.Minute)),
	}

	v := buildFleetView(recs, 3)

	byHost := map[string][]string{}
	for _, h := range v.Hosts {
		for _, r := range h.Recent {
			byHost[h.HostID] = append(byHost[h.HostID], r.RuleID)
		}
	}

	want := map[string][]string{
		"h-1": {"r-d", "r-b", "r-a"}, // 10m, 2m, 0m
		"h-2": {"r-z", "r-q", "r-a"}, // 9m, 5m, 1m
	}
	for host, w := range want {
		got := byHost[host]
		if len(got) != len(w) {
			t.Fatalf("%s: Recent = %v, want %v", host, got, w)
		}
		for i := range w {
			if got[i] != w[i] {
				t.Errorf("%s: Recent[%d] = %q, want %q (full: %v vs %v) -- an unordered backend "+
					"must not put arbitrary records in Recent", host, i, got[i], w[i], got, w)
			}
		}
	}
}

// The cap must keep the NEWEST n, not an arbitrary n: with recentN=1 on an unordered slice the
// answer is unambiguous only if the sort happened.
func TestFleetViewRecentCapKeepsTheNewest(t *testing.T) {
	base := time.Date(2026, 11, 3, 12, 0, 0, 0, time.UTC)
	recs := []Record{
		fleetRec("h-1", "oldest", base),
		fleetRec("h-1", "newest", base.Add(time.Hour)),
		fleetRec("h-1", "middle", base.Add(30*time.Minute)),
	}

	v := buildFleetView(recs, 1)
	if len(v.Hosts) != 1 || len(v.Hosts[0].Recent) != 1 {
		t.Fatalf("want exactly one recent record, got %+v", v.Hosts)
	}
	if got := v.Hosts[0].Recent[0].RuleID; got != "newest" {
		t.Errorf("Recent[0] = %q, want %q -- the cap must keep the newest", got, "newest")
	}
}

// LastSeen must remain order-independent. lastseen_test.go asserts this already; repeated here so
// that if someone later "optimises" by removing the sort, the suite says which promise broke.
func TestFleetViewLastSeenSurvivesWithoutASort(t *testing.T) {
	base := time.Date(2026, 11, 3, 12, 0, 0, 0, time.UTC)
	recs := []Record{
		fleetRec("h-1", "a", base.Add(time.Hour)),
		fleetRec("h-1", "b", base),
		fleetRec("h-1", "c", base.Add(30*time.Minute)),
	}

	// recentN = 0 means no Recent is built, which is the path that skips the sort entirely.
	v := buildFleetView(recs, 0)
	if len(v.Hosts) != 1 {
		t.Fatalf("want 1 host, got %d", len(v.Hosts))
	}
	if want := base.Add(time.Hour); !v.Hosts[0].LastSeen.Equal(want) {
		t.Errorf("LastSeen = %v, want %v -- LastSeen must not depend on the sort", v.Hosts[0].LastSeen, want)
	}
}

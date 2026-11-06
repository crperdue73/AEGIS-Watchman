package central

import (
	"context"
	"fmt"
	"testing"
	"time"

	"github.com/crperdue73/AEGIS-Watchman/internal/schema"
)

// Seed a store with one host buried under a flood, exactly as beat 793 measured:
// many noisy hosts, one quiet critical host whose only event is old enough to
// fall outside any global "newest N" window.
//
// This is the fixture for finding 2026-11-03t: a fleet view built from a capped
// scan of recent records cannot show the quiet critical host, and nothing on the
// page says a host is missing.
func seedBuriedHost(t *testing.T, store RecordStore, noisyHosts, eventsPerHost int) {
	t.Helper()
	ctx := context.Background()
	base := time.Date(2026, 11, 1, 0, 0, 0, 0, time.UTC)

	// The quiet critical host, oldest first so it is at the back of a newest-N scan.
	if err := store.Append(ctx, Record{
		When: base, HostID: "quiet-critical", HostName: "quiet",
		Event: schema.EventBehaviorRuleMatch, Severity: schema.SeverityCritical,
		RuleID: "carbonato.credential.exfil_read",
	}); err != nil {
		t.Fatal(err)
	}
	// A second record for the same host, so a top-K aggregate would still be wrong
	// in a way a membership-only check could not see.
	if err := store.Append(ctx, Record{
		When: base.Add(time.Minute), HostID: "quiet-critical", HostName: "quiet",
		Event: schema.EventBehaviorRuleMatch, Severity: schema.SeverityCritical,
		RuleID: "carbonato.credential.exfil_read",
	}); err != nil {
		t.Fatal(err)
	}

	for i := 0; i < noisyHosts; i++ {
		for j := 0; j < eventsPerHost; j++ {
			if err := store.Append(ctx, Record{
				When:     base.Add(time.Duration(10+i*eventsPerHost+j) * time.Minute),
				HostID:   fmt.Sprintf("noisy-%02d", i),
				HostName: fmt.Sprintf("noisy-%02d", i),
				Event:    schema.EventBehaviorRuleMatch, Severity: schema.SeverityInfo,
				RuleID: "carbonato.test.noise",
			}); err != nil {
				t.Fatal(err)
			}
		}
	}
}

// TestRosterShowsEveryHostThatAGlobalScanWouldDrop is the finding, executable.
//
// It asserts the property directly rather than by counting: the quiet critical
// host must be present in the roster view, and its Total must be the number of
// records the store holds for it -- not the number that happened to fit a window.
func TestRosterShowsEveryHostThatAGlobalScanWouldDrop(t *testing.T) {
	ctx := context.Background()
	store := NewMemoryRecordStore(RecordRetention{Window: 365 * 24 * time.Hour, MaxRecords: 100000})
	seedBuriedHost(t, store, 20, 50) // 1002 records, 21 hosts

	view, err := rosterView(ctx, store, 5, time.Now())
	if err != nil {
		t.Fatal(err)
	}

	if got := len(view.Hosts); got != 21 {
		t.Fatalf("hosts in roster = %d, want 21 (one quiet critical + 20 noisy)", got)
	}
	var quiet *fleetHost
	for i := range view.Hosts {
		if view.Hosts[i].HostID == "quiet-critical" {
			quiet = &view.Hosts[i]
		}
	}
	if quiet == nil {
		t.Fatal("quiet-critical host is absent from the roster: this is the 2026-11-03t defect")
	}
	if quiet.Total != 2 {
		t.Errorf("quiet-critical total = %d, want 2 (the host's real record count)", quiet.Total)
	}
	if quiet.MaxSeverity != "critical" {
		t.Errorf("quiet-critical max severity = %q, want critical", quiet.MaxSeverity)
	}
	if view.TotalEvents != 1002 {
		t.Errorf("total_events = %d, want 1002", view.TotalEvents)
	}
	// Worst-first ordering must still lead with the critical host even though it
	// is the quietest one on the fleet.
	if view.Hosts[0].HostID != "quiet-critical" {
		t.Errorf("first host = %q, want quiet-critical", view.Hosts[0].HostID)
	}
}

// TestRosterTotalIsNotATopKSample is the counterweight to the cheap fix.
//
// Fetching the newest 3 records per host gives correct MEMBERSHIP, so a test like
// the one above (presence + one total) can pass while every aggregate is a top-K
// statistic. This asserts the aggregates against the full record set for a host
// that has more records than any recent window.
func TestRosterTotalIsNotATopKSample(t *testing.T) {
	ctx := context.Background()
	store := NewMemoryRecordStore(RecordRetention{Window: 365 * 24 * time.Hour, MaxRecords: 100000})
	base := time.Date(2026, 11, 1, 0, 0, 0, 0, time.UTC)

	// 12 events: 4 info, 8 medium, with the medium ones NOT newest -- so both a
	// top-K count and a "severity of the newest record" shortcut are wrong, in
	// opposite directions.
	for i := 0; i < 12; i++ {
		sev := schema.SeverityInfo
		if i%3 == 0 {
			sev = schema.SeverityMedium
		}
		if err := store.Append(ctx, Record{
			When: base.Add(time.Duration(i) * time.Minute), HostID: "h1",
			Event: schema.EventBehaviorRuleMatch, Severity: sev, RuleID: "carbonato.test",
		}); err != nil {
			t.Fatal(err)
		}
	}

	view, err := rosterView(ctx, store, 3, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	if len(view.Hosts) != 1 {
		t.Fatalf("hosts = %d, want 1", len(view.Hosts))
	}
	h := view.Hosts[0]
	if h.Total != 12 {
		t.Errorf("total = %d, want 12 (top-K would say 3)", h.Total)
	}
	if h.BySeverity["medium"] != 4 || h.BySeverity["info"] != 8 {
		t.Errorf("by_severity = %v, want medium:4 info:8 (top-K would report 3 events)", h.BySeverity)
	}
	if h.MaxSeverity != "medium" {
		t.Errorf("max severity = %q, want medium", h.MaxSeverity)
	}
	if len(h.Recent) != 3 {
		t.Errorf("recent = %d, want 3 (the sample is capped, the aggregate is not)", len(h.Recent))
	}
	// The cap is on the DETAIL, so the sample must be the newest three.
	newest := h.Recent[0].When
	for _, r := range h.Recent[1:] {
		if r.When.After(newest) {
			t.Errorf("recent is not newest-first: %v then %v", newest, r.When)
		}
	}
}

// TestRosterAggregateMatchesBuildFleetView keeps the two rollups from drifting:
// for an UNTRUNCATED scan they must agree exactly, since the only difference is
// which records they were given. If these ever diverge, the console has two
// different answers to "what is on this host".
func TestRosterAggregateMatchesBuildFleetView(t *testing.T) {
	ctx := context.Background()
	store := NewMemoryRecordStore(RecordRetention{Window: 365 * 24 * time.Hour, MaxRecords: 100000})
	seedBuriedHost(t, store, 3, 4)

	recs, err := store.Query(ctx, RecordQuery{})
	if err != nil {
		t.Fatal(err)
	}
	fromScan := buildFleetView(append([]Record(nil), recs...), 0)
	fromRoster, err := rollupByHost(ctx, store, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(fromScan.Hosts) != len(fromRoster) {
		t.Fatalf("host count differs: scan %d vs roster %d", len(fromScan.Hosts), len(fromRoster))
	}
	for i := range fromScan.Hosts {
		a, b := fromScan.Hosts[i], fromRoster[i]
		if a.HostID != b.HostID || a.Total != b.Total || a.MaxSeverity != b.MaxSeverity ||
			!a.LastSeen.Equal(b.LastSeen) || a.HostName != b.HostName {
			t.Errorf("host %d differs:\n scan   = %+v\n roster = %+v", i, a, b)
		}
		if len(a.BySeverity) != len(b.BySeverity) || len(a.ByPack) != len(b.ByPack) {
			t.Errorf("host %s bucket count differs: scan %v roster %v", a.HostID, a.BySeverity, b.BySeverity)
		}
		for k, v := range a.BySeverity {
			if b.BySeverity[k] != v {
				t.Errorf("host %s severity %q: scan %d roster %d", a.HostID, k, v, b.BySeverity[k])
			}
		}
	}
}

// TestRosterGroupedHostsMatchUnfiltered: the per-host query path and the
// single-scan path must produce identical rows for the hosts asked for. Two code
// paths, one answer -- otherwise a filtered console and a full one disagree.
func TestRosterGroupedHostsMatchUnfiltered(t *testing.T) {
	ctx := context.Background()
	store := NewMemoryRecordStore(RecordRetention{Window: 365 * 24 * time.Hour, MaxRecords: 100000})
	seedBuriedHost(t, store, 3, 4)

	all, err := rollupByHost(ctx, store, nil)
	if err != nil {
		t.Fatal(err)
	}
	some, err := rollupByHost(ctx, store, []string{"quiet-critical", "noisy-01"})
	if err != nil {
		t.Fatal(err)
	}
	if len(some) != 2 {
		t.Fatalf("grouped hosts = %d, want 2", len(some))
	}
	for _, s := range some {
		var match *fleetHost
		for i := range all {
			if all[i].HostID == s.HostID {
				match = &all[i]
			}
		}
		if match == nil {
			t.Fatalf("grouped host %q absent from unfiltered rollup", s.HostID)
		}
		if match.Total != s.Total || match.MaxSeverity != s.MaxSeverity || !match.LastSeen.Equal(s.LastSeen) {
			t.Errorf("host %q: unfiltered %+v vs grouped %+v", s.HostID, *match, s)
		}
	}
}

// TestRosterEmptyStoreIsAnEmptyViewNotAnError: the honest answer for "no records"
// is zero hosts, not a 500 and not a nil slice that renders as a blank row.
func TestRosterEmptyStoreIsAnEmptyViewNotAnError(t *testing.T) {
	ctx := context.Background()
	store := NewMemoryRecordStore(DefaultRecordRetention)
	view, err := rosterView(ctx, store, 5, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	if view.Hosts == nil {
		t.Error("hosts is nil, want an empty slice")
	}
	if len(view.Hosts) != 0 || view.TotalEvents != 0 {
		t.Errorf("view = %+v, want empty", view)
	}
}

// TestRosterHostsCapNothing is the Truncated=true mutant's target, kept as a
// separate half so the mutant fires by name rather than on a nil dereference.
// A rollup that capped its own scan would have to say so.
func TestRosterHostsCapNothing(t *testing.T) {
	ctx := context.Background()
	store := NewMemoryRecordStore(RecordRetention{Window: 365 * 24 * time.Hour, MaxRecords: 100000})
	seedBuriedHost(t, store, 2, 3)
	view, err := rosterView(ctx, store, 2, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	if view.Truncated != nil {
		t.Errorf("truncated = %v, want nil: the roster caps DETAIL (recent) and nothing else", *view.Truncated)
	}
	if view.ScanLimit != 0 {
		t.Errorf("scan_limit = %d, want 0: there is no scan to cap", view.ScanLimit)
	}
}

// TestRosterNilStoreIsAnEmptyView: no retention configured is a configuration,
// not a failure.
func TestRosterNilStoreIsAnEmptyView(t *testing.T) {
	view, err := rosterView(context.Background(), nil, 5, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	if len(view.Hosts) != 0 {
		t.Errorf("hosts = %d, want 0", len(view.Hosts))
	}
}

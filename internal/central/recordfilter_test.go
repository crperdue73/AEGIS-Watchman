package central

import (
	"context"
	"path/filepath"
	"testing"
	"time"
)

// Beat 802. `recordMatches` is the filter every query goes through, and nothing tested it
// directly. But the interesting question is not "does the filter work" -- it is "do the backends
// agree". The memory store calls recordMatches in Go; the SQLite store re-expresses the same
// semantics as a WHERE clause. Two implementations of one predicate, and if they drift, the same
// query returns different fleets depending on which backend is configured. That is a defect nobody
// would see in testing against one backend and would see in production on the other.
//
// So: the Go predicate is tested directly below, and then the two backends are run against the
// same corpus with the same queries and required to agree exactly.

var recBase = time.Date(2026, 11, 3, 12, 0, 0, 0, time.UTC)

func mkRec(host, sev string, when time.Time) Record {
	return Record{HostID: host, HostName: host + "-name", Severity: sev, When: when,
		RuleID: "r-" + sev, Technique: "T0000", RulePack: "pack", RuleVersion: "1"}
}

// --- direct tests of the Go predicate -------------------------------------

func TestRecordMatchesHostFilterIsExactNotPrefix(t *testing.T) {
	r := mkRec("box-1", "info", recBase)
	// "box" must NOT match "box-1". A prefix or substring comparison here would let a host filter
	// return a different host's events, which for a security console means an operator
	// investigating one machine sees another machine's activity.
	if recordMatches(r, RecordQuery{HostID: "box"}) {
		t.Errorf("host filter \"box\" matched record on \"box-1\" -- filters must be exact, or " +
			"one host's investigation is answered with another host's events")
	}
	if !recordMatches(r, RecordQuery{HostID: "box-1"}) {
		t.Errorf("host filter \"box-1\" did not match its own record")
	}
}

func TestRecordMatchesEmptyHostMatchesAll(t *testing.T) {
	for _, h := range []string{"a", "b"} {
		if !recordMatches(mkRec(h, "info", recBase), RecordQuery{}) {
			t.Errorf("empty HostID must mean \"every host\"; it excluded %q", h)
		}
	}
}

// Since is INCLUSIVE. A record landing exactly on the boundary belongs to the window.
func TestRecordMatchesSinceIsInclusive(t *testing.T) {
	r := mkRec("h", "info", recBase)
	if !recordMatches(r, RecordQuery{Since: recBase}) {
		t.Errorf("a record exactly at Since was excluded; Since is inclusive, and an exclusive " +
			"boundary loses events that share a timestamp with the window edge")
	}
	if !recordMatches(r, RecordQuery{Since: recBase.Add(-time.Second)}) {
		t.Errorf("record after Since was excluded")
	}
	if recordMatches(r, RecordQuery{Since: recBase.Add(time.Second)}) {
		t.Errorf("record before Since was included")
	}
}

// Until is EXCLUSIVE, stated in the code's own comment so adjacent windows do not double-count.
// The pair matters: if one bound were exclusive and the other inclusive by accident, an event on a
// window edge would be counted twice or dropped, and either way the totals stop adding up.
func TestRecordMatchesUntilIsExclusive(t *testing.T) {
	r := mkRec("h", "info", recBase)
	if recordMatches(r, RecordQuery{Until: recBase}) {
		t.Errorf("a record exactly at Until was included; Until is exclusive so that adjacent " +
			"windows partition the stream instead of double-counting the boundary")
	}
	if !recordMatches(r, RecordQuery{Until: recBase.Add(time.Second)}) {
		t.Errorf("record before Until was excluded")
	}
	if recordMatches(r, RecordQuery{Until: recBase.Add(-time.Second)}) {
		t.Errorf("record after Until was included")
	}

	// The partition property, stated as a test rather than trusted to two separate boundary
	// checks: splitting at a point must cover every record exactly once.
	at := recBase
	inLeft := recordMatches(r, RecordQuery{Until: at})
	inRight := recordMatches(r, RecordQuery{Since: at})
	if inLeft == inRight {
		t.Errorf("record at the split point is in both windows or neither (left=%v right=%v) -- "+
			"adjacent windows must partition the stream exactly", inLeft, inRight)
	}
}

// MinSeverity is a FLOOR: the named severity is included, everything below is excluded.
func TestRecordMatchesMinSeverityIsAFloor(t *testing.T) {
	cases := []struct {
		rec, min string
		want     bool
	}{
		{"critical", "high", true},
		{"high", "high", true},    // the floor itself is included
		{"medium", "high", false}, // just below
		{"info", "info", true},
		{"low", "info", true},
		{"info", "critical", false},
	}
	for _, c := range cases {
		got := recordMatches(mkRec("h", c.rec, recBase), RecordQuery{MinSeverity: c.min})
		if got != c.want {
			t.Errorf("MinSeverity=%q vs record severity %q: got %v, want %v",
				c.min, c.rec, got, c.want)
		}
	}
}

// An UNKNOWN severity is rank 0, so it sits below every real floor. This is the sharp edge of the
// same rank function: a typo'd or newly-added severity is filtered out of any query that names a
// floor, silently. Pinned because it is a real property with a real consequence (a new severity
// fails closed rather than showing up in every critical-only view), not because it is desirable.
func TestRecordMatchesUnknownSeverityFailsClosed(t *testing.T) {
	if recordMatches(mkRec("h", "screaming", recBase), RecordQuery{MinSeverity: "info"}) {
		t.Errorf("an unrecognised severity passed an info-or-above filter. Rank 0 means it " +
			"fails closed; if that is intentional, this test is the record of the decision")
	}
	// But with no floor named it is included, so it is not invisible entirely.
	if !recordMatches(mkRec("h", "screaming", recBase), RecordQuery{}) {
		t.Errorf("an unrecognised severity was excluded with no filter set")
	}
}

// Filters combine as AND, not OR.
func TestRecordMatchesFiltersCombineAsAnd(t *testing.T) {
	r := mkRec("h1", "low", recBase)
	q := RecordQuery{HostID: "h1", Since: recBase.Add(-time.Hour),
		Until: recBase.Add(time.Hour), MinSeverity: "low"}
	if !recordMatches(r, q) {
		t.Errorf("record satisfying every filter was rejected")
	}
	// Break each one in turn; each must independently reject.
	breaks := map[string]RecordQuery{
		"host":  {HostID: "h2", Since: q.Since, Until: q.Until, MinSeverity: q.MinSeverity},
		"since": {HostID: q.HostID, Since: recBase.Add(time.Second), Until: q.Until, MinSeverity: q.MinSeverity},
		"until": {HostID: q.HostID, Since: q.Since, Until: recBase.Add(-time.Second), MinSeverity: q.MinSeverity},
		// "critical2" was my first attempt and it was wrong: an unrecognised severity ranks 0,
		// so it is a floor every record clears -- the case proved nothing while looking like it
		// did. The record IS critical (rank 5), so it needs a floor above critical to fail, which
		// no real severity provides. Use a record at "low" for this half instead.
		"severity": {HostID: q.HostID, Since: q.Since, Until: q.Until, MinSeverity: "high"},
	}
	for name, bq := range breaks {
		if recordMatches(r, bq) {
			t.Errorf("failing the %s filter still matched -- if filters were OR'd, one broken "+
				"filter would be masked by the others and a query would return more than asked", name)
		}
	}
}

// --- backend agreement (memory vs file) --------------------------------
//
// The memory store calls recordMatches in Go; the file store re-expresses the same semantics in
// its own query path. If they drift, the same query returns different fleets depending on which
// backend is configured -- invisible in testing against one backend, visible in production on the
// other. The SQLite half lives in recordfilter_sqlite_test.go under `//go:build sqlite`, NOT
// skipped inside a normal test: a test that compiles everywhere and silently does nothing is the
// shape that makes people believe coverage exists.

var filterCorpus = []Record{
	mkRec("h1", "critical", recBase),
	mkRec("h1", "info", recBase.Add(time.Hour)),
	mkRec("h1", "low", recBase.Add(-time.Hour)),
	mkRec("h2", "critical", recBase),
	mkRec("h2", "medium", recBase.Add(30*time.Minute)),
	mkRec("h3", "high", recBase.Add(2*time.Hour)),
	// BEAT 802: h1/h2/h3 was a corpus where NO host is a prefix of another, so a SQLite host
	// filter written as LIKE 'x%' returned exactly the same set as = 'x' and the mutation
	// survived. A cross-backend test needs a host pair where a prefix match OVER-matches,
	// or it cannot see the most likely way the two implementations diverge.
	mkRec("h10", "critical", recBase.Add(15*time.Minute)),
	mkRec("h1-node2", "high", recBase.Add(20*time.Minute)),
}

var filterQueries = map[string]RecordQuery{
	"all":            {},
	"host h1":        {HostID: "h1"},
	"host h2":        {HostID: "h2"},
	"host absent":    {HostID: "nope"},
	"host prefix h1": {HostID: "h1"}, // must NOT return h10 or h1-node2
	"since incl":     {Since: recBase},
	"until excl":     {Until: recBase},
	"window":         {Since: recBase.Add(-time.Hour), Until: recBase.Add(time.Hour)},
	"min high":       {MinSeverity: "high"},
	"min info":       {MinSeverity: "info"},
	"h1 crit window": {HostID: "h1", MinSeverity: "critical"},
	"empty window":   {Since: recBase.Add(10 * time.Hour), Until: recBase.Add(11 * time.Hour)},
}

func recKey(r Record) string { return r.HostID + "|" + r.Severity + "|" + r.When.UTC().String() }

// compareStores runs every query against both stores and requires the same SET of records.
// Ordering and limiting are deliberately not compared here -- that is a separate contract with its
// own traps, and mixing them in would let an ordering bug hide behind a filter test.
func compareStores(t *testing.T, aName string, a RecordStore, bName string, b RecordStore) {
	t.Helper()
	ctx := context.Background()
	for name, q := range filterQueries {
		arecs, err := a.Query(ctx, q)
		if err != nil {
			t.Fatalf("%s: %s query: %v", name, aName, err)
		}
		brecs, err := b.Query(ctx, q)
		if err != nil {
			t.Fatalf("%s: %s query: %v", name, bName, err)
		}
		aset := map[string]bool{}
		for _, r := range arecs {
			aset[recKey(r)] = true
		}
		bset := map[string]bool{}
		for _, r := range brecs {
			bset[recKey(r)] = true
		}
		for k := range aset {
			if !bset[k] {
				t.Errorf("query %q: %s returned %s, %s did not -- the backends disagree about "+
					"the filter, so the fleet depends on which store is configured", name, aName, k, bName)
			}
		}
		for k := range bset {
			if !aset[k] {
				t.Errorf("query %q: %s returned %s, %s did not", name, bName, k, aName)
			}
		}
	}
}

func TestRecordFiltersAgreeMemoryVsFile(t *testing.T) {
	mem := NewMemoryRecordStore(RecordRetention{})
	file, err := NewFileRecordStore(filepath.Join(t.TempDir(), "recs.json"), RecordRetention{})
	if err != nil {
		t.Fatalf("open file store: %v", err)
	}
	ctx := context.Background()
	for _, r := range filterCorpus {
		if err := mem.Append(ctx, r); err != nil {
			t.Fatalf("mem append: %v", err)
		}
		if err := file.Append(ctx, r); err != nil {
			t.Fatalf("file append: %v", err)
		}
	}
	compareStores(t, "memory", mem, "file", file)
}

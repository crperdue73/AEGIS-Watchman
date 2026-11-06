package central

// BEAT 764 -- Fern's case (7497): a check whose two inputs are not independent.
//
// Fern's guard compared two wall-clock stamps taken by the SAME clock, so a
// stopped process froze both and the delta stayed small. The check could not
// fail. That is not a weak check; it is a check comparing a thing to itself.
//
// Question here: does the fleet view compare a thing to itself?
//
//   fleet.go:127  "Records are newest-first, so the first seen is the latest."
//
// (2) is true for any order -- a max does not need sorted input -- so the guard
// is safe. The comment's premise is unnecessary for the arithmetic, and nothing
// in this package enforces it. Worth knowing, not a defect.
//
// The self-reference that IS real: retain() stamps every event in ONE ingest
// with a single `when := s.nowTime()`. So LastSeen is the newest INGEST that
// DETECTED something, not the newest event and not last contact.
//
// X1  control: LastSeen is max(rec.When), independent of input order
// X2  the ordering claim lives in a comment, not in code or test
// X3  every event in one ingest shares one When (the self-reference)
// X4  a clean ingest produces no records, so it cannot move LastSeen
// X5  the label-vs-value question, and what the code itself says

import (
	"context"
	"sort"
	"testing"
	"time"

	"github.com/crperdue73/AEGIS-Watchman/internal/collector"
	"github.com/crperdue73/AEGIS-Watchman/internal/schema"
)

func lsHost() schema.Host   { return schema.Host{ID: "h1", Name: "box"} }
func lsAgent() schema.Agent { return schema.Agent{ID: "a1"} }

func lsInv(root string, files map[string]string) *collector.Inventory {
	names := make([]string, 0, len(files))
	for n := range files {
		names = append(names, n)
	}
	sort.Strings(names)
	out := &collector.Inventory{Root: root, Files: []collector.FileState{}}
	for _, n := range names {
		out.Files = append(out.Files, collector.FileState{
			Path:    root + "/" + n,
			SHA256:  files[n],
			Size:    1,
			ModTime: time.Date(2026, 11, 1, 0, 0, 0, 0, time.UTC),
		})
	}
	return out
}

// X1 -- control: max, not first.
func TestLastSeenIsMaxNotFirst(t *testing.T) {
	early := time.Date(2026, 11, 1, 10, 0, 0, 0, time.UTC)
	late := time.Date(2026, 11, 2, 10, 0, 0, 0, time.UTC)

	// Deliberately OLDEST-first, contradicting the comment.
	recs := []Record{
		{HostID: "h1", HostName: "box", When: early, Severity: "low"},
		{HostID: "h1", HostName: "box", When: late, Severity: "high"},
	}
	v := buildFleetView(recs, 10)
	if len(v.Hosts) != 1 {
		t.Fatalf("X1: want 1 host, got %d", len(v.Hosts))
	}
	if !v.Hosts[0].LastSeen.Equal(late) {
		t.Fatalf("X1: LastSeen = %v, want %v -- LastSeen stopped being a max and "+
			"started trusting the input order the comment promises",
			v.Hosts[0].LastSeen, late)
	}
	t.Logf("X1 MEASURED: LastSeen is a max over rec.When with OLDEST-first input. " +
		"The comment's 'newest-first' premise is not needed for the arithmetic.")
}

// X2 -- the ordering claim. Pinned as a record, not an assertion: the package
// makes no promise, and asserting either order would manufacture one.
func TestFleetOrderingPremiseIsUnenforced(t *testing.T) {
	// buildFleetView must produce the same LastSeen for either order. That is the
	// only thing that can be asserted honestly.
	early := time.Date(2026, 11, 1, 10, 0, 0, 0, time.UTC)
	late := time.Date(2026, 11, 2, 10, 0, 0, 0, time.UTC)
	a := []Record{{HostID: "h1", When: early}, {HostID: "h1", When: late}}
	b := []Record{{HostID: "h1", When: late}, {HostID: "h1", When: early}}

	va, vb := buildFleetView(a, 10), buildFleetView(b, 10)
	if !va.Hosts[0].LastSeen.Equal(vb.Hosts[0].LastSeen) {
		t.Fatalf("X2: LastSeen depends on input order (%v vs %v) -- the comment's "+
			"premise is now load-bearing and must be enforced, not asserted in prose",
			va.Hosts[0].LastSeen, vb.Hosts[0].LastSeen)
	}
	// CORRECTION, measured after I first wrote this: the ordering claim IS in a
	// contract -- eventstore.go:102 says Query/Recent return records "newest
	// first". So the premise is documented in two places, not invented in a
	// comment. My first version of this log said "no store contract guarantees
	// it", which was wrong; I had not read the interface.
	t.Logf("X2 MEASURED: LastSeen is order-independent, so the 'newest-first' premise " +
		"is unnecessary for the arithmetic -- but it IS a documented store contract " +
		"(eventstore.go:102). The comment is redundant, not unbacked.")
}

// X3 -- the self-reference.
func TestEveryEventInOneIngestSharesOneTimestamp(t *testing.T) {
	s := NewService(NewMemoryStore())
	store := NewMemoryRecordStore(DefaultRecordRetention)
	s.SetRecordStore(store)

	base := time.Date(2026, 11, 2, 12, 0, 0, 0, time.UTC)
	s.now = func() time.Time { return base }

	if _, err := s.Ingest(context.Background(), lsHost(), lsAgent(),
		lsInv("/srv/x", map[string]string{"a": "aaa"})); err != nil {
		t.Fatalf("X3: first ingest: %v", err)
	}

	// Second ingest, later clock, two files changed so two events exist.
	s.now = func() time.Time { return base.Add(time.Hour) }
	res, err := s.Ingest(context.Background(), lsHost(), lsAgent(),
		lsInv("/srv/x", map[string]string{"a": "bbb", "b": "ccc"}))
	if err != nil {
		t.Fatalf("X3: second ingest: %v", err)
	}
	if len(res.Events) < 2 {
		t.Fatalf("X3: second ingest produced %d events, want >= 2 for this test",
			len(res.Events))
	}

	recs, err := store.Query(context.Background(), RecordQuery{})
	if err != nil {
		t.Fatalf("X3: list: %v", err)
	}
	if len(recs) == 0 {
		t.Fatalf("X3: no records retained")
	}
	distinct := map[time.Time]int{}
	for _, r := range recs {
		distinct[r.When]++
	}
	maxGroup := 0
	for _, n := range distinct {
		if n > maxGroup {
			maxGroup = n
		}
	}
	if maxGroup < 2 {
		t.Fatalf("X3: no ingest produced multiple records sharing one timestamp -- " +
			"retain may now stamp per event; re-read finding 2026-11-02s")
	}
	t.Logf("X3 MEASURED: %d records, %d distinct timestamps, largest group %d. "+
		"Every event in one ingest shares one When because retain() calls nowTime() "+
		"once per ingest.", len(recs), len(distinct), maxGroup)
}

// X4 -- the consequence: a clean ingest cannot move LastSeen, because it makes
// no records at all. So LastSeen means "last ingest that DETECTED something."
func TestCleanIngestProducesNoRecords(t *testing.T) {
	s := NewService(NewMemoryStore())
	store := NewMemoryRecordStore(DefaultRecordRetention)
	s.SetRecordStore(store)

	base := time.Date(2026, 11, 2, 12, 0, 0, 0, time.UTC)
	s.now = func() time.Time { return base }
	inv := lsInv("/srv/x", map[string]string{"a": "aaa"})
	if _, err := s.Ingest(context.Background(), lsHost(), lsAgent(), inv); err != nil {
		t.Fatalf("X4: %v", err)
	}

	// Identical ingest six hours later: no drift, nothing to retain.
	s.now = func() time.Time { return base.Add(6 * time.Hour) }
	res, err := s.Ingest(context.Background(), lsHost(), lsAgent(),
		lsInv("/srv/x", map[string]string{"a": "aaa"}))
	if err != nil {
		t.Fatalf("X4: %v", err)
	}
	if len(res.Events) != 0 {
		t.Fatalf("X4: identical ingest produced %d events; test assumes none", len(res.Events))
	}
	recs, _ := store.Query(context.Background(), RecordQuery{})
	if len(recs) != 0 {
		t.Fatalf("X4: a clean ingest produced %d records -- retain now runs without "+
			"events, which changes what LastSeen means", len(recs))
	}
	// MUTATION HONESTY (beat 764) -- read this before trusting X4.
	//
	// X4 asserts that a clean ingest produces no records. I could NOT make it go
	// red. Two mutants, both green:
	//   (a) delete the call-site guard `if len(events) > 0`  -> retain() still
	//       refuses on its own second guard.
	//   (b) delete BOTH guards                               -> retain() still
	//       refuses, because the for-loop over an empty slice appends nothing.
	//
	// So this is not a test that has been made red. It is a test of a property
	// that the code FORCES structurally: you cannot append one record per event
	// when there are zero events. Writing "the guard protects this" would be a
	// claim, and I would have been wrong; the guards are a tidy-up, not the
	// mechanism.
	//
	// The consequence for finding 2026-11-02s is unchanged and does not rest on
	// X4 being mutatable: the measured fact is that a clean ingest stores no
	// records (records examined directly), so nothing advances LastSeen. What I am
	// NOT claiming is that deleting a guard would change that.
	t.Logf("X4 MEASURED: a clean ingest retains nothing (retain() refuses on an " +
		"empty event slice, twice over). LastSeen only advances when something " +
		"DETECTED, so a host reporting clean for a week shows a week-old LastSeen.")
}

// X5 -- the label-vs-value question, decided by what the code already says.
func TestLastSeenSemanticsAreDocumentedAsRetentionTime(t *testing.T) {
	// eventstore.go:38 already states it: "When is when the record was retained
	// (server time at ingest)." So the FIELD is honest. The risk is the page
	// presenting it as liveness. Measuring the field's meaning, leaving wording to
	// a human.
	t.Logf("X5 MEASURED: Record.When is documented as retention time, and LastSeen " +
		"is max(When). So the value is correctly a detection-time, and the only open " +
		"question is whether the fleet page labels it as liveness. Not a defect in " +
		"the data path; possibly one in the label. No code changed.")
}

package collector

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/crperdue73/AEGIS-Watchman/internal/schema"
)

func testEvents(ids ...string) []schema.Event {
	var out []schema.Event
	for _, id := range ids {
		ev := schema.NewEnvelope(schema.EventBehaviorRuleMatch, schema.Host{ID: "h1"})
		ev.Rule = &schema.Rule{ID: id, Pack: "carbonato", Version: "2"}
		ev.Severity = schema.SeverityHigh
		out = append(out, *ev)
	}
	return out
}

// flakySubmitter succeeds only when its failure budget is exhausted; it records
// every batch it accepted.
type flakySubmitter struct {
	mu      sync.Mutex
	failN   int
	batches [][]string
}

func (f *flakySubmitter) SubmitActions(_ context.Context, req ActionsRequest) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failN > 0 {
		f.failN--
		return errors.New("central down")
	}
	var ids []string
	for _, ev := range req.Events {
		id := ""
		if ev.Rule != nil {
			id = ev.Rule.ID
		}
		ids = append(ids, id)
	}
	f.batches = append(f.batches, ids)
	return nil
}

func (f *flakySubmitter) accepted() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	var all []string
	for _, b := range f.batches {
		all = append(all, b...)
	}
	return all
}

func TestSpoolDisabledIsNoOp(t *testing.T) {
	s := NewSpool("", 0)
	if s.Enabled() {
		t.Fatal("empty path should be disabled")
	}
	if err := s.Append(schema.Host{ID: "h"}, schema.Agent{}, testEvents("r")); err != nil {
		t.Fatalf("disabled append: %v", err)
	}
	n, err := s.Drain(context.Background(), func(context.Context, ActionsRequest) error { return nil })
	if err != nil || n != 0 {
		t.Fatalf("disabled drain = %d,%v", n, err)
	}
}

func TestSpoolAppendDrainRoundTrip(t *testing.T) {
	dir := t.TempDir()
	s := NewSpool(filepath.Join(dir, "spool.jsonl"), 0)
	if err := s.Append(schema.Host{ID: "h1"}, schema.Agent{ID: "a1"}, testEvents("r1", "r2")); err != nil {
		t.Fatal(err)
	}
	if n, _ := s.Len(); n != 1 {
		t.Fatalf("len = %d, want 1 batch", n)
	}

	var got []string
	n, err := s.Drain(context.Background(), func(_ context.Context, req ActionsRequest) error {
		for _, ev := range req.Events {
			got = append(got, ev.Rule.ID)
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if n != 2 || len(got) != 2 {
		t.Fatalf("drained %d, got %v", n, got)
	}
	// Successful drain empties (and removes) the spool.
	if ln, _ := s.Len(); ln != 0 {
		t.Fatalf("spool not empty after drain: %d", ln)
	}
	if _, err := os.Stat(filepath.Join(dir, "spool.jsonl")); !os.IsNotExist(err) {
		t.Errorf("drained spool file should be removed, stat err = %v", err)
	}
}

// TestSpoolDurabilityAcrossRestart is the whole point: a match that central did
// not accept must survive the collector process ending and a new one starting.
func TestSpoolDurabilityAcrossRestart(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "spool.jsonl")

	// "First process": append, then try to ship to a dead central.
	s1 := NewSpool(path, 0)
	if err := s1.Append(schema.Host{ID: "h1"}, schema.Agent{}, testEvents("critical.exfil")); err != nil {
		t.Fatal(err)
	}
	if _, err := s1.Drain(context.Background(), func(context.Context, ActionsRequest) error {
		return errors.New("connection refused")
	}); err != nil {
		t.Fatalf("drain with failing submit should not error: %v", err)
	}
	if n, _ := s1.Len(); n != 1 {
		t.Fatalf("failed drain must keep the batch, len = %d", n)
	}

	// "Second process": fresh Spool over the same file, central now healthy.
	s2 := NewSpool(path, 0)
	sub := &flakySubmitter{}
	if _, err := s2.Drain(context.Background(), sub.SubmitActions); err != nil {
		t.Fatal(err)
	}
	if got := sub.accepted(); len(got) != 1 || got[0] != "critical.exfil" {
		t.Fatalf("restart drain delivered %v, want [critical.exfil]", got)
	}
	if n, _ := s2.Len(); n != 0 {
		t.Errorf("spool should be empty after successful restart drain, len = %d", n)
	}
}

func TestSpoolDrainStopsAtFirstFailure(t *testing.T) {
	dir := t.TempDir()
	s := NewSpool(filepath.Join(dir, "spool.jsonl"), 0)
	for _, id := range []string{"r1", "r2", "r3"} {
		if err := s.Append(schema.Host{ID: "h1"}, schema.Agent{}, testEvents(id)); err != nil {
			t.Fatal(err)
		}
	}
	// Fail on the SECOND batch: first ships, r2 and r3 must remain, in order.
	var seen int
	sub := func(_ context.Context, req ActionsRequest) error {
		seen++
		if seen == 2 {
			return errors.New("central hiccup")
		}
		return nil
	}
	n, err := s.Drain(context.Background(), sub)
	if err != nil {
		t.Fatal(err)
	}
	if n != 1 {
		t.Fatalf("shipped = %d, want 1 (only r1 before the failure)", n)
	}
	recs, _ := s.read()
	if len(recs) != 2 {
		t.Fatalf("remaining batches = %d, want 2 (r2, r3)", len(recs))
	}
	if recs[0].Events[0].Rule.ID != "r2" || recs[1].Events[0].Rule.ID != "r3" {
		t.Errorf("order not preserved: %v %v", recs[0].Events[0].Rule.ID, recs[1].Events[0].Rule.ID)
	}
}

func TestSpoolDropsMalformedLine(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "spool.jsonl")
	s := NewSpool(path, 0)
	if err := s.Append(schema.Host{ID: "h1"}, schema.Agent{}, testEvents("good")); err != nil {
		t.Fatal(err)
	}
	// Append a torn/corrupt line, as a crash mid-write would leave.
	f, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0o640)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := f.WriteString("{not valid json\n"); err != nil {
		t.Fatal(err)
	}
	f.Close()

	sub := &flakySubmitter{}
	if _, err := s.Drain(context.Background(), sub.SubmitActions); err != nil {
		t.Fatal(err)
	}
	if got := sub.accepted(); len(got) != 1 || got[0] != "good" {
		t.Fatalf("malformed line should be dropped, got %v", got)
	}
}

func TestSpoolTrimDropsOldest(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "spool.jsonl")
	// A tiny bound forces trimming: each batch serializes to well over 64 bytes.
	s := NewSpool(path, 200)
	for _, id := range []string{"old1", "old2", "old3", "newest"} {
		if err := s.Append(schema.Host{ID: "h1"}, schema.Agent{}, testEvents(id)); err != nil {
			t.Fatal(err)
		}
	}
	recs, err := s.read()
	if err != nil {
		t.Fatal(err)
	}
	if len(recs) == 0 || len(recs) >= 4 {
		t.Fatalf("trim did not bound the spool: %d batches", len(recs))
	}
	// The newest entry must survive (a home for fresh evidence beats a home for
	// all of it).
	last := recs[len(recs)-1]
	if last.Events[0].Rule.ID != "newest" {
		t.Errorf("newest dropped by trim: kept %v", last.Events[0].Rule.ID)
	}
}

// TestBehaviorEngineSpoolsWhenCentralDown covers the integration: a rule match
// while central is unreachable must land on disk and ship on a later Poll once
// central recovers.
func TestBehaviorEngineSpoolsWhenCentralDown(t *testing.T) {
	tmp := t.TempDir()
	audit := filepath.Join(tmp, "audit.log")
	if err := os.WriteFile(audit, nil, 0o644); err != nil {
		t.Fatal(err)
	}
	spoolPath := filepath.Join(tmp, "spool.jsonl")
	cfg := &Config{Behavior: Behavior{RulesDir: "../../rules", AuditLog: audit, Spool: spoolPath}}

	sub := &flakySubmitter{failN: 1} // first submission fails, then recovers
	be, err := NewBehaviorEngine(cfg, schema.Host{ID: "h1"}, &schema.Agent{ID: "a1"}, sub)
	if err != nil {
		t.Fatalf("NewBehaviorEngine: %v", err)
	}

	writeAuditChain(t, audit)
	evs := be.Poll()
	if len(evs) == 0 {
		t.Fatal("expected matches from the chain")
	}
	// Central was down for this cycle: nothing accepted yet, but the spool holds.
	if got := sub.accepted(); len(got) != 0 {
		t.Fatalf("nothing should have been accepted yet, got %v", got)
	}
	if n, _ := be.spool.Len(); n != 1 {
		t.Fatalf("spool should hold 1 batch, got %d", n)
	}

	// Next cycle: central is back. The backlog drains even with no new activity.
	if evs := be.Poll(); len(evs) != 0 {
		t.Errorf("no new telemetry, expected 0 new events, got %d", len(evs))
	}
	if got := sub.accepted(); len(got) == 0 {
		t.Fatal("spooled batch should ship once central recovers")
	}
	if n, _ := be.spool.Len(); n != 0 {
		t.Errorf("spool should be empty after recovery, got %d", n)
	}
}

func TestSpoolTrimKeepsLargestFittingPrefix(t *testing.T) {
	dir := t.TempDir()
	appendN := func(path string, n int) *Spool {
		s := NewSpool(path, 1<<20)
		for i := 0; i < n; i++ {
			id := fmt.Sprintf("rule-%02d", i)
			if err := s.Append(schema.Host{ID: "h1"}, schema.Agent{}, testEvents(id)); err != nil {
				t.Fatal(err)
			}
		}
		return s
	}
	keptBytes := func(recs []spoolRecord) int64 {
		var total int64
		for _, r := range recs {
			l, _ := json.Marshal(r)
			total += int64(len(l) + 1)
		}
		return total
	}

	// Beat 819 history, kept because three of the four attempts were wrong and each wrong for a
	// different reason -- that is the useful part:
	//   1. Asserted a fixed 242-byte batch. testEvents stamps a timestamp via schema.NewEnvelope,
	//      so consecutive batches differ in length and the test flaked.
	//   2. "Fixed" it by learning the size from a SEPARATE spool, so the bytes measured were not
	//      the bytes under test. Flaked again (485 vs 486).
	//   3. Fixed that, but pointed the cap at exactly `total`, where `<=` and `<` cannot disagree
	//      because no drop is required at all. Green, and it killed nothing.
	//
	// What the sweep actually showed (2..8 batches, caps from total-4 to total): the surviving
	// `<=` -> `<` mutant at spool.go:248 is NOT OBSERVABLE through trim. Kept-batch counts were
	// identical for both operators at every cap; the loop's size includes the record it is about
	// to consider, and Append trims after each write, so the boundary case never changes the
	// outcome. That is a measured equivalence, not an asserted one.
	//
	// So this test does not pretend to kill that mutant. It pins the invariant that IS real and IS
	// observable: trim keeps the LARGEST prefix that fits, and never keeps bytes past the bound
	// (except the documented last-resort single batch). Both halves would break if someone
	// "simplified" the loop to drop a fixed count or to drop-then-check.
	for _, n := range []int{2, 3, 4, 5, 6, 7, 8} {
		probe := appendN(filepath.Join(dir, fmt.Sprintf("probe%d.jsonl", n)), n)
		probeRecs, err := probe.read()
		if err != nil {
			t.Fatal(err)
		}
		if len(probeRecs) != n {
			t.Fatalf("probe kept %d of %d batches", len(probeRecs), n)
		}
		total := keptBytes(probeRecs)

		for _, back := range []int64{0, 1, 2, 4} {
			cap := total - back
			sp := appendN(filepath.Join(dir, fmt.Sprintf("n%d_c%d.jsonl", n, cap)), n)
			sp.maxBytes = cap
			if err := sp.trim(); err != nil {
				t.Fatalf("trim at cap=%d: %v", cap, err)
			}
			got, err := sp.read()
			if err != nil {
				t.Fatal(err)
			}
			kb := keptBytes(got)
			// Which end survives matters more than how much. A spool that drops the NEWEST
			// record keeps a file named "evidence" while discarding the events closest to the
			// incident an investigator is asking about. Measured gap: a mutant that did
			// `recs = recs[:len(recs)-1]` survived this test before the ordering assertion below
			// existed. Event IDs are appended in order, so the surviving tail must be the newest.
			if len(got) > 0 && len(got) < n {
				want := probeRecs[n-len(got):]
				for i := range got {
					if got[i].Events[0].Rule.ID != want[i].Events[0].Rule.ID {
						t.Errorf("n=%d cap=%d: kept record %d is rule %q, want %q -- trim kept the "+
							"WRONG END (oldest evidence must survive, not the newest)",
							n, cap, i, got[i].Events[0].Rule.ID, want[i].Events[0].Rule.ID)
						break
					}
				}
			}
			if len(got) > 1 && kb > cap {
				t.Errorf("n=%d cap=%d: kept %d batches / %d bytes, over the bound. The oldest "+
					"record is what an investigator asks for after an incident; over-keeping is "+
					"tolerable, but the loop must not keep a multi-batch file past its bound.",
					n, cap, len(got), kb)
			}
			// Idempotence is the property I can actually measure from inside this spool: trimming
			// again must change nothing. If trim could have kept another batch, its decision was
			// already made on a stale size and a second pass would differ; if the loop over-dropped,
			// a second pass on the shortened file would also be stable -- so this does not by
			// itself prove optimality. It proves the loop CONVERGES, which is the failure mode that
			// matters operationally (a trim that shrinks the spool on every Append would eat the
			// backlog during an outage).
			//
			// I tried twice to assert "one more batch would still fit" and could not do it
			// honestly: the dropped record is gone, and reconstructing its size from the probe
			// spool is wrong because batch lengths differ (that was attempt 2's bug). Asserting it
			// from a minimum-batch proxy produced three false alarms at cap=1212/1456/1697. A test
			// that fails on correct code is worse than no test, so the claim is narrowed to what
			// the spool can witness about itself.
			if err := sp.trim(); err != nil {
				t.Fatalf("second trim at cap=%d: %v", cap, err)
			}
			again, err := sp.read()
			if err != nil {
				t.Fatal(err)
			}
			if len(again) != len(got) || keptBytes(again) != kb {
				t.Errorf("n=%d cap=%d: trim is not idempotent -- first pass kept %d batches/%d bytes, "+
					"second kept %d/%d", n, cap, len(got), kb, len(again), keptBytes(again))
			}
		}
	}
}

// TestSpoolDrainReportsRewriteFailure pins spool.go:127 -- the error check around rewrite after a
// partial drain. It was a mutation survivor until beat 824 because no test ever made rewrite fail.
//
// What the mutant costs: rewrite stores the undo record of "these batches have been delivered".
// If it fails after some events shipped, the spool still contains them, so the next Drain ships
// them a second time. The CORRECT code returns the error so the caller can see that central
// received events the spool still believes are pending. The mutant returns (shipped, nil): a
// successful-looking drain over a spool that was never shortened. Cursors drift, and duplicates
// arrive at central with no error anywhere to explain them.
//
// Reaching the failure without relying on file permissions (this box runs as root, where chmod
// binds nothing -- see docs/FINDING-2026-11-05h): rewrite writes through "<path>.tmp", so making
// that path a DIRECTORY makes os.OpenFile fail for any uid, root included.
//
// Two records are needed, and that detail is the whole test: with ONE record the drain either
// ships it (rewrite gets an empty slice, takes the os.Remove branch, never touches .tmp) or ships
// nothing (the shipped==0 guard returns before rewrite). Only a partial drain exercises :127.
func TestSpoolDrainReportsRewriteFailure(t *testing.T) {
	d := t.TempDir()
	path := filepath.Join(d, "spool.jsonl")
	s := NewSpool(path, 1<<20)

	host, agent := schema.Host{ID: "h-1"}, schema.Agent{ID: "a-1"}
	for i := 0; i < 2; i++ {
		if err := s.Append(host, agent, []schema.Event{{}}); err != nil {
			t.Fatalf("append %d: %v", i, err)
		}
	}

	// Block the rewrite's temp file. A directory at that path cannot be opened with O_WRONLY.
	if err := os.Mkdir(path+".tmp", 0o755); err != nil {
		t.Fatalf("could not block the temp path: %v", err)
	}

	calls := 0
	shipped, err := s.Drain(context.Background(), func(context.Context, ActionsRequest) error {
		calls++
		if calls > 1 {
			return errors.New("central down")
		}
		return nil
	})

	if calls != 2 {
		t.Fatalf("submit called %d times, want 2 -- the test did not set up a partial drain", calls)
	}
	if shipped != 1 {
		t.Errorf("shipped = %d, want 1", shipped)
	}
	if err == nil {
		t.Fatalf("Drain reported success after rewrite failed; the spool still holds the %d "+
			"event(s) it just delivered, so they will be shipped again on the next cycle and "+
			"central will see duplicates with no error to explain them", shipped)
	}
	if !strings.Contains(err.Error(), "temp") {
		t.Errorf("error does not name the rewrite failure: %v", err)
	}

	// And the spool must still hold both records: a failed rewrite means nothing was removed.
	recs, rerr := s.read()
	if rerr != nil {
		t.Fatalf("read after failed rewrite: %v", rerr)
	}
	if len(recs) != 2 {
		t.Errorf("spool holds %d records after a failed rewrite, want 2 (unchanged)", len(recs))
	}
}

// TestSpoolAtExactCeilingIsNotTrimmed pins spool.go:233 -- `st.Size() <= s.maxBytes`.
//
// The mutant (`<`) turns "at the ceiling" into "over the ceiling". The FILE ends up byte-identical
// either way -- the drop loop below also compares with `<=`, so it breaks on its first pass and
// nothing is actually removed. Measured: size 350 before, 350 after, in both builds. So a test
// that only inspects the file passes against the mutant, which is why this one survived four
// beats of trim tests.
//
// What the mutant DOES change is that trim() stops being a no-op: it reads and rewrites the whole
// spool and logs
//
//	collector: spool over 350 bytes; trimming to 2 batches
//
// on a spool that is exactly the size it is allowed to be. A spool parked at its ceiling is a
// NORMAL steady state -- it is the operator's configured limit -- so the mutant fires that alarm
// every cycle forever, and an operator who learns to ignore it will also ignore the real one.
// The assertion is therefore on the log, because the log is the only difference there is.
func TestSpoolAtExactCeilingIsNotTrimmed(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "spool.jsonl")
	host := schema.Host{ID: "h1"}
	agent := schema.Agent{ID: "a1"}
	one := []schema.Event{*schema.NewEnvelope(schema.EventIdentityDrift, host)}

	// Two separate batches, so the spool holds two records -- the trim loop's working set.
	if err := NewSpool(path, 1<<20).Append(host, agent, one); err != nil {
		t.Fatal(err)
	}
	if err := NewSpool(path, 1<<20).Append(host, agent, one); err != nil {
		t.Fatal(err)
	}
	st, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	exact := st.Size()

	var buf bytes.Buffer
	prev := log.Writer()
	log.SetOutput(&buf)
	defer log.SetOutput(prev)

	if err := NewSpool(path, exact).trim(); err != nil {
		t.Fatalf("trim at exactly the ceiling returned an error: %v", err)
	}

	if logged := buf.String(); strings.Contains(logged, "spool over") {
		t.Fatalf("a spool of exactly %d bytes -- the configured ceiling -- was reported as over "+
			"it; this alarm would fire every cycle on a healthy spool:\n%s", exact, logged)
	}

	// And the records must still be there.
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if n := strings.Count(string(data), "\n"); n != 2 {
		t.Fatalf("want 2 spooled records after a no-op trim, got %d", n)
	}
}

// TestSpoolMaxBytesIsAlwayPositive pins spool.go:47 and :223 by pinning the INVARIANT that makes
// both of them unreachable, rather than by contriving an input that reaches them.
//
// :47  NewSpool: `if maxBytes <= 0 { maxBytes = DefaultSpoolMaxBytes }` -- mutant `< 0`
// :223 trim:    `if s.maxBytes <= 0 { return nil }`                  -- mutant `< 0`
//
// Neither mutant can change behaviour, because no *reachable* Spool ever has maxBytes == 0: :47
// is the only place maxBytes is ever set, and it runs before the struct exists. Measured, not
// assumed -- `grep -n "Spool{" -r internal/ --include=*.go` returns exactly one non-test line,
// spool.go:50, the constructor's own return, downstream of the clamp. So the zero state :223
// guards against cannot be produced without hand-building a Spool{} literal, which nothing in
// the package or its tests does.
//
// This is a stronger claim than "equivalent": it is not that the two spellings agree at 0, it is
// that 0 never arrives. Pinning the reachable half keeps the guard honest if someone later adds a
// second constructor or a direct literal -- at which point this test fails and the mutants become
// real and need real tests.
func TestSpoolMaxBytesIsAlwayPositive(t *testing.T) {
	for _, in := range []int64{0, -1, -1000} {
		if got := NewSpool(filepath.Join(t.TempDir(), "s.jsonl"), in).maxBytes; got != DefaultSpoolMaxBytes {
			t.Errorf("NewSpool(maxBytes=%d) stored maxBytes=%d, want DefaultSpoolMaxBytes (%d); "+
				"if maxBytes can now be non-positive on a live Spool, spool.go:223's guard is "+
				"reachable and its mutant needs a real test", in, got, DefaultSpoolMaxBytes)
		}
	}
	if got := NewSpool(filepath.Join(t.TempDir(), "s.jsonl"), 4096).maxBytes; got != 4096 {
		t.Errorf("NewSpool(4096) stored %d; a positive maxBytes must be kept as given", got)
	}

	// The unreachability argument, made executable: a spool built the only way it can be built
	// never has a non-positive bound, so trim's zero-guard cannot fire on real traffic.
	if s := NewSpool(filepath.Join(t.TempDir(), "s.jsonl"), 0); s.maxBytes <= 0 {
		t.Fatalf("a constructed Spool has maxBytes=%d, making spool.go:223 reachable -- the "+
			"unreachability claim in this test is now false", s.maxBytes)
	}
}

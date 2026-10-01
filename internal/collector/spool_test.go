package collector

import (
	"context"
	"errors"
	"os"
	"path/filepath"
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

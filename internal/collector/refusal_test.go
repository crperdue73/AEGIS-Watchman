package collector

// BEAT 752 -- the measurement beat 751 left open, and CALLIE ASKED FOR (7421).
//
// Finding 2026-11-02g: the spool advances on `submit(...) == nil` and deletes
// the batch, so a 202 that accepted ZERO events reads as "shipped". The obvious
// fix -- refuse a total drop, return 400 -- only helps if the collector does
// the right thing with the refusal. Callie's warning, quoted:
//
//   "refusing a total drop is one line, but it only helps if the producer
//    retries on a refusal. If Drain treats refusal the same as error it will
//    preserve the batch (good) and spin on it (also good, though noisy). If it
//    treats it as a permanent failure it deletes -- and you have moved the drop,
//    not closed it."
//
// These tests do not fix anything. They are the measurement that makes Dad's
// decision informed, and that answer Callie's question with the real Spool:
//
//	R1 a refusal (non-nil error) PRESERVES the batch          -- it is not deleted
//	R2 after a refusal the batch is RESUMBITTED next drain     -- it is not dropped
//	R3 the pipeline does NOT spin on it (one submit per drain)-- no tight loop
//	R4 an empty-event batch never reaches central at all       -- client skips it
//
// R4 is the finding's scope limit: `Client.SubmitActions` returns early on zero
// events, so a total drop is unreachable for THIS producer even after the
// refusal fix. Refusal closes the contract for other producers; it cannot
// close a path the collector never walks. MEASURED, not assumed.

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/crperdue73/AEGIS-Watchman/internal/schema"
)

// refusingSubmitter refuses the first N batches exactly the way the HTTP client
// would after a `400` fix: a non-nil error, nothing else.
type refusingSubmitter struct {
	refuseN  int
	calls    int
	accepted [][]string
}

func (r *refusingSubmitter) SubmitActions(_ context.Context, req ActionsRequest) error {
	r.calls++
	if r.refuseN > 0 {
		r.refuseN--
		return errors.New("central returned 400 Bad Request (accepted:0)")
	}
	var ids []string
	for _, ev := range req.Events {
		if ev.Rule != nil {
			ids = append(ids, ev.Rule.ID)
		}
	}
	r.accepted = append(r.accepted, ids)
	return nil
}

func newTestSpool(t *testing.T) *Spool {
	t.Helper()
	dir := t.TempDir()
	return NewSpool(filepath.Join(dir, "spool.jsonl"), 0)
}

// R1 + R2 -- a refusal preserves the batch AND it rides the next drain.
func TestRefusalPreservesAndRetries(t *testing.T) {
	s := newTestSpool(t)
	if err := s.Append(schema.Host{ID: "h1"}, schema.Agent{ID: "a1"}, testEvents("rule-a", "rule-b")); err != nil {
		t.Fatalf("append: %v", err)
	}
	sub := &refusingSubmitter{refuseN: 1}

	shipped, err := s.Drain(context.Background(), sub.SubmitActions)
	if err != nil {
		t.Fatalf("drain with a refusal should not propagate an error to the caller: %v", err)
	}
	if shipped != 0 {
		t.Fatalf("R1: a refused batch was counted as shipped (%d)", shipped)
	}
	n, err := s.Len()
	if err != nil {
		t.Fatalf("len: %v", err)
	}
	if n != 1 {
		t.Fatalf("R1: refused batch was DELETED -- spool has %d records, want 1 "+
			"(the drop moved, it was not closed)", n)
	}

	shipped, err = s.Drain(context.Background(), sub.SubmitActions)
	if err != nil {
		t.Fatalf("retry drain: %v", err)
	}
	if shipped != 2 {
		t.Fatalf("R2: retried batch shipped %d events, want 2", shipped)
	}
	if len(sub.accepted) != 1 || len(sub.accepted[0]) != 2 {
		t.Fatalf("R2: accepted = %v, want one batch of 2", sub.accepted)
	}
	if n, _ := s.Len(); n != 0 {
		t.Fatalf("R2: spool not emptied after acceptance: %d", n)
	}
}

// R3 -- a refusal does not spin: one submit attempt per drain call, no loop.
func TestRefusalDoesNotSpin(t *testing.T) {
	s := newTestSpool(t)
	if err := s.Append(schema.Host{ID: "h1"}, schema.Agent{ID: "a1"}, testEvents("r1")); err != nil {
		t.Fatalf("append: %v", err)
	}
	if err := s.Append(schema.Host{ID: "h1"}, schema.Agent{ID: "a1"}, testEvents("r2")); err != nil {
		t.Fatalf("append: %v", err)
	}
	sub := &refusingSubmitter{refuseN: 999}

	if _, err := s.Drain(context.Background(), sub.SubmitActions); err != nil {
		t.Fatalf("drain: %v", err)
	}
	if sub.calls != 1 {
		t.Fatalf("R3: refusing submitter called %d times in one drain, want exactly 1 "+
			"(a tight retry loop would be a hot spin against a 400)", sub.calls)
	}
	if n, _ := s.Len(); n != 2 {
		t.Fatalf("R3: spool lost batches while refusing: %d, want 2", n)
	}
}

// R4 -- the scope limit: the collector's client never sends a zero-event batch,
// so the total-drop path is unreachable for THIS producer even after a fix.
//
// BEAT 752 CORRECTION: my first version of this test built `&Client{}` -- zero
// value, nil HTTP -- which panicked on the mutant instead of failing cleanly.
// A PANIC IS NOT A VERDICT: it proves the test broke, not that the defect it
// names was caught. Rebuilt against a real server so the only thing that can
// move is whether central was CONTACTED.
func TestClientSkipsEmptyBatch(t *testing.T) {
	var hits int
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits++
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusAccepted)
		_, _ = w.Write([]byte(`{"accepted":0}`))
	}))
	defer srv.Close()

	c := NewClient(srv.URL + "/api/v1/events")
	if err := c.SubmitActions(context.Background(), ActionsRequest{Host: schema.Host{ID: "h1"}}); err != nil {
		t.Fatalf("R4: empty batch should be a no-op: %v", err)
	}
	if hits != 0 {
		t.Fatalf("R4: an EMPTY batch reached central (%d requests) -- the total-drop "+
			"path is reachable from the collector, and finding 2026-11-02g's scope is wider "+
			"than measured", hits)
	}

	// Control: the same client DOES send when there is something to send, so a
	// zero hit count means "skipped", never "the client is broken".
	if err := c.SubmitActions(context.Background(), ActionsRequest{
		Host: schema.Host{ID: "h1"}, Events: testEvents("r1"),
	}); err != nil {
		t.Fatalf("R4 control: non-empty batch failed: %v", err)
	}
	if hits != 1 {
		t.Fatalf("R4 control: expected exactly 1 request for a 1-event batch, got %d "+
			"(the zero in the first half is meaningless if the client never posts at all)", hits)
	}
}

// R5 -- guard: the spool still writes before attempting the network call, so a
// process death mid-send leaves evidence on disk (the promise's first half).
func TestSpoolAppendsBeforeSend(t *testing.T) {
	s := newTestSpool(t)
	if err := s.Append(schema.Host{ID: "h1"}, schema.Agent{ID: "a1"}, testEvents("x")); err != nil {
		t.Fatalf("append: %v", err)
	}
	if _, err := os.Stat(filepath.Join(filepath.Dir(s.path), filepath.Base(s.path))); err != nil {
		t.Fatalf("R5: spool file does not exist before any drain: %v", err)
	}
}

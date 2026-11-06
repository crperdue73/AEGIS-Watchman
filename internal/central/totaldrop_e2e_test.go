package central_test

// END-TO-END PROOF OF THE FINDING 2026-11-02g FIX (2026-11-06, beat 836).
//
// The finding was measured in pieces: `spoolackcensus.py` proved the SPOOL
// deletes on a nil submit, `actions_test.go` proved the HANDLER answered 202 on
// a total drop, and `refusal_test.go` proved the spool does the right thing
// with a non-nil error. Every one of those used a double on the other side.
//
// Not one of them put the real Spool, the real Client, and the real handler in a
// line and asked the question the product actually answers: *after a batch that
// central could not use, is the evidence still on disk?*
//
// This file does exactly that, and nothing here is stubbed except the listener
// address. If this test goes red, the contract is broken in the way that costs
// evidence -- so it is the one that matters.

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"testing"

	"github.com/crperdue73/AEGIS-Watchman/internal/central"
	"github.com/crperdue73/AEGIS-Watchman/internal/collector"
	"github.com/crperdue73/AEGIS-Watchman/internal/schema"
)

// unnamedEvents is the payload that makes central retain nothing: well-formed
// JSON, real events with no `event` name. Deliberately NOT an empty batch --
// an empty batch never leaves the collector (R4 in refusal_test.go), so it
// would test a path the finding cannot reach.
func unnamedEvents() []schema.Event {
	return []schema.Event{{Severity: "high"}, {Severity: "low"}}
}

func TestE2ETotalDropKeepsEvidenceOnDisk(t *testing.T) {
	svc := central.NewService(central.NewMemoryStore())
	srv := httptest.NewServer(svc.Handler())
	defer srv.Close()

	dir := t.TempDir()
	spoolPath := filepath.Join(dir, "actions.jsonl")
	sp := collector.NewSpool(spoolPath, 1<<20)

	if err := sp.Append(schema.Host{ID: "h1"}, schema.Agent{ID: "a1"}, unnamedEvents()); err != nil {
		t.Fatalf("append: %v", err)
	}

	// The REAL client, pointed at the REAL handler.
	client := collector.NewClient(srv.URL + "/api/v1/actions")

	// Drain exactly as BehaviorEngine.drain does. Its return value is not
	// interesting; what matters is what is on disk afterwards.
	if _, err := sp.Drain(context.Background(), client.SubmitActions); err != nil {
		t.Fatalf("drain returned a caller-level error: %v -- a refusal must be absorbed "+
			"as \"paused\", not propagated as a drain failure", err)
	}

	n, err := sp.Len()
	if err != nil {
		t.Fatalf("len: %v", err)
	}
	if n != 1 {
		t.Fatalf("EVIDENCE LOSS: the spool holds %d batch(es) after central retained none, "+
			"want 1. This is finding 2026-11-02g verbatim: a batch nobody stored was "+
			"deleted on the strength of a response that certified nothing.", n)
	}

	// And the counter on the central side rose, so the loss is visible even
	// while the batch is preserved. Refusing a drop must not make it invisible.
	if got := svc.DroppedUnnamed(); got != 2 {
		t.Errorf("dropped_unnamed = %d, want 2 -- refusal must not replace counting", got)
	}

	// CONTROL: the same spool, the same client, a batch central CAN use. Without
	// this, "still on disk" could mean "the client never sent anything".
	controlPath := filepath.Join(dir, "control.jsonl")
	csp := collector.NewSpool(controlPath, 1<<20)
	if err := csp.Append(schema.Host{ID: "h1"}, schema.Agent{ID: "a1"}, []schema.Event{
		{Event: "behavior.rule_match", Severity: "high"},
	}); err != nil {
		t.Fatalf("control append: %v", err)
	}
	shipped, err := csp.Drain(context.Background(), client.SubmitActions)
	if err != nil {
		t.Fatalf("control drain: %v", err)
	}
	if shipped != 1 {
		t.Fatalf("control shipped %d, want 1 -- the wire works, so the preserved batch "+
			"above is a REFUSAL and not a dead client", shipped)
	}
	if n, _ := csp.Len(); n != 0 {
		t.Fatalf("control spool not emptied after acceptance: %d", n)
	}
}

// The wire shape of the refusal, asserted at the HTTP layer rather than through
// the service, because a collector reading this response is what the contract is
// for. A 400 that did not say how much was dropped would be a refusal a caller
// cannot act on.
func TestE2ETotalDropResponseIsActionable(t *testing.T) {
	svc := central.NewService(central.NewMemoryStore())
	srv := httptest.NewServer(svc.Handler())
	defer srv.Close()

	client := collector.NewClient(srv.URL + "/api/v1/actions")
	err := client.SubmitActions(context.Background(), collector.ActionsRequest{
		Host:   schema.Host{ID: "h1"},
		Events: unnamedEvents(),
	})
	if err == nil {
		t.Fatal("client treated a total drop as success -- this is the whole finding")
	}

	// Raw check of the body, bypassing the client's error formatting.
	body, err := json.Marshal(collector.ActionsRequest{Host: schema.Host{ID: "h1"}, Events: unnamedEvents()})
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	resp, rawErr := http.Post(srv.URL+"/api/v1/actions", "application/json", bytes.NewReader(body))
	if rawErr != nil {
		t.Fatalf("post: %v", rawErr)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400 (not 5xx: a 5xx makes the spool retry a poison "+
			"batch forever)", resp.StatusCode)
	}
	var out struct {
		Error    string `json:"error"`
		Accepted int    `json:"accepted"`
		Received int    `json:"received"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		t.Fatalf("decode refusal body: %v", err)
	}
	if out.Accepted != 0 || out.Received != 2 || out.Error == "" {
		t.Fatalf("refusal body = %+v, want error set, accepted=0, received=2 -- a refusal "+
			"that does not say what it refused is not actionable", out)
	}
}

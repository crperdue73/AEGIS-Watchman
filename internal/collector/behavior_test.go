package collector

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/CRPerdue/agentids/internal/schema"
)

type fakeSubmitter struct {
	got  ActionsRequest
	err  error
	call int
}

func (f *fakeSubmitter) SubmitActions(_ context.Context, req ActionsRequest) error {
	f.call++
	f.got = req
	return f.err
}

// writeAuditChain appends a CARBONATO process chain to an audit log.
func writeAuditChain(t *testing.T, path string) {
	t.Helper()
	lines := `type=SYSCALL msg=audit(1790597000.100:900): arch=x86_64 syscall=execve success=yes pid=4242 ppid=1 uid=0 auid=1000 exe="/usr/bin/bash"
type=EXECVE msg=audit(1790597000.100:900): argc=3 a0="bash" a1="-c" a2="cat ~/.ssh/id_ed25519"
type=SYSCALL msg=audit(1790597000.200:901): arch=x86_64 syscall=execve success=yes pid=4243 ppid=4242 uid=0 auid=1000 exe="/usr/bin/nohup"
type=EXECVE msg=audit(1790597000.200:901): argc=2 a0="nohup" a1="./beacon"
`
	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	if _, err := f.WriteString(lines); err != nil {
		t.Fatal(err)
	}
}

func TestBehaviorEngine_PollShipsMatches(t *testing.T) {
	tmp := t.TempDir()
	audit := filepath.Join(tmp, "audit.log")
	if err := os.WriteFile(audit, nil, 0o644); err != nil {
		t.Fatal(err)
	}
	cfg := &Config{Behavior: Behavior{RulesDir: "../../rules", AuditLog: audit}}
	sub := &fakeSubmitter{}
	be, err := NewBehaviorEngine(cfg, schema.Host{ID: "h1"}, &schema.Agent{ID: "a1"}, sub)
	if err != nil {
		t.Fatalf("NewBehaviorEngine: %v", err)
	}

	// First poll: nothing appended yet -> no events, no submission.
	if evs := be.Poll(); len(evs) != 0 {
		t.Fatalf("expected no events before activity, got %d", len(evs))
	}
	if sub.call != 0 {
		t.Fatalf("submitter called with nothing to send")
	}

	writeAuditChain(t, audit)
	evs := be.Poll()
	if len(evs) == 0 {
		t.Fatal("expected rule matches from the CARBONATO chain")
	}
	if sub.call != 1 {
		t.Fatalf("want 1 submission, got %d", sub.call)
	}
	if len(sub.got.Events) != len(evs) {
		t.Errorf("submitted %d events, polled %d", len(sub.got.Events), len(evs))
	}
	if sub.got.Host.ID != "h1" || sub.got.Agent.ID != "a1" {
		t.Errorf("submission attribution = %+v / %+v", sub.got.Host, sub.got.Agent)
	}

	// Second poll with no new bytes: quiet, no second submission.
	if evs := be.Poll(); len(evs) != 0 {
		t.Errorf("expected no new events on unchanged log, got %d", len(evs))
	}
	if sub.call != 1 {
		t.Errorf("submitter called again with no new data (call=%d)", sub.call)
	}
}

func TestBehaviorEngine_SubmitErrorDoesNotPanic(t *testing.T) {
	tmp := t.TempDir()
	audit := filepath.Join(tmp, "audit.log")
	if err := os.WriteFile(audit, nil, 0o644); err != nil {
		t.Fatal(err)
	}
	cfg := &Config{Behavior: Behavior{RulesDir: "../../rules", AuditLog: audit}}
	sub := &fakeSubmitter{err: context.DeadlineExceeded}
	be, err := NewBehaviorEngine(cfg, schema.Host{ID: "h1"}, nil, sub)
	if err != nil {
		t.Fatalf("NewBehaviorEngine: %v", err)
	}
	writeAuditChain(t, audit)
	// A central outage must log-and-continue, not crash the collector.
	if evs := be.Poll(); len(evs) == 0 {
		t.Fatal("events should still be returned even if shipping fails")
	}
}

// TestBehaviorEngine_ObserveDriftFeedsTimeline covers the wiring that makes
// correlation rules possible on a live collector: Layer 1/2 drift events must
// land in the same timeline Layer 3 consults, or "egress after identity change"
// can never fire.
func TestBehaviorEngine_ObserveDriftFeedsTimeline(t *testing.T) {
	tmp := t.TempDir()
	audit := filepath.Join(tmp, "audit.log")
	if err := os.WriteFile(audit, nil, 0o644); err != nil {
		t.Fatal(err)
	}
	cfg := &Config{Behavior: Behavior{RulesDir: "../../rules", AuditLog: audit}}
	be, err := NewBehaviorEngine(cfg, schema.Host{ID: "h1"}, &schema.Agent{ID: "a1"}, &fakeSubmitter{})
	if err != nil {
		t.Fatalf("NewBehaviorEngine: %v", err)
	}
	if be.timeline == nil {
		t.Fatal("behavior engine must own a timeline")
	}

	if n := be.timeline.Len("h1"); n != 0 {
		t.Fatalf("timeline should start empty, len=%d", n)
	}
	be.ObserveDrift([]schema.Event{{
		Event: schema.EventIdentityDrift,
		Host:  schema.Host{ID: "h1"},
	}})
	if n := be.timeline.Len("h1"); n != 1 {
		t.Fatalf("drift not recorded into timeline, len=%d", n)
	}

	// Nil engine (Layer 3 off) must no-op rather than panic.
	var nilBE *BehaviorEngine
	nilBE.ObserveDrift([]schema.Event{{Event: schema.EventIdentityDrift, Host: schema.Host{ID: "h1"}}})
}

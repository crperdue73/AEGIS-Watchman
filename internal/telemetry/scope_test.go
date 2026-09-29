package telemetry

import (
	"testing"
	"time"

	"github.com/CRPerdue/agentids/internal/schema"
)

// batchSource is a minimal in-memory Source that returns a fixed batch once.
type batchSource struct {
	batches [][]Event
	i       int
}

func (b *batchSource) Actions() ([]Event, error) {
	if b.i >= len(b.batches) {
		return nil, nil
	}
	out := b.batches[b.i]
	b.i++
	return out, nil
}

func listenEvent(addr string, pid int) Event {
	p := pid
	return Event{
		Action: schema.Action{Type: NetListen, Detail: map[string]any{"listen": addr, "exe": "/home/agent/bin/beacon"}},
		Actor:  &schema.Actor{PID: &p},
	}
}

func egressEvent(service, endpoint string) Event {
	return Event{
		Action: schema.Action{Type: NetEgress, Detail: map[string]any{"service": service, "endpoint": endpoint}},
		Actor:  &schema.Actor{},
	}
}

func execEvent(exe string) Event {
	return Event{
		Action: schema.Action{Type: ProcessExec, Detail: map[string]any{"exe": exe, "argv": []any{exe}}},
		Actor:  &schema.Actor{},
	}
}

// fire: a first-ever listener is reported as a heuristic scope.change.
func TestScopeSourceEmitsNovelListener(t *testing.T) {
	inner := &batchSource{batches: [][]Event{{
		listenEvent("0.0.0.0:4444", 42),
	}}}
	ss := NewScopeSource(inner, nil)

	evs, err := ss.Actions()
	if err != nil {
		t.Fatalf("Actions: %v", err)
	}
	// the original net.listen, preserved, plus one derived scope.change.
	if len(evs) != 2 {
		t.Fatalf("want 2 events (listen + scope.change), got %d", len(evs))
	}
	if evs[0].Action.Type != NetListen {
		t.Fatalf("inner event must be preserved first, got %s", evs[0].Action.Type)
	}
	sc := evs[1].Action
	if sc.Type != ScopeChange {
		t.Fatalf("derived type = %q, want %q", sc.Type, ScopeChange)
	}
	if sc.Detail["source"] != ScopeSourceHeuristic {
		t.Errorf("detail.source = %v, want %q (a derived change must be labelled)", sc.Detail["source"], ScopeSourceHeuristic)
	}
	if sc.Detail["novel"] != "true" {
		t.Errorf("detail.novel = %v, want \"true\" (the rule keys on this)", sc.Detail["novel"])
	}
	if sc.Detail["kind"] != ScopeKindListener {
		t.Errorf("detail.kind = %v, want %q", sc.Detail["kind"], ScopeKindListener)
	}
	if sc.Detail["value"] != "0.0.0.0:4444" {
		t.Errorf("detail.value = %v, want the listener address", sc.Detail["value"])
	}
}

// quiet: the same listener seen again is not a change — otherwise every poll
// after the first is a false alarm and the rule gets tuned off.
func TestScopeSourceQuietOnRepeat(t *testing.T) {
	inner := &batchSource{batches: [][]Event{
		{listenEvent("0.0.0.0:4444", 42)}, // first: novel
		{listenEvent("0.0.0.0:4444", 42)}, // second: not
	}}
	ss := NewScopeSource(inner, nil)

	// first poll: derived scope.change present
	first, _ := ss.Actions()
	if len(first) != 2 {
		t.Fatalf("first poll: want 2 events, got %d", len(first))
	}
	// second poll: only the original event, no derived change
	second, _ := ss.Actions()
	if len(second) != 1 {
		t.Fatalf("second poll: want 1 event (no repeat scope.change), got %d", len(second))
	}
	if second[0].Action.Type != NetListen {
		t.Fatalf("second poll event = %s, want the preserved %s", second[0].Action.Type, NetListen)
	}
}

// quiet: two sightings of the same capability in ONE batch yield one change.
func TestScopeSourceDeduplicatesWithinBatch(t *testing.T) {
	inner := &batchSource{batches: [][]Event{{
		listenEvent("0.0.0.0:4444", 42),
		listenEvent("0.0.0.0:4444", 42),
	}}}
	ss := NewScopeSource(inner, nil)
	evs, _ := ss.Actions()
	// 2 originals + 1 derived (not 2)
	if len(evs) != 3 {
		t.Fatalf("want 3 events (2 listen + 1 scope.change), got %d", len(evs))
	}
}

// fire: a new named egress service is a capability keyed on the service name,
// not the rotating IP, so the same service twice is not novel twice.
func TestScopeSourceEgressKeyedOnService(t *testing.T) {
	inner := &batchSource{batches: [][]Event{
		{egressEvent("slack.com", "slack.com 1.2.3.4:443")},
		{egressEvent("slack.com", "slack.com 5.6.7.8:443")}, // same service, new IP
	}}
	ss := NewScopeSource(inner, nil)
	first, _ := ss.Actions()
	if len(first) != 2 {
		t.Fatalf("first poll: want 2 (egress + scope.change), got %d", len(first))
	}
	if got := first[1].Action.Detail["value"]; got != "slack.com" {
		t.Errorf("scope value = %v, want service name %q", got, "slack.com")
	}
	second, _ := ss.Actions()
	if len(second) != 1 {
		t.Fatalf("second poll: want 1 (service already seen), got %d", len(second))
	}
}

// fire: a new executable under an agent-owned root is scope expansion.
func TestScopeSourceAgentOwnedExec(t *testing.T) {
	inner := &batchSource{batches: [][]Event{{
		execEvent("/home/agent/.local/bin/nmap"),
	}}}
	ss := NewScopeSource(inner, nil)
	evs, _ := ss.Actions()
	if len(evs) != 2 {
		t.Fatalf("want 2 (exec + scope.change), got %d", len(evs))
	}
	if evs[1].Action.Detail["kind"] != ScopeKindExec {
		t.Errorf("kind = %v, want %q", evs[1].Action.Detail["kind"], ScopeKindExec)
	}
}

// quiet: a system binary is not agent scope — /usr/bin/ls appearing is not a
// capability the agent reached for. This is the benign lookalike the rule's
// quiet test needs.
func TestScopeSourcePatientOnSystemExec(t *testing.T) {
	inner := &batchSource{batches: [][]Event{{
		execEvent("/usr/bin/ls"),
		execEvent("ls"),
	}}}
	ss := NewScopeSource(inner, nil)
	evs, _ := ss.Actions()
	if len(evs) != 2 {
		t.Fatalf("want 2 events (no derived change for system binaries), got %d", len(evs))
	}
	for _, e := range evs {
		if e.Action.Type == ScopeChange {
			t.Fatalf("system binary produced a scope.change: %+v", e.Action.Detail)
		}
	}
}

// the shim stamps a deterministic time when Now is set.
func TestScopeSourceStampsTime(t *testing.T) {
	fixed := time.Date(2026, 9, 29, 12, 0, 0, 0, time.UTC)
	inner := &batchSource{batches: [][]Event{{listenEvent("127.0.0.1:9000", 7)}}}
	ss := NewScopeSource(inner, nil)
	ss.Now = func() time.Time { return fixed }
	evs, _ := ss.Actions()
	if got := evs[1].Action.Detail["since"]; got != "2026-09-29T12:00:00Z" {
		t.Errorf("since = %v, want the stamped time", got)
	}
}

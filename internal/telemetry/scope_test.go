package telemetry

import (
	"encoding/json"
	"os"
	"path/filepath"
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

func countScopeChanges(evs []Event) int {
	n := 0
	for _, e := range evs {
		if e.Action.Type == ScopeChange {
			n++
		}
	}
	return n
}

// A restart must not re-report the host's established capabilities as novel.
// The set is persisted on the first sighting; a second source over the same
// file is quiet for the same capability.
func TestScopeSourcePersistsSeenAcrossRestart(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "scope-seen.json")

	first := NewScopeSource(&batchSource{batches: [][]Event{{listenEvent("0.0.0.0:4444", 42)}}}, nil)
	if err := first.PersistSeen(path); err != nil {
		t.Fatalf("PersistSeen (fresh): %v", err)
	}
	evs, err := first.Actions()
	if err != nil {
		t.Fatalf("first Actions: %v", err)
	}
	if got := countScopeChanges(evs); got != 1 {
		t.Fatalf("first run: want 1 scope.change, got %d", got)
	}
	if _, err := os.Stat(path); err != nil {
		t.Fatalf("seen file not written: %v", err)
	}

	// Restart: a new source over the same state file, seeing the SAME listener.
	second := NewScopeSource(&batchSource{batches: [][]Event{{listenEvent("0.0.0.0:4444", 42)}}}, nil)
	if err := second.PersistSeen(path); err != nil {
		t.Fatalf("PersistSeen (restart): %v", err)
	}
	evs2, err := second.Actions()
	if err != nil {
		t.Fatalf("second Actions: %v", err)
	}
	if got := countScopeChanges(evs2); got != 0 {
		t.Fatalf("after restart the same listener was re-reported: got %d scope.change(s)", got)
	}

	// A genuinely new capability on the restarted source still fires.
	second.inner = &batchSource{batches: [][]Event{{egressEvent("slack.com", "slack.com 1.2.3.4:443")}}}
	evs3, _ := second.Actions()
	if got := countScopeChanges(evs3); got != 1 {
		t.Fatalf("a new capability after restart did not fire: got %d", got)
	}
}

func TestScopeSourcePersistSeenMissingFileIsFresh(t *testing.T) {
	path := filepath.Join(t.TempDir(), "nope", "scope-seen.json")
	s := NewScopeSource(&batchSource{batches: [][]Event{{listenEvent("0.0.0.0:1", 1)}}}, nil)
	if err := s.PersistSeen(path); err != nil {
		t.Fatalf("a missing seen file must be a fresh start, got: %v", err)
	}
	if s.Seen() != 0 {
		t.Fatalf("fresh source already has %d seen keys", s.Seen())
	}
}

// A corrupt cache is discarded, not fatal: the shim keeps working and rewrites
// the file on the next new capability.
func TestScopeSourcePersistSeenCorruptFileRecovers(t *testing.T) {
	path := filepath.Join(t.TempDir(), "scope-seen.json")
	if err := os.WriteFile(path, []byte("{not json"), 0o600); err != nil {
		t.Fatal(err)
	}
	s := NewScopeSource(&batchSource{batches: [][]Event{{listenEvent("0.0.0.0:4444", 42)}}}, nil)
	if err := s.PersistSeen(path); err == nil {
		t.Fatalf("a corrupt seen file should be reported (and then ignored)")
	}
	evs, err := s.Actions()
	if err != nil {
		t.Fatalf("Actions after a corrupt cache: %v", err)
	}
	if got := countScopeChanges(evs); got != 1 {
		t.Fatalf("a capability after a corrupt cache should still fire: got %d", got)
	}
	// The file now holds valid JSON again.
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var keys []string
	if err := json.Unmarshal(data, &keys); err != nil {
		t.Fatalf("seen file was not rewritten valid: %v", err)
	}
}

// The set is bounded: past the cap the oldest observation is evicted, so a
// long-untouched capability may re-fire once rather than the set growing
// without limit.
func TestScopeSourceSeenCapEvictsOldest(t *testing.T) {
	s := NewScopeSource(&batchSource{}, nil)
	s.maxSeen = 3
	for _, addr := range []string{"0.0.0.0:1", "0.0.0.0:2", "0.0.0.0:3", "0.0.0.0:4"} {
		s.inner = &batchSource{batches: [][]Event{{listenEvent(addr, 1)}}}
		s.Actions()
	}
	if s.Seen() != 3 {
		t.Fatalf("seen set not capped: got %d, want 3", s.Seen())
	}
	// The oldest (0.0.0.0:1) was evicted, so it is novel again.
	s.inner = &batchSource{batches: [][]Event{{listenEvent("0.0.0.0:1", 1)}}}
	evs, _ := s.Actions()
	if got := countScopeChanges(evs); got != 1 {
		t.Fatalf("the evicted-oldest capability should re-fire once: got %d", got)
	}
}

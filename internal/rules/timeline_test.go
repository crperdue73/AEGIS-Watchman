package rules

import (
	"testing"
	"time"

	"github.com/CRPerdue/watchman/internal/schema"
)

var t0 = time.Unix(1790600000, 0).UTC()

func TestTimelineRecordAndSeen(t *testing.T) {
	tl := NewTimeline()
	tl.Record("h1", t0, "identity.drift", t0)

	if !tl.Seen("h1", "identity.drift", 5*time.Minute, t0.Add(time.Minute)) {
		t.Error("drift within window should be seen")
	}
	if tl.Seen("h1", "identity.drift", 5*time.Minute, t0.Add(10*time.Minute)) {
		t.Error("drift outside window should not be seen")
	}
	if tl.Seen("h2", "identity.drift", time.Hour, t0.Add(time.Minute)) {
		t.Error("other host must not see the drift")
	}
	if tl.Seen("h1", "process.exec", time.Hour, t0.Add(time.Minute)) {
		t.Error("other class must not match")
	}
}

func TestTimelineNoFuture(t *testing.T) {
	tl := NewTimeline()
	// An observation stamped AFTER asOf must not count as "prior".
	tl.Record("h1", t0.Add(time.Minute), "identity.drift", t0.Add(time.Minute))
	if tl.Seen("h1", "identity.drift", time.Hour, t0) {
		t.Error("an event in the future must not satisfy a prior predicate")
	}
}

func TestTimelineEvictsExpired(t *testing.T) {
	tl := NewTimelineSized(time.Minute, 8)
	tl.Record("h1", t0, "a", t0)
	if tl.Len("h1") != 1 {
		t.Fatalf("len = %d, want 1", tl.Len("h1"))
	}
	// A later record outside the window evicts the old one.
	tl.Record("h1", t0.Add(2*time.Minute), "b", t0.Add(2*time.Minute))
	if tl.Len("h1") != 1 {
		t.Errorf("expired entry not evicted: len = %d", tl.Len("h1"))
	}
	if tl.Seen("h1", "a", 0, t0.Add(2*time.Minute)) {
		t.Error("expired 'a' should be gone")
	}
}

func TestTimelineCapTrimsOldest(t *testing.T) {
	tl := NewTimelineSized(time.Hour, 3)
	for i := 0; i < 5; i++ {
		tl.Record("h1", t0.Add(time.Duration(i)*time.Second), "x", t0.Add(time.Duration(i)*time.Second))
	}
	if tl.Len("h1") != 3 {
		t.Fatalf("cap not enforced: len = %d, want 3", tl.Len("h1"))
	}
}

func TestTimelinePrune(t *testing.T) {
	tl := NewTimelineSized(time.Minute, 8)
	tl.Record("h1", t0, "a", t0)
	tl.Record("h2", t0.Add(90*time.Second), "b", t0.Add(90*time.Second))
	tl.Prune(t0.Add(2 * time.Minute))
	if tl.Len("h1") != 0 {
		t.Errorf("h1 should be pruned, len = %d", tl.Len("h1"))
	}
	if tl.Len("h2") != 1 {
		t.Errorf("h2 should survive, len = %d", tl.Len("h2"))
	}
}

func TestTimelineObserveEvent(t *testing.T) {
	tl := NewTimeline()
	ev := schema.Event{Event: schema.EventIdentityDrift, Host: schema.Host{ID: "h1"}, TS: t0}
	tl.ObserveEvent(ev, t0.Add(time.Second))
	if !tl.Seen("h1", "identity.drift", time.Hour, t0.Add(time.Minute)) {
		t.Error("observed event not recorded")
	}
	// Zero TS falls back to asOf.
	ev2 := schema.Event{Event: schema.EventIdentityDrift, Host: schema.Host{ID: "h1"}}
	tl.ObserveEvent(ev2, t0)
	if !tl.Seen("h1", "identity.drift", time.Hour, t0) {
		t.Error("zero-TS event should default to asOf")
	}
}

func TestTimelineNilSafe(t *testing.T) {
	var tl *Timeline
	tl.Record("h1", t0, "a", t0)
	tl.Prune(t0)
	if tl.Len("h1") != 0 || tl.Seen("h1", "a", time.Hour, t0) {
		t.Error("nil timeline must be inert")
	}
}

// TestCorrelationEgressAfterIdentityChange is the brief's canonical temporal
// rule, end to end: an outbound call only matters when an identity file changed
// shortly before it. Same action, two timelines, two verdicts.
func TestCorrelationEgressAfterIdentityChange(t *testing.T) {
	pack := mustPack(t, `
name: carbonato
version: "2"
rules:
  - id: carbonato.exfil.egress_after_identity_change
    severity: critical
    technique: phase3/correlated-exfil
    match:
      type: net.egress
      endpoint_glob_any: ["*slack.com*", "*api.telegram.org*"]
      prior_event:
        class: identity.drift
        within: 5m
`)
	eng := NewEngine(pack)
	action := schema.Action{Type: "net.egress", Detail: map[string]any{"endpoint": "hooks.slack.com:443"}}
	host := schema.Host{ID: "h1"}

	// With a recent identity change on this host -> fires.
	tl := NewTimeline()
	tl.Record("h1", t0, schema.EventIdentityDrift, t0)
	eng.WithTimeline(tl)
	if evs := eng.Match(host, nil, action, nil, t0.Add(time.Minute)); len(evs) != 1 {
		t.Fatalf("expected correlated fire, got %+v", evs)
	}

	// Same action, no prior change -> silent.
	eng.WithTimeline(NewTimeline())
	if evs := eng.Match(host, nil, action, nil, t0.Add(time.Minute)); len(evs) != 0 {
		t.Fatalf("no prior change should not fire, got %+v", evs)
	}

	// Same action, change happened too long ago -> silent.
	tl2 := NewTimeline()
	tl2.Record("h1", t0, schema.EventIdentityDrift, t0)
	eng.WithTimeline(tl2)
	if evs := eng.Match(host, nil, action, nil, t0.Add(10*time.Minute)); len(evs) != 0 {
		t.Fatalf("stale prior change should not fire, got %+v", evs)
	}

	// No timeline attached at all -> correlation rule cannot fire.
	eng.WithTimeline(nil)
	if evs := eng.Match(host, nil, action, nil, t0.Add(time.Minute)); len(evs) != 0 {
		t.Fatalf("no timeline should not fire, got %+v", evs)
	}
}

func TestPriorEventValidation(t *testing.T) {
	base := func(pe string) string {
		return "name: p\nrules:\n  - id: p.r\n    severity: high\n    match:\n      type: net.egress\n      prior_event:" + pe
	}
	if _, err := ParsePack([]byte(base("\n        within: 5m"))); err == nil {
		t.Error("missing class should be rejected")
	}
	if _, err := ParsePack([]byte(base("\n        class: identity.drift"))); err == nil {
		t.Error("missing within should be rejected")
	}
	if _, err := ParsePack([]byte(base("\n        class: identity.drift\n        within: 0s"))); err == nil {
		t.Error("non-positive within should be rejected")
	}
	if _, err := ParsePack([]byte(base("\n        class: identity.drift\n        within: 5m"))); err != nil {
		t.Errorf("valid prior_event rejected: %v", err)
	}
}

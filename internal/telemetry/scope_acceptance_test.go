package telemetry

import (
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/CRPerdue/agentids/internal/rules"
	"github.com/CRPerdue/agentids/internal/schema"
)

// TestScopeChangeFiresPackRule closes the P1.3 acceptance criterion end to end
// at the unit level: the scope shim's derived action, fed through the REAL
// carbonato rule pack, fires carbonato.tool.scope_new — and a benign, repeated
// action does not. It runs against the shipped pack on disk, not a fixture, so
// the rule and the shim cannot drift apart in a test that still passes.
func TestScopeChangeFiresPackRule(t *testing.T) {
	packPath := filepath.Join("..", "..", "rules", "carbonato.yaml")
	if _, err := os.Stat(packPath); err != nil {
		t.Skipf("rule pack not found at %s: %v", packPath, err)
	}
	pack, err := rules.LoadPack(packPath)
	if err != nil {
		t.Fatalf("load pack: %v", err)
	}
	eng := rules.NewEngine(pack)

	host := schema.Host{ID: "h1", Name: "test"}
	at := time.Date(2026, 9, 29, 12, 0, 0, 0, time.UTC)

	// The shim derives a scope.change from a first-ever listener.
	inner := &batchSource{batches: [][]Event{{
		listenEvent("0.0.0.0:4444", 42),
	}}}
	ss := NewScopeSource(inner, &schema.Agent{ID: "agent-1", Root: "/home/agent"})
	ss.Now = func() time.Time { return at }
	evs, err := ss.Actions()
	if err != nil {
		t.Fatalf("scope Actions: %v", err)
	}
	if len(evs) != 2 {
		t.Fatalf("want 2 events, got %d", len(evs))
	}
	derived := evs[1].Action
	if derived.Type != ScopeChange {
		t.Fatalf("derived type = %q, want %q", derived.Type, ScopeChange)
	}

	matches := eng.Match(host, &schema.Agent{ID: "agent-1", Root: "/home/agent"}, derived, evs[1].Actor, at)
	if !hasRule(matches, "carbonato.tool.scope_new") {
		t.Fatalf("derived scope.change did NOT fire carbonato.tool.scope_new; matches=%v", ruleIDs(matches))
	}

	// Quiet: a second poll of the same listener yields no scope.change, so
	// nothing for the rule to fire on.
	second, _ := ss.Actions()
	for _, e := range second {
		if e.Action.Type == ScopeChange {
			t.Fatalf("repeat listener produced a scope.change: %+v", e.Action.Detail)
		}
		m := eng.Match(host, &schema.Agent{ID: "agent-1"}, e.Action, e.Actor, at)
		if hasRule(m, "carbonato.tool.scope_new") {
			t.Fatalf("benign repeat fired carbonato.tool.scope_new: %+v", e.Action)
		}
	}
}

func hasRule(evs []schema.Event, id string) bool {
	for _, e := range evs {
		if e.Rule != nil && e.Rule.ID == id {
			return true
		}
	}
	return false
}

func ruleIDs(evs []schema.Event) []string {
	var out []string
	for _, e := range evs {
		if e.Rule != nil {
			out = append(out, e.Rule.ID)
		}
	}
	return out
}

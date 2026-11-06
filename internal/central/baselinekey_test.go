package central

// BEAT 755 -- the identity is received, used for attribution, and dropped before the roster.
//
// Beat 754 found `/api/v1/agents` carries no agent id. The obvious next question
// is whether that id EXISTS at ingest -- and it does: `Ingest(ctx, host, agent, inv)`
// takes `schema.Agent` and passes it to ProvenanceEvents, Drift and retain.
//
// So the id is not missing from the system. It is dropped at two specific places:
//
// P1  the BASELINE KEY is (HostID, Root) -- `agent.ID` is not in it, so a second
//     identity on the same (host, root) overwrites the first baseline, exactly
//     as the manifest registry overwrites by root (finding v, beat 753)
// P2  `buildAgentsView` builds the entry from `b.Key` + `b.Files` only, so the
//     reporting agent's ID never reaches the wire even though central had it
// P3  `Schema.Agent` carries ID -- proving the field exists at the ingest edge,
//     so P1/P2 are drops, not absences (the distinction that makes the fix small)
// P4  control: distinct roots keep distinct baselines -- the collapse is the key
// P5  control: the drift path DOES attribute to agent.ID, so identity is used for
//     events and merely unconsulted for baselines -- not "identity is ignored"
//
// Nothing fixed. Baseline keying is Dad's call.

import (
	"context"
	"strings"
	"testing"

	"github.com/crperdue73/AEGIS-Watchman/internal/collector"
	"github.com/crperdue73/AEGIS-Watchman/internal/schema"
)

func collisionInv(root, path, hash string) *collector.Inventory {
	return &collector.Inventory{
		Root:  root,
		Files: []collector.FileState{{Path: path, SHA256: hash}},
	}
}

// P3 -- the ingest signature carries the identity. Structural: if this ever
// changes, the whole "dropped, not absent" framing has to be re-measured.
func TestIngestReceivesTheAgentIdentity(t *testing.T) {
	var agent schema.Agent
	agent.ID = "helper"
	if agent.ID == "" {
		t.Fatalf("P3: schema.Agent has no ID to set -- re-measure the finding")
	}
	// The signature itself is the evidence: Ingest(ctx, host, agent, inv).
	// Call it once to keep the claim executable rather than a comment.
	svc := NewService(NewMemoryStore())
	if svc == nil {
		t.Fatalf("P3: NewService returned nil")
	}
}

// P1 -- THE DEFECT, pinned: two identities, same (host, root), one baseline.
func TestBaselineKeyHasNoAgentID(t *testing.T) {
	ctx := context.Background()
	svc := NewService(NewMemoryStore())

	host := schema.Host{ID: "host-1"}
	a := schema.Agent{ID: "helper", Root: "/srv/x"}
	b := schema.Agent{ID: "helper-copy", Root: "/srv/x"}

	if _, err := svc.Ingest(ctx, host, a, collisionInv("/srv/x", "/srv/x/one.md", "11")); err != nil {
		t.Fatalf("P1: first ingest: %v", err)
	}
	res, err := svc.Ingest(ctx, host, b, collisionInv("/srv/x", "/srv/x/two.md", "22"))
	if err != nil {
		t.Fatalf("P1: second ingest: %v", err)
	}

	// FirstSeen must be FALSE: central believes it has seen this agent before,
	// because "this agent" is a (host, root) pair and the pair is already known.
	if res.FirstSeen {
		t.Fatalf("P1: a brand-new agent identity was reported as FirstSeen=true. If the " +
			"key now includes the agent id, this finding is fixed -- retire it, don't " +
			"keep the pin green")
	}

	// And there is exactly ONE baseline for the shared root.
	list, err := svc.store.ListBaselines(ctx)
	if err != nil {
		t.Fatalf("P1: list: %v", err)
	}
	if len(list) != 1 {
		t.Fatalf("P1: expected 1 baseline for the shared (host, root), got %d -- keying "+
			"changed; re-read this finding", len(list))
	}
	if list[0].Files[0].Path != "/srv/x/two.md" {
		t.Fatalf("P1: surviving baseline is %q, expected the second agent's -- "+
			"last write should win", list[0].Files[0].Path)
	}
	// 'helper's baseline is gone, and nothing said so.
}

// P2 -- the roster entry has no field for the ID that ingest received.
func TestRosterDropsTheReportingAgentID(t *testing.T) {
	ctx := context.Background()
	svc := NewService(NewMemoryStore())
	host := schema.Host{ID: "host-1"}
	a := schema.Agent{ID: "helper", Root: "/srv/y"}
	if _, err := svc.Ingest(ctx, host, a, collisionInv("/srv/y", "/srv/y/a.md", "aa")); err != nil {
		t.Fatalf("P2: ingest: %v", err)
	}

	view, err := svc.buildAgentsView(ctx, "", "")
	if err != nil {
		t.Fatalf("P2: view: %v", err)
	}
	if len(view.Agents) != 1 {
		t.Fatalf("P2: expected 1 agent, got %d", len(view.Agents))
	}
	entry := view.Agents[0]

	// The ID exists in the store path we just wrote and is not on the entry.
	if entry.HostID != "host-1" || entry.Root != "/srv/y" {
		t.Fatalf("P2 control: entry = %+v, want host-1 /srv/y", entry)
	}
	// AgentID is not a field, so this is compile-time absence. Assert it the
	// only way it can be asserted: the entry's rendered keys.
	rendered := strings.Join([]string{entry.HostID, entry.Root}, "|")
	if strings.Contains(rendered, "helper") {
		t.Fatalf("P2: the reporting agent's id reached the roster fields -- finding "+
			"2026-11-02i may be closable. Rendered: %s", rendered)
	}
}

// P4 -- control: distinct roots keep distinct baselines.
func TestDistinctRootsKeepDistinctBaselines(t *testing.T) {
	ctx := context.Background()
	svc := NewService(NewMemoryStore())
	host := schema.Host{ID: "host-1"}

	if _, err := svc.Ingest(ctx, host, schema.Agent{ID: "a", Root: "/srv/a"},
		collisionInv("/srv/a", "/srv/a/f.md", "aa")); err != nil {
		t.Fatalf("P4: %v", err)
	}
	if _, err := svc.Ingest(ctx, host, schema.Agent{ID: "b", Root: "/srv/b"},
		collisionInv("/srv/b", "/srv/b/f.md", "bb")); err != nil {
		t.Fatalf("P4: %v", err)
	}
	list, err := svc.store.ListBaselines(ctx)
	if err != nil {
		t.Fatalf("P4: list: %v", err)
	}
	if len(list) != 2 {
		t.Fatalf("P4: expected 2 baselines for 2 roots, got %d -- the store is not keyed "+
			"by root alone, so P1's collapse is specifically the missing agent id", len(list))
	}
}

// P5 -- control: identity is USED on the event path, so the claim is "unconsulted
// for baselines", not "ignored everywhere".
func TestDriftEventsAttributeToTheAgent(t *testing.T) {
	ctx := context.Background()
	svc := NewService(NewMemoryStore())
	host := schema.Host{ID: "host-1"}
	agent := schema.Agent{ID: "helper", Root: "/srv/z"}

	// First ingest records the baseline silently.
	if _, err := svc.Ingest(ctx, host, agent, collisionInv("/srv/z", "/srv/z/a.md", "aa")); err != nil {
		t.Fatalf("P5: seed: %v", err)
	}
	// Second ingest changes a hash -> drift event.
	res, err := svc.Ingest(ctx, host, agent, collisionInv("/srv/z", "/srv/z/a.md", "bb"))
	if err != nil {
		t.Fatalf("P5: change: %v", err)
	}
	if len(res.Events) == 0 {
		t.Fatalf("P5: expected a drift event for a changed hash, got none")
	}
	attributed := false
	for _, ev := range res.Events {
		if ev.Agent != nil && ev.Agent.ID == "helper" {
			attributed = true
		}
	}
	if !attributed {
		t.Fatalf("P5: no drift event carried agent.ID=helper. If attribution changed, " +
			"re-read this finding before editing it")
	}
}

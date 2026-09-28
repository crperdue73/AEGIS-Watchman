package central

import (
	"context"
	"sort"

	"github.com/CRPerdue/agentids/internal/collector"
	"github.com/CRPerdue/agentids/internal/schema"
)

// Service is the Phase-1 central engine: it takes an incoming inventory from a
// collector, diffs it against the stored baseline, records the new baseline,
// and returns the resulting drift events.
type Service struct {
	store Store
	// MaxDiffBytes caps the unified diff attached to each event (0 = unlimited).
	MaxDiffBytes int
}

// NewService wires a Service to a Store.
func NewService(store Store) *Service {
	return &Service{store: store, MaxDiffBytes: 4096}
}

// IngestResult is what a collector gets back for one inventory submission.
type IngestResult struct {
	FirstSeen bool           `json:"first_seen"`
	Events    []schema.Event `json:"events"`
}

// Ingest diffs inv against the stored baseline for (host, agent), stores inv as
// the new baseline, and returns the drift events. On the first sighting of an
// agent there is no baseline to diff against: FirstSeen is true and Events is
// empty (the baseline is recorded silently — no false alarm on enrollment).
func (s *Service) Ingest(ctx context.Context, host schema.Host, agent schema.Agent, inv *collector.Inventory) (*IngestResult, error) {
	key := AgentKey{HostID: host.ID, Root: inv.Root}

	baseline, found, err := s.store.GetBaseline(ctx, key)
	if err != nil {
		return nil, err
	}

	var events []schema.Event
	if !found {
		events = []schema.Event{}
	} else {
		events = collector.Drift(host, agent, baseline, inv, s.MaxDiffBytes)
		// Stable ordering: sort by path, then by event class, so output is
		// deterministic regardless of map iteration order in Drift.
		sort.SliceStable(events, func(i, j int) bool {
			pi, pj := pathOf(events[i]), pathOf(events[j])
			if pi != pj {
				return pi < pj
			}
			return events[i].Event < events[j].Event
		})
	}

	if err := s.store.PutBaseline(ctx, key, inv); err != nil {
		return nil, err
	}

	return &IngestResult{FirstSeen: !found, Events: events}, nil
}

func pathOf(e schema.Event) string {
	if e.File != nil {
		return e.File.Path
	}
	return ""
}

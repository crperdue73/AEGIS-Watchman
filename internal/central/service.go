package central

import (
	"context"
	"log"
	"sort"
	"time"

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
	// alerter is notified of drift events after a baseline is stored. Nil
	// disables alerting (the reference behavior in tests and -dry-run setups).
	alerter Alerter
	// logger receives non-fatal alert-delivery failures. Nil is safe.
	logger *log.Logger
}

// Logger is the minimal logging surface config wiring needs, so callers can
// pass *log.Logger (or a test double) without importing log in the config path.
type Logger = *log.Logger

// NewService wires a Service to a Store.
func NewService(store Store) *Service {
	return &Service{store: store, MaxDiffBytes: 4096}
}

// SetAlerter installs an Alerter that is called once per ingest cycle with the
// full set of drift events (a single batched call, not one call per event).
// Passing nil disables alerting.
func (s *Service) SetAlerter(a Alerter) { s.alerter = a }

// SetLogger installs a logger for non-fatal delivery failures.
func (s *Service) SetLogger(l *log.Logger) { s.logger = l }

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

	// Alert after the baseline is durable, so a failed alert never loses the
	// new baseline. Delivery failures are logged, never fatal: the collector
	// still gets its events, and the store is already correct.
	if s.alerter != nil && len(events) > 0 {
		s.alert(ctx, host, agent, events)
	}

	return &IngestResult{FirstSeen: !found, Events: events}, nil
}

func (s *Service) alert(ctx context.Context, host schema.Host, agent schema.Agent, events []schema.Event) {
	a := Alert{When: time.Now().UTC(), Host: host, Agent: agent, Events: events}
	if err := s.alerter.Alert(ctx, a); err != nil && s.logger != nil {
		s.logger.Printf("central: alert delivery failed: %v", err)
	}
}

func pathOf(e schema.Event) string {
	if e.File != nil {
		return e.File.Path
	}
	return ""
}

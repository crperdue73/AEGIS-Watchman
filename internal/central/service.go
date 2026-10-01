package central

import (
	"context"
	"log"
	"sort"
	"time"

	"github.com/crperdue73/AEGIS-Watchman/internal/collector"
	"github.com/crperdue73/AEGIS-Watchman/internal/schema"
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
	// manifests holds the approved Layer 2 approval lists. Nil disables Layer 2.
	manifests *ManifestRegistry
	// RequireApproval makes Layer 2 loud for unapproved agents: with no manifest
	// on record, every reported file fires provenance.untrusted instead of being
	// silently ignored. Off by default so an un-migrated fleet is not drowned.
	RequireApproval bool
	// uids resolves numeric actor uids to usernames at alert time. Nil (or a
	// disabled resolver) leaves uids numeric.
	uids *UidResolver
	// records retains detected events for the fleet view (P2.1). Nil disables
	// retention (alerts still fire; there is just nothing to query afterward).
	records RecordStore
	// now stubs the clock for retention/record timestamps in tests. Nil = time.Now.
	now func() time.Time
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

// SetManifests installs the Layer 2 manifest registry. Nil disables Layer 2.
func (s *Service) SetManifests(r *ManifestRegistry) { s.manifests = r }

// SetUidResolver installs the uid->name resolver applied to every event's actor
// before alerting. Nil disables resolution (uids stay numeric).
func (s *Service) SetUidResolver(r *UidResolver) { s.uids = r }

// SetRecordStore installs the durable event record store behind the fleet view
// (P2.1). Nil disables retention. It is wired separately from the alerter: an
// operator may want a queryable history even with alerting off, or alerts with
// no history on a throwaway -dry-run.
func (s *Service) SetRecordStore(r RecordStore) { s.records = r }

// nowTime returns the service clock (test-overridable).
func (s *Service) nowTime() time.Time {
	if s.now != nil {
		return s.now()
	}
	return time.Now().UTC()
}

// retain records each event for the fleet view. Best-effort: a record-store
// failure is logged, never fatal, because the alert already fired and the
// baseline is already durable — losing a history row must not fail an ingest.
func (s *Service) retain(host schema.Host, agent schema.Agent, events []schema.Event) {
	if s.records == nil || len(events) == 0 {
		return
	}
	when := s.nowTime()
	for _, ev := range events {
		rec := RecordFromEvent(host, agent, ev, when)
		if err := s.records.Append(context.Background(), rec); err != nil && s.logger != nil {
			s.logger.Printf("central: record store append failed: %v", err)
		}
	}
}

// resolveActors rewrites the actor on each event so a numeric uid carries its
// username where known. No-op when no resolver is installed. Applied to both
// ingest paths at the one point every event passes through on its way to an
// alert, so no sink has to know about uid mapping.
func (s *Service) resolveActors(events []schema.Event) {
	if s.uids == nil || !s.uids.Enabled() {
		return
	}
	for i := range events {
		s.uids.Apply(&events[i])
	}
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
	}

	// Layer 2: provenance/attestation runs on EVERY ingest (not just after a
	// baseline exists), because a brand-new dropped skill is exactly the case
	// where no drift is seen but the file was never approved.
	if s.manifests != nil {
		events = append(events, s.ProvenanceEvents(host, agent, inv)...)
	}

	// Stable ordering: sort by path, then by event class, so output is
	// deterministic regardless of map iteration order in Drift/Verify.
	sort.SliceStable(events, func(i, j int) bool {
		pi, pj := pathOf(events[i]), pathOf(events[j])
		if pi != pj {
			return pi < pj
		}
		return events[i].Event < events[j].Event
	})

	if err := s.store.PutBaseline(ctx, key, inv); err != nil {
		return nil, err
	}

	// Alert after the baseline is durable, so a failed alert never loses the
	// new baseline. Delivery failures are logged, never fatal: the collector
	// still gets its events, and the store is already correct. Retain the same
	// events for the fleet view regardless of whether an alerter is configured.
	if len(events) > 0 {
		s.resolveActors(events)
		s.retain(host, agent, events)
		if s.alerter != nil {
			s.alert(ctx, host, agent, events)
		}
	}

	return &IngestResult{FirstSeen: !found, Events: events}, nil
}

func (s *Service) alert(ctx context.Context, host schema.Host, agent schema.Agent, events []schema.Event) {
	a := Alert{When: time.Now().UTC(), Host: host, Agent: agent, Events: events}
	if err := s.alerter.Alert(ctx, a); err != nil && s.logger != nil {
		s.logger.Printf("central: alert delivery failed: %v", err)
	}
}

// IngestActions accepts Layer 3 behavior events (behavior.rule_match) from a
// collector's action telemetry and alerts on them through the same pipeline as
// drift. Unlike Ingest, there is no baseline to store or diff — these are
// already-decided events — so the only work is validation and alerting.
//
// The events are stamped with the submitting host/agent if they arrive without
// one, so an alert is always attributable even if a collector forgot.
func (s *Service) IngestActions(ctx context.Context, host schema.Host, agent schema.Agent, events []schema.Event) (int, error) {
	if len(events) == 0 {
		return 0, nil
	}
	accepted := make([]schema.Event, 0, len(events))
	for _, ev := range events {
		if ev.Event == "" {
			continue // not an event; drop rather than alert on garbage
		}
		if ev.Host.ID == "" {
			ev.Host = host
		}
		if ev.Agent == nil && agent.ID != "" {
			a := agent
			ev.Agent = &a
		}
		accepted = append(accepted, ev)
	}
	if len(accepted) == 0 {
		return 0, nil
	}
	s.resolveActors(accepted)
	s.retain(host, agent, accepted)
	if s.alerter != nil {
		s.alert(ctx, host, agent, accepted)
	}
	return len(accepted), nil
}

func pathOf(e schema.Event) string {
	if e.File != nil {
		return e.File.Path
	}
	return ""
}

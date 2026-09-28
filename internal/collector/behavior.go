package collector

import (
	"context"
	"log"
	"time"

	"github.com/CRPerdue/agentids/internal/rules"
	"github.com/CRPerdue/agentids/internal/schema"
	"github.com/CRPerdue/agentids/internal/telemetry"
)

// ActionSubmitter ships Layer 3 behavior events to the central service.
// *Client implements it; a test double can too, which keeps the collector's
// behavior wiring testable without a live central.
type ActionSubmitter interface {
	SubmitActions(ctx context.Context, req ActionsRequest) error
}

// BehaviorEngine is the collector-side Layer 3 evaluator: telemetry in, rule
// matches out. It is deliberately thin — the adapter parses host activity, the
// rules engine decides, the submitter ships — so each half stays independently
// testable.
type BehaviorEngine struct {
	eng       *telemetry.Engine
	submitter ActionSubmitter
	host      schema.Host
	agent     schema.Agent
}

// NewBehaviorEngine loads rule packs from cfg.Behavior.RulesDir and returns an
// engine bound to the given host/agent and submission client. Returns nil (not
// an error) when Layer 3 is not configured, so the caller can branch cleanly.
//
// A malformed pack IS an error: silently running with no rules would make the
// host look protected when it is not.
func NewBehaviorEngine(cfg *Config, host schema.Host, agent *schema.Agent, submitter ActionSubmitter) (*BehaviorEngine, error) {
	if cfg.Behavior.RulesDir == "" {
		return nil, nil
	}
	packs, err := rules.LoadPacks(cfg.Behavior.RulesDir)
	if err != nil {
		return nil, err
	}
	eng := rules.NewEngine(packs...)
	src := telemetry.NewFileSource(cfg.Behavior.AuditLog)
	a := schema.Agent{}
	if agent != nil {
		a = *agent
	}
	return &BehaviorEngine{
		eng:       telemetry.NewEngine(src, eng, host, agent),
		submitter: submitter,
		host:      host,
		agent:     a,
	}, nil
}

// Poll evaluates any new telemetry since the last call, logs each rule match,
// and ships the batch to central. Returning the events (rather than only
// logging) keeps this usable from tests and from a future local-only mode.
//
// Shipping is best-effort relative to the log: a central outage logs a warning
// and drops the batch rather than blocking the collector's file cycle. A
// durable spool is a later hardening step; for v1 the log line is the record.
func (b *BehaviorEngine) Poll() []schema.Event {
	if b == nil {
		return nil
	}
	events, err := b.eng.Matches()
	if err != nil {
		log.Printf("collector: behavior poll: %v", err)
		return nil
	}
	if len(events) == 0 {
		return nil
	}
	for _, ev := range events {
		id := ""
		if ev.Rule != nil {
			id = ev.Rule.ID
		}
		log.Printf("collector: RULE %s %s (severity=%s)", ev.Action.Type, id, ev.Severity)
	}
	if b.submitter != nil {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		defer cancel()
		if err := b.submitter.SubmitActions(ctx, ActionsRequest{
			Host: b.host, Agent: b.agent, Events: events,
		}); err != nil {
			log.Printf("collector: submit actions (%d): %v", len(events), err)
		}
	}
	return events
}

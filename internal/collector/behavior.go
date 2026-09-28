package collector

import (
	"log"

	"github.com/CRPerdue/agentids/internal/rules"
	"github.com/CRPerdue/agentids/internal/schema"
	"github.com/CRPerdue/agentids/internal/telemetry"
)

// BehaviorEngine is the collector-side Layer 3 evaluator: telemetry in, rule
// matches out. It is deliberately thin — the adapter parses host activity, the
// rules engine decides — so the two halves stay independently testable.
type BehaviorEngine struct {
	eng *telemetry.Engine
}

// NewBehaviorEngine loads rule packs from cfg.Behavior.RulesDir and returns an
// engine bound to the given host/agent. Returns nil (not an error) when Layer 3
// is not configured, so the caller can branch cleanly.
//
// A malformed pack IS an error: silently running with no rules would make the
// host look protected when it is not.
func NewBehaviorEngine(cfg *Config, host schema.Host, agent *schema.Agent) (*BehaviorEngine, error) {
	if cfg.Behavior.RulesDir == "" {
		return nil, nil
	}
	packs, err := rules.LoadPacks(cfg.Behavior.RulesDir)
	if err != nil {
		return nil, err
	}
	eng := rules.NewEngine(packs...)
	src := telemetry.NewFileSource(cfg.Behavior.AuditLog)
	return &BehaviorEngine{
		eng: telemetry.NewEngine(src, eng, host, agent),
	}, nil
}

// Poll evaluates any new telemetry since the last call and logs each rule
// match. Returning the events (rather than only logging) leaves room for the
// next step — shipping them to central alongside drift events.
func (b *BehaviorEngine) Poll() []schema.Event {
	if b == nil {
		return nil
	}
	events, err := b.eng.Matches()
	if err != nil {
		log.Printf("collector: behavior poll: %v", err)
		return nil
	}
	for _, ev := range events {
		id := ""
		if ev.Rule != nil {
			id = ev.Rule.ID
		}
		log.Printf("collector: RULE %s %s (severity=%s)", ev.Action.Type, id, ev.Severity)
	}
	return events
}

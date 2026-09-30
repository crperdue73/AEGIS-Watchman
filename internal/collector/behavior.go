package collector

import (
	"context"
	"fmt"
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
	timeline  *rules.Timeline
	spool     *Spool
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

	// An empty rules dir is the quiet twin of a malformed pack: the collector
	// would start with Layer 3 "on" and evaluate zero rules, so the host looks
	// protected while nothing can ever fire (a mis-pointed dir, a deleted pack).
	// Refuse to start instead of going silently blind — the same principle the
	// comment above states for a malformed pack.
	total := 0
	for _, p := range packs {
		total += len(p.Rules)
	}
	if total == 0 {
		return nil, fmt.Errorf("behavior.rules_dir %q contains no rules — Layer 3 would be enabled but detect nothing", cfg.Behavior.RulesDir)
	}
	log.Printf("collector: behavior: Layer 3 loaded %d rule(s) from %d pack(s)", total, len(packs))
	eng := rules.NewEngine(packs...)
	tl := rules.NewTimeline()
	// Compose telemetry sources through a MultiSource so a second backend (the
	// eBPF file.access adapter, docs/R2-EBPF-DESIGN.md) can be added here without
	// the collector or engine changing. Today it carries the auditd tail alone;
	// a host whose kernel can't load the eBPF object simply stays at one child.
	auditSrc := telemetry.NewFileSource(cfg.Behavior.AuditLog)
	if cur := cursorPath(cfg); cur != "" {
		auditSrc.SetCursor(cur)
	}
	src := telemetry.NewMultiSource(auditSrc)

	// The eBPF adapter is additive (D1): when configured, try to build it and
	// attach it alongside auditd. Any failure — no eBPF in this build, an old
	// kernel, no CAP_BPF — is logged once and the collector runs degraded rather
	// than refusing to start (D6). A verdict tool that goes blind because its
	// *second* source is unavailable is worse than one that runs with one eye
	// and says so.
	if cfg.Behavior.EBPF.Enabled {
		ebpfSrc, err := telemetry.NewEBPFSource(cfg.Behavior.EBPF, log.Default())
		switch {
		case err != nil:
			log.Printf("collector: behavior: eBPF file.access adapter unavailable, running auditd-only: %v", err)
		case ebpfSrc == nil:
			// Disabled at the source level; nothing to attach.
		default:
			src.Add(ebpfSrc)
			log.Printf("collector: behavior: eBPF file.access adapter attached")
		}
	}

	// The container-runtime adapter (G4, the CARBONATO entry vector) is additive
	// in exactly the same way: when enabled, `docker events` is spawned and its
	// container.start actions are matched alongside the auditd stream. A host
	// with no Docker daemon or no docker binary simply has nothing to report —
	// the collector keeps running on its other sources.
	if cfg.Behavior.Docker.Enabled {
		dockerSrc, err := telemetry.NewDockerSource(cfg.Behavior.Docker)
		switch {
		case err != nil:
			log.Printf("collector: behavior: docker events adapter unavailable, continuing without it: %v", err)
		case dockerSrc == nil:
			// Disabled at the source level; nothing to attach.
		default:
			src.Add(dockerSrc)
			log.Printf("collector: behavior: docker events adapter attached")
		}
	}
	a := schema.Agent{}
	if agent != nil {
		a = *agent
	}

	// The scope.change shim (P1.3, partial G5) wraps the composed stream and
	// derives "reached for a capability never used before" from the actions the
	// real sources emit. It is additive and inert when disabled, so it chains here
	// without any source learning about it. Wrapping last means it sees every
	// action the other adapters produced this poll. Default-on whenever Layer 3
	// rules are configured (cfg.Behavior.Scope.IsEnabled).
	var composed telemetry.Source = src
	if shim := telemetry.NewScopeSourceFromConfig(cfg.Behavior.Scope, cfg.Behavior.RulesDir != "", src, agent); shim != nil {
		composed = shim
		log.Printf("collector: behavior: scope.change heuristic shim attached (derived, detail.source=heuristic)")
	}
	return &BehaviorEngine{
		eng:       telemetry.NewEngine(composed, eng, host, agent).WithTimeline(tl),
		submitter: submitter,
		host:      host,
		agent:     a,
		timeline:  tl,
		spool:     NewSpool(cfg.Behavior.Spool, cfg.Behavior.SpoolMaxBytes),
	}, nil
}

// cursorPath resolves the audit-log offset cursor path: the explicit
// behavior.cursor if set, else derived beside the spool, else empty (no
// persistence). Deriving it from the spool means a deployment that already
// configured durability for shipping also gets restart-safe reading, with no
// extra knob.
func cursorPath(cfg *Config) string {
	if cfg.Behavior.Cursor != "" {
		return cfg.Behavior.Cursor
	}
	if cfg.Behavior.Spool != "" {
		return cfg.Behavior.Spool + ".cursor"
	}
	return ""
}

// ObserveDrift feeds Layer 1/2 file events into the timeline so correlation
// rules can see the file half of a chain. The brief's canonical rule — egress
// shortly after an identity file changed — cannot be decided by the action
// stream alone; this is how the two halves meet.
func (b *BehaviorEngine) ObserveDrift(events []schema.Event) {
	if b == nil || b.timeline == nil {
		return
	}
	now := time.Now()
	for _, ev := range events {
		b.timeline.ObserveEvent(ev, now)
	}
}

// Poll evaluates any new telemetry since the last call, logs each rule match,
// and ships the batch to central. Returning the events (rather than only
// logging) keeps this usable from tests and from a future local-only mode.
//
// Shipping is durable: every match is spooled to disk BEFORE any network call,
// and removed only once central accepts it. A central outage therefore costs
// latency, not evidence — whatever did not ship rides the next cycle. The spool
// is the single submission path (append, then drain), so there is no separate
// direct-send branch that could double-deliver.
func (b *BehaviorEngine) Poll() []schema.Event {
	if b == nil {
		return nil
	}
	if b.timeline != nil {
		b.timeline.Prune(time.Now())
	}
	events, err := b.eng.Matches()
	if err != nil {
		log.Printf("collector: behavior poll: %v", err)
		return nil
	}
	if len(events) > 0 {
		for _, ev := range events {
			id := ""
			if ev.Rule != nil {
				id = ev.Rule.ID
			}
			log.Printf("collector: RULE %s %s (severity=%s)", ev.Action.Type, id, ev.Severity)
		}
		if b.spool.Enabled() {
			if err := b.spool.Append(b.host, b.agent, events); err != nil {
				// A spool failure is loud: it means the durability guarantee is broken.
				log.Printf("collector: spool append (%d events): %v", len(events), err)
			}
		} else if b.submitter != nil {
			// No spool configured: ship best-effort (no durability, but shipping
			// still happens so a dev setup behaves like production minus the disk).
			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			if err := b.submitter.SubmitActions(ctx, ActionsRequest{Host: b.host, Agent: b.agent, Events: events}); err != nil {
				log.Printf("collector: submit actions (%d): %v", len(events), err)
			}
			cancel()
		}
	}
	// Drain ships this cycle's batch plus any backlog, stopping at the first
	// failure so nothing is lost when central is unreachable.
	b.drain()
	return events
}

// drain flushes spooled batches to central, stopping at the first failure.
func (b *BehaviorEngine) drain() {
	if b == nil || b.submitter == nil {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	n, err := b.spool.Drain(ctx, func(ctx context.Context, req ActionsRequest) error {
		return b.submitter.SubmitActions(ctx, req)
	})
	if err != nil {
		log.Printf("collector: spool drain: %v", err)
		return
	}
	if n > 0 {
		log.Printf("collector: shipped %d spooled events", n)
	}
}

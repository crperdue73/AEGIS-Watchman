package telemetry

import (
	"path"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/CRPerdue/agentids/internal/schema"
)

// ScopeSource derives synthetic `scope.change` actions from telemetry we
// already collect.
//
// # WHERE THIS SITS IN THE HONESTY LEDGER
//
// The CARBONATO objective ordering is "reach for new capability, then use it",
// and the pure signal for that is an agent-native event: the agent control
// plane saying "this agent was granted a new tool / API key / endpoint." We do
// not have that log in v1 — see the coverage matrix gap G5/A10. What we *can*
// do without it is infer "the agent reached for something it has never touched
// before" from the actions we already see:
//
//   - a new listening socket under an agent account (net.listen)
//   - a new outbound endpoint under an agent account (net.egress)
//   - a new executable path invoked under an agent account (process.exec)
//
// Each first-ever observation of such a capability is emitted as a single
// `scope.change` action with detail.source="heuristic" and detail.novel="true".
// The mark is deliberate and load-bearing: a downstream reader (a rule, an
// operator, a blog post) must be able to tell a *derived* scope change from an
// *authoritative* one. A heuristic dressed up as ground truth is the failure
// mode this project exists to argue against.
//
// WHAT IT IS NOT: it cannot see a capability the agent holds and never uses —
// no capability inventory exists to diff against. It sees first *use*, which is
// a strict subset and a smaller claim. The rule pack's scope rule therefore
// keys on detail.novel, and this source is the only thing in v1 that sets it.
//
// WINDOW: the "have I seen this before" memory is per-process and unbounded for
// the life of the collector. That is intentional — a capability seen an hour
// ago is still seen; forgetting it would re-fire. A collector restart resets
// the memory, so the first observation after a restart is reported again. That
// is a known, benign duplicate (at-least-once spirit), documented rather than
// hidden, because a persistent baseline belongs with the central store, not in
// a stateless shim.
type ScopeSource struct {
	inner Source
	agent *schema.Agent

	mu sync.Mutex
	// seen holds the capability keys already reported as novel, per class.
	// Keyed by "class\x00value" so one map serves listeners, endpoints, and
	// executables without collisions.
	seen map[string]struct{}

	// Now stamps derived actions. Nil means time.Now; tests set it.
	Now func() time.Time
}

// Detail keys and the source marker. Exported so rules and docs reference one
// spelling.
const (
	// ScopeSourceHeuristic is the value ScopeSource puts in detail.source. A
	// future agent-native adapter would emit detail.source="agent" and the same
	// rules would fire — the shim and the real thing are interchangeable.
	ScopeSourceHeuristic = "heuristic"
	// ScopeKind* name the capability class in detail.kind.
	ScopeKindListener = "listener"
	ScopeKindEndpoint = "endpoint"
	ScopeKindExec     = "exec"
)

// NewScopeSource wraps an inner source and derives scope.change actions from
// it. agent, when set, is copied onto the derived action for attribution.
func NewScopeSource(inner Source, agent *schema.Agent) *ScopeSource {
	return &ScopeSource{inner: inner, agent: agent, seen: map[string]struct{}{}}
}

// ScopeConfig enables the scope.change shim. Enabled is a pointer so an absent
// key (nil) is distinguishable from an explicit `enabled: false`: nil means
// "default", which the collector resolves to ON when a behavior rules_dir is
// configured (a deployment running Layer 3 gets the shim without opting in),
// and false means the operator turned it off deliberately.
type ScopeConfig struct {
	Enabled *bool `yaml:"enabled"`
}

// IsEnabled resolves the tri-state against the default-on-when-Layer-3 rule.
func (c ScopeConfig) IsEnabled(hasRules bool) bool {
	if c.Enabled == nil {
		return hasRules
	}
	return *c.Enabled
}

// NewScopeSourceFromConfig builds the shim from config. Returns nil when
// disabled, so the collector can chain it unconditionally and get an inert zero.
func NewScopeSourceFromConfig(cfg ScopeConfig, hasRules bool, inner Source, agent *schema.Agent) *ScopeSource {
	if !cfg.IsEnabled(hasRules) {
		return nil
	}
	return NewScopeSource(inner, agent)
}

// Seen reports how many distinct capabilities have been observed. Test-facing.
func (s *ScopeSource) Seen() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.seen)
}

// Actions polls the inner source and appends a scope.change event for every
// capability observed for the first time. The original events are preserved
// unchanged and in order; derived events follow them, sorted by capability key
// so output is deterministic.
func (s *ScopeSource) Actions() ([]Event, error) {
	inner, err := s.inner.Actions()
	if err != nil {
		return nil, err
	}
	now := time.Now()
	if s.Now != nil {
		now = s.Now()
	}

	// Collect the newly-novel capabilities from this batch. A set, so two
	// actions naming the same capability in one poll yield one scope.change.
	type cap struct {
		kind, value, exe string
		pid              int
	}
	novel := map[string]cap{}

	s.mu.Lock()
	for _, ev := range inner {
		kind, value := capabilityOf(ev.Action)
		if kind == "" || value == "" {
			continue
		}
		key := kind + "\x00" + value
		if _, ok := s.seen[key]; ok {
			continue
		}
		// Guard against the same capability appearing twice in one batch: only
		// the first sighting is recorded, and it is recorded before we leave the
		// loop, so the second sighting is a no-op.
		s.seen[key] = struct{}{}
		c := cap{kind: kind, value: value}
		if e, ok := ev.Action.Detail["exe"].(string); ok {
			c.exe = e
		}
		if ev.Actor != nil && ev.Actor.PID != nil {
			c.pid = *ev.Actor.PID
		}
		novel[key] = c
	}
	s.mu.Unlock()

	out := inner
	keys := make([]string, 0, len(novel))
	for k := range novel {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		c := novel[k]
		detail := map[string]any{
			"kind":   c.kind,
			"value":  c.value,
			"novel":  "true",
			"source": ScopeSourceHeuristic,
			"since":  now.UTC().Format(time.RFC3339),
		}
		if c.exe != "" {
			detail["exe"] = c.exe
		}
		if c.pid != 0 {
			detail["pid"] = c.pid
		}
		out = append(out, Event{
			Action: schema.Action{Type: ScopeChange, Detail: detail},
			Actor:  scopeActor(c.exe, c.pid),
		})
	}
	return out, nil
}

// capabilityOf maps an action to the (kind, value) capability it exercises, or
// ("", "") if the action does not name a capability we track.
//
// Only actions that represent *reaching for* something are mapped. A shell
// running `ls` is not a capability; a shell appearing under an agent root for
// the first time is closer to one, but we deliberately do not treat every first
// exec as scope expansion — that would fire on every shell the agent ever
// spawns and tune the rule off. The kinds we do track are the three that the
// CARBONATO ordering actually moves through: listening, egress, and the
// appearance of a new executable under an agent-owned path.
func capabilityOf(a schema.Action) (string, string) {
	switch a.Type {
	case NetListen:
		if v, ok := a.Detail["listen"].(string); ok && v != "" {
			return ScopeKindListener, v
		}
	case NetEgress:
		// Prefer the named service (stable, DNS-free) over the raw IP:port, so
		// the capability key is "slack.com" rather than a rotating IP that would
		// look novel every time the CDN changes.
		if v, ok := a.Detail["service"].(string); ok && v != "" {
			return ScopeKindEndpoint, v
		}
		if v, ok := a.Detail["endpoint"].(string); ok && v != "" {
			// endpoint may be "service ip:port"; take the first token as the key
			// so identity survives the IP suffix.
			return ScopeKindEndpoint, strings.Fields(v)[0]
		}
	case ProcessExec:
		exe, _ := a.Detail["exe"].(string)
		if isAgentOwnedExec(exe) {
			return ScopeKindExec, exe
		}
	}
	return "", ""
}

// agentExecRoots are the path prefixes that mark an executable as belonging to
// an agent's own tree rather than the system. A new binary appearing under one
// of these is a capability the agent reached for (an installed tool, a dropped
// script) — the "agent skills are the new curl | bash" shape, seen as an
// action.
var agentExecRoots = []string{
	"/home/",
	"/opt/agent",
	"/srv/agent",
	"/var/lib/agent",
	"/.openclaw/",
}

// isAgentOwnedExec reports whether an executable path sits under an
// agent-owned root. It requires a path separator so a bare system name like
// "ls" is never treated as agent-scoped (it is not agent-owned by location, and
// system binaries are not scope expansion).
func isAgentOwnedExec(exe string) bool {
	if exe == "" || !strings.Contains(exe, "/") {
		return false
	}
	// Normalise so a glob-shaped root prefix matches a nested path.
	clean := path.Clean(exe)
	for _, root := range agentExecRoots {
		if strings.HasPrefix(clean, root) {
			return true
		}
	}
	return false
}

// scopeActor builds an actor for a derived scope.change: the process that
// exercised the capability. It is best-effort by design — a scope change
// derived from an action with no actor is still worth reporting, so a nil actor
// is acceptable and the field is omitted downstream.
func scopeActor(exe string, pid int) *schema.Actor {
	if exe == "" && pid == 0 {
		return nil
	}
	a := &schema.Actor{}
	if pid != 0 {
		p := pid
		a.PID = &p
	}
	return a
}

package telemetry

import (
	"encoding/json"
	"fmt"
	"log"
	"os"
	"path"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/crperdue73/AEGIS-Watchman/internal/schema"
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
// WINDOW: the "have I seen this before" memory is per-process. A capability
// seen an hour ago is still seen; forgetting it would re-fire. The memory is
// bounded (DefaultScopeSeenMax) and, when a state file is configured, durable:
// PersistSeen loads the set at startup and saves it when it grows, so a
// collector restart does NOT re-report the host's established capabilities as
// novel. That matters — a collector restarts on every deploy and reboot, and a
// restart that re-emits every capability the agent routinely uses is exactly
// the kind of false burst that gets a rule tuned off. The on-disk set is a
// cache, not a system of record: an unreadable file is discarded and rebuilt,
// never fatal, because the shim is derived telemetry and must degrade (D6).
type ScopeSource struct {
	inner Source
	agent *schema.Agent

	mu sync.Mutex
	// seen holds the capability keys already reported as novel, per class.
	// Keyed by "class\x00value" so one map serves listeners, endpoints, and
	// executables without collisions. order preserves insertion order for
	// bounded eviction and for a deterministic on-disk encoding.
	seen  map[string]struct{}
	order []string
	// seenPath, when non-empty, is where the set is persisted across restarts.
	seenPath string
	// maxSeen caps the set (oldest evicted first). Zero means no cap.
	maxSeen int

	// Now stamps derived actions. Nil means time.Now; tests set it.
	Now func() time.Time
}

// DefaultScopeSeenMax bounds the seen-capability set. Distinct capabilities per
// host are realistically dozens; the cap exists so a pathological host (or a
// hostile one minting endpoints) cannot grow the set or its file without bound.
// Past the cap the oldest observation is evicted, so a capability untouched for
// long enough may re-fire once — a bounded, self-healing duplicate, never a
// standing alert.
const DefaultScopeSeenMax = 8192

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
	return &ScopeSource{
		inner:   inner,
		agent:   agent,
		seen:    map[string]struct{}{},
		maxSeen: DefaultScopeSeenMax,
	}
}

// PersistSeen makes the seen-set durable at path and loads any set already
// there. Call it once, right after construction. A missing file is a fresh
// start (nil error); an unreadable one is logged and discarded — the shim keeps
// working with an in-memory set and overwrites the file on the next new
// capability, so a corrupt cache costs at most a one-time duplicate, never a
// failure to start. An empty path disables persistence.
func (s *ScopeSource) PersistSeen(path string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if path == "" {
		s.seenPath = ""
		return nil
	}
	s.seenPath = path
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		return fmt.Errorf("scope: read seen file %q: %w", path, err)
	}
	var keys []string
	if err := json.Unmarshal(data, &keys); err != nil {
		return fmt.Errorf("scope: seen file %q unreadable, starting empty: %w", path, err)
	}
	for _, k := range keys {
		if _, ok := s.seen[k]; ok {
			continue
		}
		s.seen[k] = struct{}{}
		s.order = append(s.order, k)
	}
	s.evictLocked()
	return nil
}

// recordLocked marks key as seen and maintains the cap. Caller holds s.mu.
func (s *ScopeSource) recordLocked(key string) {
	s.seen[key] = struct{}{}
	s.order = append(s.order, key)
	s.evictLocked()
}

// evictLocked drops the oldest keys past the cap. Caller holds s.mu.
func (s *ScopeSource) evictLocked() {
	for s.maxSeen > 0 && len(s.order) > s.maxSeen {
		old := s.order[0]
		s.order = s.order[1:]
		delete(s.seen, old)
	}
}

// saveSeenLocked writes the set atomically (temp file + rename, mode 0600) in
// insertion order. Caller holds s.mu. A no-op when persistence is off or the
// set is empty.
func (s *ScopeSource) saveSeenLocked() error {
	if s.seenPath == "" || len(s.order) == 0 {
		return nil
	}
	data, err := json.Marshal(s.order)
	if err != nil {
		return err
	}
	dir := filepath.Dir(s.seenPath)
	if err := os.MkdirAll(dir, 0o750); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(dir, ".scope-seen-*")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	defer os.Remove(tmpName) // no-op once the rename succeeds
	if err := tmp.Chmod(0o600); err != nil {
		tmp.Close()
		return err
	}
	if _, err := tmp.Write(data); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Sync(); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmpName, s.seenPath)
}

// ScopeConfig enables the scope.change shim. Enabled is a pointer so an absent
// key (nil) is distinguishable from an explicit `enabled: false`: nil means
// "default", which the collector resolves to ON when a behavior rules_dir is
// configured (a deployment running Layer 3 gets the shim without opting in),
// and false means the operator turned it off deliberately.
type ScopeConfig struct {
	Enabled *bool `yaml:"enabled"`
	// Seen is a file path where the seen-capability set is persisted so a
	// collector restart does not re-report established capabilities as novel.
	// Empty -> derived as <spool>.scope-seen when a spool is set, else
	// in-memory only (reset on restart).
	Seen string `yaml:"seen"`
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
		// loop, so the second sighting is a no-op. recordLocked also enforces the
		// cap and keeps insertion order.
		s.recordLocked(key)
		c := cap{kind: kind, value: value}
		if e, ok := ev.Action.Detail["exe"].(string); ok {
			c.exe = e
		}
		if ev.Actor != nil && ev.Actor.PID != nil {
			c.pid = *ev.Actor.PID
		}
		novel[key] = c
	}
	if len(novel) > 0 {
		if err := s.saveSeenLocked(); err != nil {
			// Persisting the cache is best-effort: the derived events below must
			// still be emitted. A failed save costs a duplicate after the next
			// restart, not a lost detection.
			log.Printf("scope: could not persist seen set to %q: %v", s.seenPath, err)
		}
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

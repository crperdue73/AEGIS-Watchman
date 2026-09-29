// Package rules implements the Layer 3 rule format, parser, and evaluator.
//
// Rules match on ACTIONS, not files. A rule is a small, versioned, shareable
// document (the Snort model): an id, a severity, a match block over the action
// fields, and a description that ties it to a technique where possible.
//
// The engine is deliberately simple and deterministic — no regex compilation at
// match time, no eval, no external DSL. A rule is a set of conjunctive field
// predicates; a rule fires when every predicate matches. This keeps the format
// auditable (you can read a rule and know exactly what it does) and keeps the
// hot path allocation-light.
package rules

import (
	"fmt"
	"os"
	"path"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"gopkg.in/yaml.v3"
)

// Format version for the on-disk rule document.
const FormatVersion = 1

// Rule is one detection rule.
type Rule struct {
	// ID is a stable, namespaced identifier, e.g. "carbonato.tool.scope_new".
	ID string `yaml:"id" json:"id"`
	// Title is a one-line human summary.
	Title string `yaml:"title" json:"title"`
	// Severity: info | low | medium | high | critical.
	Severity string `yaml:"severity" json:"severity"`
	// Pack labels the rule family, e.g. "carbonato".
	Pack string `yaml:"pack,omitempty" json:"pack,omitempty"`
	// Version is the rule's own revision, e.g. "1".
	Version string `yaml:"version,omitempty" json:"version,omitempty"`
	// Technique optionally maps to a ATT&CK-style id or a brief section.
	Technique string `yaml:"technique,omitempty" json:"technique,omitempty"`
	// Description explains what the rule catches and why it matters.
	Description string `yaml:"description,omitempty" json:"description,omitempty"`
	// Match is the predicate block. A rule fires when ALL present predicates
	// match the observed action (logical AND). An empty Match never fires.
	Match Match `yaml:"match" json:"match"`
}

// Match is the predicate block over an action. Every present field must match;
// absent fields are ignored. Slices (argv_any, path_glob_any, endpoint_glob_any)
// match if ANY element matches.
type Match struct {
	// Type matches action.type exactly (process.exec, net.egress, ...).
	Type string `yaml:"type,omitempty" json:"type,omitempty"`

	// Exe matches the executable basename or full path (exact, case-sensitive).
	Exe string `yaml:"exe,omitempty" json:"exe,omitempty"`
	// ExeAny matches if the executable is any of these (basename or path).
	ExeAny []string `yaml:"exe_any,omitempty" json:"exe_any,omitempty"`

	// ArgvAny fires if ANY element of argv matches ANY of these as a glob
	// (e.g. "*.ssh*", "*id_rsa*"). Globs use path.Match semantics.
	ArgvAny []string `yaml:"argv_any,omitempty" json:"argv_any,omitempty"`

	// PathGlobAny matches action.detail.path against any glob.
	PathGlobAny []string `yaml:"path_glob_any,omitempty" json:"path_glob_any,omitempty"`

	// EndpointGlobAny matches action.detail.endpoint (host[:port] or URL) against
	// any glob.
	EndpointGlobAny []string `yaml:"endpoint_glob_any,omitempty" json:"endpoint_glob_any,omitempty"`

	// DetailEquals requires action.detail[key] to equal value (string compare).
	DetailEquals map[string]string `yaml:"detail_equals,omitempty" json:"detail_equals,omitempty"`
	// DetailNonEmpty requires action.detail[key] to be present and non-empty.
	DetailNonEmpty []string `yaml:"detail_non_empty,omitempty" json:"detail_non_empty,omitempty"`
	// DetailNonEmptyAny fires if AT LEAST ONE of these detail keys is present and
	// non-empty. Where DetailNonEmpty is an AND over keys, this is an OR — the
	// shape a rule wants when several independent fields each indicate the same
	// risk (e.g. a container is dangerous if privileged OR host-PID OR
	// host-network).
	DetailNonEmptyAny []string `yaml:"detail_non_empty_any,omitempty" json:"detail_non_empty_any,omitempty"`
	// DetailGlobAny maps a detail key to globs; the predicate fires if
	// action.detail[key], for ANY listed key, matches ANY of that key's globs.
	// It exists for fields that carry a composite value — a container's `binds`
	// is a comma-joined list of host mounts, and a rule wants "any bind that
	// looks like the host root" without the telemetry layer exploding the list.
	DetailGlobAny map[string][]string `yaml:"detail_glob_any,omitempty" json:"detail_glob_any,omitempty"`

	// AgentID matches agent.id exactly, if set.
	AgentID string `yaml:"agent_id,omitempty" json:"agent_id,omitempty"`

	// PriorEvent requires that an event of the named class was observed on the
	// same host within the given window BEFORE this action. This is the temporal
	// predicate that makes correlation rules possible — e.g. "an identity.drift
	// happened within 5m of this outbound call." It is the one predicate that
	// needs the Timeline rather than the action alone.
	PriorEvent *PriorEvent `yaml:"prior_event,omitempty" json:"prior_event,omitempty"`
}

// PriorEvent is a temporal predicate: "an event of Class was seen on this host
// within Within before the action under evaluation."
type PriorEvent struct {
	// Class is an event class the timeline records, e.g. "identity.drift" or a
	// raw action type like "process.exec".
	Class string `yaml:"class" json:"class"`
	// Within is the look-back window, e.g. "5m". Required and positive.
	Within time.Duration `yaml:"within" json:"within"`
}

// Pack is a named, versioned collection of rules loaded from one file or dir.
type Pack struct {
	Name    string `yaml:"name" json:"name"`
	Version string `yaml:"version,omitempty" json:"version,omitempty"`
	Rules   []Rule `yaml:"rules" json:"rules"`
}

// ParsePack parses a YAML rule pack from raw bytes and validates every rule.
//
// Parsing is STRICT about unknown keys: a misspelled predicate or field is a
// hard error, not a silently ignored line. This is the same reasoning as
// Validate — a rule that loads but can never fire (or silently drops a
// predicate and under-matches) is the worst failure mode for a detection tool,
// because the operator believes they are covered. It also makes a pack
// forward-safe: an older binary refuses a pack that uses a predicate it does
// not understand, instead of running a weakened version of it.
func ParsePack(raw []byte) (*Pack, error) {
	var p Pack
	dec := yaml.NewDecoder(strings.NewReader(string(raw)))
	dec.KnownFields(true)
	if err := dec.Decode(&p); err != nil {
		return nil, fmt.Errorf("rules: parse pack: %w", err)
	}
	if p.Name == "" {
		return nil, fmt.Errorf("rules: pack has no name")
	}
	if len(p.Rules) == 0 {
		return nil, fmt.Errorf("rules: pack %q has no rules", p.Name)
	}
	seen := map[string]bool{}
	for i := range p.Rules {
		r := &p.Rules[i]
		if r.Pack == "" {
			r.Pack = p.Name
		}
		if r.Version == "" {
			r.Version = p.Version
		}
		if err := r.Validate(); err != nil {
			return nil, fmt.Errorf("rules: pack %q: %w", p.Name, err)
		}
		if seen[r.ID] {
			return nil, fmt.Errorf("rules: pack %q: duplicate rule id %q", p.Name, r.ID)
		}
		seen[r.ID] = true
	}
	return &p, nil
}

// LoadPack reads and parses a rule pack from a file path.
func LoadPack(path string) (*Pack, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("rules: read pack %s: %w", path, err)
	}
	p, err := ParsePack(raw)
	if err != nil {
		return nil, fmt.Errorf("rules: %s: %w", path, err)
	}
	return p, nil
}

// LoadPacks loads every *.yaml/*.yml under dir (non-recursive), sorted by name
// for determinism. A directory with no packs is not an error.
func LoadPacks(dir string) ([]*Pack, error) {
	if dir == "" {
		return nil, nil
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, fmt.Errorf("rules: read dir %s: %w", dir, err)
	}
	var names []string
	for _, e := range entries {
		if e.IsDir() {
			continue
		}
		n := e.Name()
		if strings.HasSuffix(n, ".yaml") || strings.HasSuffix(n, ".yml") {
			names = append(names, n)
		}
	}
	sort.Strings(names)
	var packs []*Pack
	seen := map[string]string{} // rule id -> the pack that declared it first
	for _, n := range names {
		p, err := LoadPack(filepath.Join(dir, n))
		if err != nil {
			return nil, err
		}
		// IDs must be unique across the whole loaded set, not just within one
		// pack: two packs that both define "carbonato.exec.shell_spawn" would
		// otherwise ship duplicate alerts under one id, and a deliberate override
		// (a typo'd, or hostile, pack shadowing a real rule) would be silent.
		for i := range p.Rules {
			id := p.Rules[i].ID
			if prev, ok := seen[id]; ok {
				return nil, fmt.Errorf("rules: duplicate rule id %q in packs %q and %q", id, prev, p.Name)
			}
			seen[id] = p.Name
		}
		packs = append(packs, p)
	}
	return packs, nil
}

// Validate checks a rule's shape. It is strict on purpose: a malformed rule
// that silently never fires is worse than a startup error.
func (r *Rule) Validate() error {
	if r.ID == "" {
		return fmt.Errorf("rule has no id")
	}
	if !strings.Contains(r.ID, ".") {
		return fmt.Errorf("rule %q: id should be namespaced (e.g. pack.area.name)", r.ID)
	}
	switch r.Severity {
	case "info", "low", "medium", "high", "critical":
	case "":
		return fmt.Errorf("rule %q: severity is required", r.ID)
	default:
		return fmt.Errorf("rule %q: invalid severity %q", r.ID, r.Severity)
	}
	if m := r.Match; isZeroMatch(m) {
		return fmt.Errorf("rule %q: empty match block (would never fire)", r.ID)
	}
	for k := range r.Match.DetailEquals {
		if k == "" {
			return fmt.Errorf("rule %q: detail_equals has an empty key", r.ID)
		}
	}
	for k, globs := range r.Match.DetailGlobAny {
		if k == "" {
			return fmt.Errorf("rule %q: detail_glob_any has an empty key", r.ID)
		}
		if len(globs) == 0 {
			return fmt.Errorf("rule %q: detail_glob_any[%s] has no globs", r.ID, k)
		}
		if err := validateGlobs(r.ID, "detail_glob_any["+k+"]", globs); err != nil {
			return err
		}
	}
	for _, f := range []struct {
		name  string
		globs []string
	}{
		{"argv_any", r.Match.ArgvAny},
		{"path_glob_any", r.Match.PathGlobAny},
		{"endpoint_glob_any", r.Match.EndpointGlobAny},
	} {
		if err := validateGlobs(r.ID, f.name, f.globs); err != nil {
			return err
		}
	}
	if pe := r.Match.PriorEvent; pe != nil {
		if strings.TrimSpace(pe.Class) == "" {
			return fmt.Errorf("rule %q: prior_event.class is required", r.ID)
		}
		if pe.Within <= 0 {
			return fmt.Errorf("rule %q: prior_event.within must be positive (e.g. 5m)", r.ID)
		}
	}
	return nil
}

// validateGlobs rejects a malformed glob pattern at load time. The matcher
// treats a bad pattern as a non-match (path.Match returns ErrBadPattern, which
// matchOne discards), so without this check a rule with an unclosed class like
// "[abc" would load and silently never fire — the same silent-death failure
// mode Validate exists to refuse. Checked against the empty string, which still
// parses the pattern.
func validateGlobs(ruleID, field string, globs []string) error {
	for _, g := range globs {
		if _, err := path.Match(g, ""); err != nil {
			return fmt.Errorf("rule %q: %s has a malformed glob %q: %w", ruleID, field, g, err)
		}
	}
	return nil
}

// isZeroMatch reports whether a match block has no predicates at all.
func isZeroMatch(m Match) bool {
	return m.Type == "" && m.Exe == "" && len(m.ExeAny) == 0 &&
		len(m.ArgvAny) == 0 && len(m.PathGlobAny) == 0 && len(m.EndpointGlobAny) == 0 &&
		len(m.DetailEquals) == 0 && len(m.DetailNonEmpty) == 0 &&
		len(m.DetailNonEmptyAny) == 0 && len(m.DetailGlobAny) == 0 &&
		m.AgentID == "" && m.PriorEvent == nil
}

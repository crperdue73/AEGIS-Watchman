package rules

import (
	"path"
	"strings"

	"github.com/CRPerdue/agentids/internal/schema"
)

// Engine evaluates a set of rules against observed actions.
type Engine struct {
	rules []Rule
}

// NewEngine builds an engine from packs (later packs do not override earlier
// ids — duplicate ids across packs are a load error elsewhere, but here the
// engine simply evaluates all rules and reports every match).
func NewEngine(packs ...*Pack) *Engine {
	var rs []Rule
	for _, p := range packs {
		if p == nil {
			continue
		}
		rs = append(rs, p.Rules...)
	}
	return &Engine{rules: rs}
}

// NewEngineRules builds an engine directly from rules (used by tests).
func NewEngineRules(rs []Rule) *Engine { return &Engine{rules: rs} }

// Len returns the number of loaded rules.
func (e *Engine) Len() int { return len(e.rules) }

// Match evaluates every rule against an action and returns the events for those
// that fire. Ordering is deterministic: rules fire in load order, so output is
// stable regardless of map iteration.
//
// actor is optional (nil when unattributable). agent is optional.
func (e *Engine) Match(host schema.Host, agent *schema.Agent, action schema.Action, actor *schema.Actor) []schema.Event {
	var out []schema.Event
	for i := range e.rules {
		r := &e.rules[i]
		if !matches(r.Match, agent, action) {
			continue
		}
		ev := schema.NewEnvelope(schema.EventBehaviorRuleMatch, host)
		if agent != nil {
			a := *agent
			ev.Agent = &a
		}
		act := action
		ev.Action = &act
		ev.Rule = &schema.Rule{ID: r.ID, Pack: r.Pack, Version: r.Version}
		ev.Severity = r.Severity
		ev.Actor = actor
		out = append(out, *ev)
	}
	return out
}

// matches reports whether an action satisfies every predicate in a match block.
func matches(m Match, agent *schema.Agent, action schema.Action) bool {
	if m.Type != "" && action.Type != m.Type {
		return false
	}
	if m.AgentID != "" {
		if agent == nil || agent.ID != m.AgentID {
			return false
		}
	}

	exe := detailString(action, "exe")
	if m.Exe != "" && !exeMatches(m.Exe, exe) {
		return false
	}
	if len(m.ExeAny) > 0 {
		ok := false
		for _, e := range m.ExeAny {
			if exeMatches(e, exe) {
				ok = true
				break
			}
		}
		if !ok {
			return false
		}
	}

	if len(m.ArgvAny) > 0 {
		argv := detailStringSlice(action, "argv")
		if !anyArgvMatches(m.ArgvAny, argv, exe) {
			return false
		}
	}

	if len(m.PathGlobAny) > 0 {
		p := detailString(action, "path")
		if !anyGlobMatches(m.PathGlobAny, p) {
			return false
		}
	}

	if len(m.EndpointGlobAny) > 0 {
		ep := detailString(action, "endpoint")
		if !anyGlobMatches(m.EndpointGlobAny, ep) {
			return false
		}
	}

	for k, want := range m.DetailEquals {
		if detailString(action, k) != want {
			return false
		}
	}
	for _, k := range m.DetailNonEmpty {
		if detailString(action, k) == "" {
			return false
		}
	}
	return true
}

// exeMatches compares an exe value against a rule term. The rule term matches
// either the raw string (full path) or its basename, so "bash" matches
// "/bin/bash" and "/usr/bin/curl" matches exactly.
func exeMatches(term, exe string) bool {
	if exe == "" {
		return false
	}
	if term == exe {
		return true
	}
	return term == path.Base(exe)
}

// anyArgvMatches reports whether any argv element matches any glob. The
// executable itself is also considered (argv[0] is often the program name), and
// each element is checked both whole and as a basename so "*.ssh*" catches
// "/home/x/.ssh/id_rsa" and "*id_rsa*" catches it too.
func anyArgvMatches(globs []string, argv []string, exe string) bool {
	candidates := append([]string{}, argv...)
	if exe != "" {
		candidates = append(candidates, exe)
	}
	for _, a := range candidates {
		if anyGlobMatches(globs, a) {
			return true
		}
	}
	return false
}

// anyGlobMatches reports whether s matches any of the globs. Matching is
// "hierarchical": '*' and '?' do not cross '/' (path.Match semantics), but a
// glob is also tried against every trailing suffix of s (so "*/.ssh/*" hits
// "/root/.ssh/id_ed25519"), against the basename alone (so "*id_rsa*" hits
// "/home/x/.ssh/id_rsa"), and — for globs that contain no '/' — against the raw
// string with a fallback "contains" test so "*slack.com*" still matches a full
// URL like "https://api.telegram.org/...".
func anyGlobMatches(globs []string, s string) bool {
	if s == "" {
		return false
	}
	base := path.Base(s)
	suffixes := pathSuffixes(s)
	for _, g := range globs {
		if matchOne(g, s, base, suffixes) {
			return true
		}
	}
	return false
}

func matchOne(glob, s, base string, suffixes []string) bool {
	// Whole-string match.
	if ok, err := path.Match(glob, s); err == nil && ok {
		return true
	}
	// Any trailing suffix (handles multi-segment prefixes matched by '*'/'?').
	for _, suf := range suffixes {
		if ok, err := path.Match(glob, suf); err == nil && ok {
			return true
		}
	}
	// Basename.
	if ok, err := path.Match(glob, base); err == nil && ok {
		return true
	}
	// Contains fallback for globs shaped like "*needle*" or "prefix*" that name a
	// domain/substring rather than a path: '*'/'?' do not cross '/', so
	// "*api.telegram.org*" would miss "api.telegram.org/bot123/send" without
	// this. Only applied to globs with no interior '/' beyond the wildcard.
	if needle, ok := substringNeedle(glob); ok {
		if strings.Contains(s, needle) {
			return true
		}
	}
	return false
}

// substringNeedle extracts a literal substring from a "*x*" / "*x" / "x*"
// glob whose interior contains no further wildcard, so a domain glob still
// matches a full URL/path. Returns ok=false for anything else.
func substringNeedle(glob string) (string, bool) {
	core := glob
	core = strings.TrimPrefix(core, "*")
	core = strings.TrimSuffix(core, "*")
	if core == "" {
		return "", false
	}
	if strings.ContainsAny(core, "*?[") {
		return "", false
	}
	return core, true
}

// pathSuffixes returns s and every suffix beginning after a '/'.
// "/root/.ssh/id_ed25519" -> ["/root/.ssh/id_ed25519", ".ssh/id_ed25519", "id_ed25519"].
func pathSuffixes(s string) []string {
	out := []string{s}
	for i := 0; i < len(s); i++ {
		if s[i] == '/' && i+1 < len(s) {
			out = append(out, s[i+1:])
		}
	}
	return out
}

// detailString reads a string field from an action's detail map.
func detailString(action schema.Action, key string) string {
	if action.Detail == nil {
		return ""
	}
	v, ok := action.Detail[key]
	if !ok {
		return ""
	}
	switch t := v.(type) {
	case string:
		return t
	case []byte:
		return string(t)
	default:
		return ""
	}
}

// detailStringSlice reads a []string field, tolerating []any (as JSON decoding
// produces) so the engine works on both in-process and wire-decoded actions.
func detailStringSlice(action schema.Action, key string) []string {
	if action.Detail == nil {
		return nil
	}
	v, ok := action.Detail[key]
	if !ok {
		return nil
	}
	switch t := v.(type) {
	case []string:
		return t
	case []any:
		out := make([]string, 0, len(t))
		for _, e := range t {
			if s, ok := e.(string); ok {
				out = append(out, s)
			}
		}
		return out
	case string:
		return []string{t}
	default:
		return nil
	}
}

// normalizePathGlobs is a small helper used by tests to keep globs portable.
func normalizePathGlobs(globs []string) []string {
	out := make([]string, len(globs))
	for i, g := range globs {
		out[i] = strings.ReplaceAll(g, "\\", "/")
	}
	return out
}

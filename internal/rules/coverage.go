package rules

import "sort"

// Coverage accounting: which loaded rules a given host can actually fire.
//
// A rule pack is a claim about what a fleet detects. But a rule only fires if a
// telemetry source emits the action class it keys on (its Match.Type), and some
// classes come from sources that are optional or not built at all — the eBPF
// file.access adapter, the opt-in docker container.start adapter. On a host where
// such a source is absent, those rules load, validate, and ride in the pack, and
// can never fire. That is the "look protected, detect nothing" failure this
// whole project exists to name, so it is worth making mechanical rather than a
// footnote an operator may not read.

// ActionTypes returns the sorted, distinct action classes the rules in packs are
// keyed on (each rule's Match.Type). A rule with no explicit type is omitted:
// it can fire on any action, so no single class gates it. The result is exactly
// the set of classes a host must be able to emit for every typed rule to have a
// chance to fire.
func ActionTypes(packs []*Pack) []string {
	set := map[string]bool{}
	for _, p := range packs {
		for _, r := range p.rules() {
			if t := r.Match.Type; t != "" {
				set[t] = true
			}
		}
	}
	out := make([]string, 0, len(set))
	for t := range set {
		out = append(out, t)
	}
	sort.Strings(out)
	return out
}

// InertRules returns, in input order, the rules whose Match.Type names an action
// class that `available` does not contain — rules that load, validate, and are
// carried by a pack but cannot fire on a host that emits only `available`. A
// rule with no explicit type is never inert (it matches any action).
//
// The caller decides what "available" means; see the collector's
// availableActionTypes, which derives it from the sources it actually attached.
func InertRules(packs []*Pack, available map[string]bool) []Rule {
	var out []Rule
	for _, p := range packs {
		for _, r := range p.rules() {
			if t := r.Match.Type; t != "" && !available[t] {
				out = append(out, r)
			}
		}
	}
	return out
}

// rules returns a pack's rules, tolerating a nil pack so callers need no guard.
func (p *Pack) rules() []Rule {
	if p == nil {
		return nil
	}
	return p.Rules
}

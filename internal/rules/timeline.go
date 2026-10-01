package rules

import (
	"strings"
	"time"

	"github.com/crperdue73/AEGIS-Watchman/internal/schema"
)

// Timeline is a small, bounded per-host record of recently observed events.
//
// Most Layer 3 rules match on a single action. A few of the most valuable ones
// are correlations in time — the brief's canonical example is "the agent's
// identity file changed within N minutes of an outbound call." That rule cannot
// be decided by looking at one action; it needs a short memory of what else just
// happened on the host.
//
// Timeline is that memory. It is deliberately small and dumb: a ring buffer of
// (time, class) observations, capped per host, with a retention window. No
// indexing, no query language — a linear scan within a window is trivially fast
// at these sizes and impossible to get subtly wrong.
type Timeline struct {
	// window is how far back Record/Seen look. Observations older than this are
	// dropped on insert.
	window time.Duration
	// cap is the maximum observations retained per host (defence against a
	// noisy host pinning memory). The oldest are evicted first.
	cap    int
	byHost map[string][]Observation
}

// Observation is one remembered event: when it happened and what class it was.
type Observation struct {
	At    time.Time
	Class string
	// Host is kept so a caller can merge timelines if it ever needs to.
	Host string
}

// DefaultTimelineWindow and DefaultTimelineCap are the built-in bounds.
const (
	DefaultTimelineWindow = 15 * time.Minute
	DefaultTimelineCap    = 256
)

// NewTimeline returns a timeline with the default window and cap.
func NewTimeline() *Timeline {
	return NewTimelineSized(DefaultTimelineWindow, DefaultTimelineCap)
}

// NewTimelineSized returns a timeline with explicit window and cap. A
// non-positive window falls back to the default; a non-positive cap likewise.
func NewTimelineSized(window time.Duration, cap int) *Timeline {
	if window <= 0 {
		window = DefaultTimelineWindow
	}
	if cap <= 0 {
		cap = DefaultTimelineCap
	}
	return &Timeline{window: window, cap: cap, byHost: map[string][]Observation{}}
}

// Record adds an observation for a host, evicting anything older than the
// window and trimming to the cap. asOf is the "now" for eviction, so tests can
// drive time deterministically.
func (t *Timeline) Record(host string, at time.Time, class string, asOf time.Time) {
	if t == nil || host == "" || class == "" {
		return
	}
	cutoff := asOf.Add(-t.window)
	list := t.byHost[host]
	// Drop expired (the slice is append-ordered, so expired entries are a
	// prefix).
	keep := list[:0]
	for _, o := range list {
		if o.At.After(cutoff) {
			keep = append(keep, o)
		}
	}
	keep = append(keep, Observation{At: at, Class: class, Host: host})
	if len(keep) > t.cap {
		keep = keep[len(keep)-t.cap:]
	}
	t.byHost[host] = keep
}

// Seen reports whether an observation of the given class happened on the host
// within [asOf-within, asOf]. A within <= 0 means "any time still retained".
func (t *Timeline) Seen(host, class string, within time.Duration, asOf time.Time) bool {
	if t == nil || host == "" || class == "" {
		return false
	}
	cutoff := asOf.Add(-within)
	if within <= 0 {
		cutoff = time.Time{}
	}
	for _, o := range t.byHost[host] {
		if o.Class != class {
			continue
		}
		if o.At.After(cutoff) && !o.At.After(asOf) {
			return true
		}
	}
	return false
}

// Len returns the number of retained observations for a host (test helper).
func (t *Timeline) Len(host string) int {
	if t == nil {
		return 0
	}
	return len(t.byHost[host])
}

// Prune drops every host's observations older than the window as of asOf.
// The collector calls this each tick so an idle host's memory falls away.
func (t *Timeline) Prune(asOf time.Time) {
	if t == nil {
		return
	}
	cutoff := asOf.Add(-t.window)
	for host, list := range t.byHost {
		keep := list[:0]
		for _, o := range list {
			if o.At.After(cutoff) {
				keep = append(keep, o)
			}
		}
		if len(keep) == 0 {
			delete(t.byHost, host)
			continue
		}
		t.byHost[host] = keep
	}
}

// classOf maps an event class ("identity.drift", "behavior.rule_match") or an
// action type ("process.exec") to the token a rule uses to refer to it. Kept in
// one place so the rule vocabulary and the timeline agree.
func classOf(ev string) string { return strings.TrimSpace(ev) }

// ObserveEvent records a schema event into the timeline under its event class.
// This is how a collector feeds file/identity changes in alongside actions, so
// the egress-after-change rule can see both halves.
func (t *Timeline) ObserveEvent(ev schema.Event, asOf time.Time) {
	if t == nil {
		return
	}
	host := ev.Host.ID
	if host == "" {
		return
	}
	at := ev.TS
	if at.IsZero() {
		at = asOf
	}
	t.Record(host, at, classOf(ev.Event), asOf)
}

package telemetry

import (
	"fmt"
	"log"
)

// MultiSource composes several telemetry Sources into one. Each backend keeps
// its own position (the auditd tail its offset, a future eBPF source its ring
// buffer), and MultiSource simply concatenates their batches on each poll.
//
// This exists so the collector never learns about backends: run.go holds one
// Source, and whether that is auditd alone, auditd plus eBPF, or a demo text
// stream is a wiring detail. It is also the mechanism that lets the tool
// degrade instead of fail — a host whose kernel cannot load the eBPF object
// just runs a MultiSource with one healthy child.
//
// Overlap is acceptable. Two sources may report the same logical action (e.g. a
// connect seen by both an auditd rule and an eBPF probe); matches are keyed by
// rule id + action, and the spool is at-least-once by design, so a duplicate is
// a duplicate alert at worst, never a lost one.
type MultiSource struct {
	srcs []Source
	// Logger receives a line when one child fails, so a broken backend is
	// visible without silencing the healthy ones. Nil is safe.
	Logger *log.Logger
}

// NewMultiSource composes the given sources. Nil children are dropped, so a
// caller can pass an optional source unconditionally.
func NewMultiSource(srcs ...Source) *MultiSource {
	kept := make([]Source, 0, len(srcs))
	for _, s := range srcs {
		if s != nil {
			kept = append(kept, s)
		}
	}
	return &MultiSource{srcs: kept}
}

// Add appends a source (nil is ignored). Returns the receiver for chaining.
func (m *MultiSource) Add(s Source) *MultiSource {
	if s != nil {
		m.srcs = append(m.srcs, s)
	}
	return m
}

// Len reports how many sources are composed.
func (m *MultiSource) Len() int { return len(m.srcs) }

// Actions polls every child and returns their events concatenated, in child
// order. A child that errors is logged and skipped, never fatal: one blind
// backend must not blind the rest. If every child errors, the errors are
// summarised into one so the collector's own logging still sees a problem.
func (m *MultiSource) Actions() ([]Event, error) {
	var out []Event
	var firstErr error
	errs := 0
	for _, s := range m.srcs {
		evs, err := s.Actions()
		if err != nil {
			errs++
			if firstErr == nil {
				firstErr = err
			}
			if m.Logger != nil {
				m.Logger.Printf("telemetry: source failed, continuing: %v", err)
			}
			continue
		}
		out = append(out, evs...)
	}
	if errs > 0 && errs == len(m.srcs) {
		return nil, fmt.Errorf("telemetry: all %d sources failed: %w", errs, firstErr)
	}
	return out, nil
}

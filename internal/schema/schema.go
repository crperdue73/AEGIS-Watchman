// Package schema defines the agentids event wire format.
//
// This is the PUBLIC CONTRACT of the project. Other people build on it.
// Treat changes as breaking — see ../../../docs/event-schema.md for the
// versioning policy. Additive optional fields are fine; renames are not.
package schema

import "time"

// Version is the current event schema version string.
const Version = "agentids.event/v1"

// Event class names.
const (
	EventIdentityDrift       = "identity.drift"       // Layer 1
	EventProvenanceUntrusted = "provenance.untrusted" // Layer 2
	EventBehaviorRuleMatch   = "behavior.rule_match"  // Layer 3
)

// Severity levels.
const (
	SeverityInfo     = "info"
	SeverityLow      = "low"
	SeverityMedium   = "medium"
	SeverityHigh     = "high"
	SeverityCritical = "critical"
)

// Event is the top-level envelope shared by every detection event.
type Event struct {
	Schema   string    `json:"schema"`
	Event    string    `json:"event"`
	TS       time.Time `json:"ts"`
	Host     Host      `json:"host"`
	Agent    *Agent    `json:"agent,omitempty"`
	File     *FileInfo `json:"file,omitempty"`
	Action   *Action   `json:"action,omitempty"`
	Rule     *Rule     `json:"rule,omitempty"`
	Actor    *Actor    `json:"actor"`
	Severity string    `json:"severity,omitempty"`
}

// Host identifies the machine an event originated from.
type Host struct {
	ID   string `json:"id"`
	Name string `json:"name,omitempty"`
}

// Agent identifies the agent concerned by an event.
type Agent struct {
	ID   string `json:"id,omitempty"`
	Root string `json:"root,omitempty"`
}

// FileInfo carries file-oriented detail for Layer 1/2 events.
type FileInfo struct {
	Path         string `json:"path"`
	SHA256Before string `json:"sha256_before,omitempty"`
	SHA256After  string `json:"sha256_after,omitempty"`
	SizeBefore   *int64 `json:"size_before,omitempty"`
	SizeAfter    *int64 `json:"size_after,omitempty"`
	Diff         string `json:"diff,omitempty"`
}

// Action carries behavior-oriented detail for Layer 3 events.
type Action struct {
	Type   string         `json:"type"`             // process.exec | net.egress | file.access | scope.change
	Detail map[string]any `json:"detail,omitempty"` // rule-specific fields
}

// Rule identifies which rule fired.
type Rule struct {
	ID      string `json:"id"`
	Pack    string `json:"pack,omitempty"`
	Version string `json:"version,omitempty"`
	// Technique is the rule's ATT&CK-style tag, when the rule declares one. It
	// rides on the event so consumers (and central's fleet rollup) can group by
	// technique without re-reading the rule pack. Additive/optional per the v1
	// versioning policy.
	Technique string `json:"technique,omitempty"`
}

// Actor is who performed the action, when attributable. Nil when unavailable.
type Actor struct {
	UID  *int   `json:"uid,omitempty"`
	User string `json:"user,omitempty"`
	PID  *int   `json:"pid,omitempty"`
}

// NewEnvelope returns a minimal, valid event with the header fields set.
func NewEnvelope(event string, host Host) *Event {
	return &Event{
		Schema: Version,
		Event:  event,
		TS:     time.Now().UTC(),
		Host:   host,
	}
}

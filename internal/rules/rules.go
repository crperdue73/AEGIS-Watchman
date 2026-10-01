// Package rules implements the Layer 3 rule format, parser, and engine:
// rules match on ACTIONS, not files, and a rule is a small, versioned,
// shareable document (the Snort model) that a stranger can read and audit.
//
// The pieces:
//
//   - rule.go   — the Rule/Match/Pack types, the strict parser, and validation
//   - engine.go — the deterministic evaluator (every predicate in a Match is an
//     AND; slices are an OR within a predicate)
//   - timeline.go — the per-host timeline that temporal predicates (prior_event)
//     consult, so correlation rules like "egress within 5m of an identity
//     change" can be decided
//   - pins.go   — the pack pin/sign/verify tooling behind `watchman-rules`
//
// Telemetry adapters that turn host activity into the actions these rules match
// live in internal/telemetry; the shipped pack is ../../rules/carbonato.yaml.
package rules

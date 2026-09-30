// Package central implements the central service: ingest of file inventories
// and behavior events, the durable baseline and record stores, drift and
// provenance verification, alerting, and the fleet view. It is the one place a
// fleet's evidence lands, so an operator has something to look at after an
// incident rather than a log that scrolled away.
//
// Layer 3 rule evaluation runs in the collector (internal/rules), not here;
// central ingests the behavior.rule_match events it produces and rolls them up
// with the drift and provenance events for GET / and GET /api/v1/fleet.
package central

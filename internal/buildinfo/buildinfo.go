// Package buildinfo carries the version, commit, and build date stamped into
// the binaries at link time.
//
// The values default to "dev" / "unknown" so a plain `go build` (with no
// ldflags) still produces a binary that answers the question honestly rather
// than lying. `make build` stamps real values via -ldflags, e.g.:
//
//	go build -ldflags "-X github.com/CRPerdue/watchman/internal/buildinfo.Version=0.1.0 ..."
//
// Note: this is the *product* version. The event wire format has its own,
// independent version — watchman.event/v1 (see docs/event-schema.md). They are
// different things and move on different schedules.
package buildinfo

import "fmt"

// Set at link time by the Makefile; the defaults are what a bare `go build`
// yields, which is why they are honest placeholders rather than a fake number.
var (
	Version = "dev"
	Commit  = "unknown"
	Date    = "unknown"
)

// String returns a one-line, human description for a named binary, e.g.
// "watchman-central 0.1.0 (commit a1b2c3d, built 2026-09-29T20:00:00Z)". When no
// build metadata was stamped, it degrades to "watchman-central dev".
func String(name string) string {
	s := name + " " + Version
	if Commit != "unknown" || Date != "unknown" {
		s += fmt.Sprintf(" (commit %s, built %s)", Commit, Date)
	}
	return s
}

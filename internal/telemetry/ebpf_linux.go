//go:build ebpf

package telemetry

import (
	"fmt"
	"log"
)

// NewEBPFSource loads the compiled CO-RE object and returns a Source that
// streams file-open events (file.access) from its ring buffer.
//
// This file is compiled ONLY with `-tags ebpf`. It is the entry point for the
// R2.2 deliverable (docs/R2-EBPF-DESIGN.md); until the object and loader exist,
// it returns a clear "not yet implemented" error rather than pretending to
// work. The build-tagged split is what keeps the default build dependency-free.
func NewEBPFSource(cfg EBPFConfig, logger *log.Logger) (Source, error) {
	if !cfg.Enabled {
		return nil, nil
	}
	_ = logger
	// R2.2 lands here: load cfg.ObjectPath (or the embedded object), attach the
	// openat/openat2 probes, and stream filtered events into a Source. Until
	// then, fail loudly and let the collector degrade to auditd-only.
	return nil, fmt.Errorf("telemetry: ebpf source not yet implemented (R2.2)")
}

//go:build !ebpf

package telemetry

import "log"

// NewEBPFSource is the stub used when the binary is built WITHOUT the `ebpf`
// build tag (the default). It returns ErrEBPFUnsupported so the caller falls
// back to auditd, and the build never grows a clang/libbpf/kernel-headers
// dependency. This is decision D3 in docs/R2-EBPF-DESIGN.md: a stock checkout
// must build on any machine.
//
// The signature is identical to the tagged implementation, so callers compile
// unchanged whether or not eBPF is available.
func NewEBPFSource(cfg EBPFConfig, logger *log.Logger) (Source, error) {
	_ = logger
	if !cfg.Enabled {
		return nil, nil
	}
	return nil, ErrEBPFUnsupported
}

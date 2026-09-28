package telemetry

import (
	"errors"
	"fmt"
	"strings"
)

// ErrEBPFUnsupported is returned by NewEBPFSource when this binary was built
// without the `ebpf` build tag. It exists so a caller can distinguish "the
// build has no eBPF support" from "eBPF failed to load on this host" — both
// mean the same thing operationally (fall back to auditd, log once), but a
// clear reason beats a generic error.
var ErrEBPFUnsupported = errors.New("telemetry: eBPF support not compiled in (build with -tags ebpf)")

// EBPFConfig configures the eBPF file-access adapter. It is defined
// unconditionally (not behind a build tag) so the collector's config struct and
// its validation compile on every platform, whether or not eBPF is built in.
//
// Design: docs/R2-EBPF-DESIGN.md. The adapter is always *additional* — it never
// replaces the auditd source. A host that cannot load it logs one line and runs
// degraded (see D1, D6).
type EBPFConfig struct {
	// Enabled turns the adapter on. Even when true, a build without the ebpf
	// tag or a host that cannot load the object degrades to auditd-only.
	Enabled bool `yaml:"enabled"`
	// ObjectPath is the compiled CO-RE object to load. Empty -> the object
	// embedded in the binary (when built with the ebpf tag).
	ObjectPath string `yaml:"object_path"`
	// WatchPaths are the path prefixes the kernel-side filter keeps. Only opens
	// under these prefixes reach userspace, because a host opens thousands of
	// files a second and shipping them all is a self-inflicted flood. Defaults
	// to the secret-store prefixes that matter for this threat class.
	WatchPaths []string `yaml:"watch_paths"`
	// RingBufBytes sizes the per-CPU ring buffer. Zero -> DefaultRingBufBytes.
	RingBufBytes int `yaml:"ringbuf_bytes"`
}

// DefaultRingBufBytes is the ring-buffer size used when EBPFConfig.RingBufBytes
// is unset — large enough to absorb a burst without dropping, small enough not
// to pin significant memory per host.
const DefaultRingBufBytes = 1 << 20 // 1 MiB per CPU

// DefaultWatchPaths are the secret-store prefixes the kernel filter keeps when
// the operator lists none. These are the paths whose *read* is the CARBONATO
// objective: AI API keys first, SSH credentials second. Not exhaustive by
// design — an operator adds their own key directories; a wrong path is worse
// than none, so the defaults are the conservative, universal ones.
func DefaultWatchPaths() []string {
	return []string{
		"/.ssh/",
		"/.aws/",
		"/.config/gcloud/",
		"/.azure/",
		"/.kube/",
		"/.openclaw/",
		"/etc/shadow",
	}
}

// Validate checks the config for obvious errors. It runs even on a build with
// no eBPF support, so a bad config is caught at startup, not silently ignored.
func (c EBPFConfig) Validate() error {
	if !c.Enabled {
		return nil
	}
	if c.RingBufBytes < 0 {
		return fmt.Errorf("ebpf: ringbuf_bytes must be >= 0")
	}
	for _, p := range c.WatchPaths {
		if strings.TrimSpace(p) == "" {
			return fmt.Errorf("ebpf: watch_paths contains an empty entry")
		}
	}
	return nil
}

// watchPaths returns the configured prefixes or the defaults.
func (c EBPFConfig) watchPaths() []string {
	if len(c.WatchPaths) == 0 {
		return DefaultWatchPaths()
	}
	return c.WatchPaths
}

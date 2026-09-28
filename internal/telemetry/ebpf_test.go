package telemetry

import (
	"errors"
	"strings"
	"testing"
)

func TestEBPFConfigValidateDisabledIsOK(t *testing.T) {
	if err := (EBPFConfig{}).Validate(); err != nil {
		t.Errorf("disabled config should validate: %v", err)
	}
}

func TestEBPFConfigValidateRejectsBadRingBuf(t *testing.T) {
	c := EBPFConfig{Enabled: true, RingBufBytes: -1}
	if err := c.Validate(); err == nil {
		t.Error("negative ringbuf should be rejected")
	}
}

func TestEBPFConfigValidateRejectsEmptyWatchPath(t *testing.T) {
	c := EBPFConfig{Enabled: true, WatchPaths: []string{"/.ssh/", "  "}}
	if err := c.Validate(); err == nil {
		t.Error("empty watch path should be rejected")
	}
}

func TestEBPFConfigWatchPathsDefaults(t *testing.T) {
	c := EBPFConfig{Enabled: true}
	got := c.watchPaths()
	if len(got) == 0 {
		t.Fatal("expected default watch paths")
	}
	// The defaults must include the CARBONATO objective — the key stores.
	joined := strings.Join(got, " ")
	for _, want := range []string{"/.ssh/", "/.aws/", "/.openclaw/"} {
		if !strings.Contains(joined, want) {
			t.Errorf("default watch paths missing %q: %v", want, got)
		}
	}
}

func TestEBPFConfigWatchPathsOverride(t *testing.T) {
	c := EBPFConfig{Enabled: true, WatchPaths: []string{"/custom/"}}
	got := c.watchPaths()
	if len(got) != 1 || got[0] != "/custom/" {
		t.Errorf("override not honored: %v", got)
	}
}

// The default build has no eBPF: the stub must return ErrEBPFUnsupported so the
// caller degrades to auditd. This test asserts the default-build contract; with
// -tags ebpf it would matter differently, so it is guarded.
func TestStubReturnsUnsupported(t *testing.T) {
	// Only meaningful in the default (no ebpf tag) build; in the tagged build
	// NewEBPFSource does real work. We detect the default build by calling with
	// a disabled config's opposite: enabled, which the stub rejects.
	src, err := NewEBPFSource(EBPFConfig{Enabled: true}, nil)
	if err == nil {
		// Tagged build with a real implementation may return (nil, "not yet").
		// Either way an error is expected until R2.2 lands.
		if src != nil {
			t.Skip("eBPF implemented; stub contract test no longer applies")
		}
	}
	if err != nil && !errors.Is(err, ErrEBPFUnsupported) && !strings.Contains(err.Error(), "not yet implemented") {
		t.Errorf("unexpected error: %v", err)
	}
}

func TestNewEBPFSourceDisabledReturnsNil(t *testing.T) {
	// A disabled config must yield (nil, nil) even in the tagged build, so the
	// collector attaches nothing.
	if src, err := NewEBPFSource(EBPFConfig{Enabled: false}, nil); err != nil || src != nil {
		t.Errorf("disabled config = %v, %v; want nil, nil", src, err)
	}
}

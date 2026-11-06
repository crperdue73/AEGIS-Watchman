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
//
// BEAT 775 -- this test used to assert NOTHING on one path. Measured: with the
// stub mutated to `return nil, nil` (a silent no-op with no error, i.e. the D3
// degradation contract failing OPEN), this test still reported PASS. The old
// body checked `err == nil` and then fell through without a verdict whenever
// src was also nil, and its t.Skip was unreachable (it sat inside a branch that
// had already established err == nil). Finding:
// docs/FINDING-2026-11-03c-the-default-build-contract-test-asserted-nothing.md
//
// It now fails closed: an enabled config in the default build MUST return a
// non-nil, errors.Is(err, ErrEBPFUnsupported) result. Nothing else is a pass.
func TestStubReturnsUnsupported(t *testing.T) {
	src, err := NewEBPFSource(EBPFConfig{Enabled: true}, nil)

	// Distinguish the two builds WITHOUT a build-tagged constant, because this
	// test file carries no build tag and compiles under both. The tagged
	// implementation returns a Source together with its error; the stub returns
	// nil. So a non-nil src with a non-nil err means the real implementation is
	// in play and the stub contract is not in force -- printed, never silent.
	// The discriminator is the ERROR IDENTITY, not whether a Source came back:
	// measured at beat 775, the tagged implementation returns (nil, "not yet
	// implemented (R2.2)") -- a nil src -- so keying the skip on src != nil was
	// wrong and made the tagged build fail. Only the stub returns the sentinel.
	if err != nil && !errors.Is(err, ErrEBPFUnsupported) {
		t.Skipf("built with -tags ebpf: NewEBPFSource is the real implementation "+
			"(err=%v), so the default-build stub contract does not apply", err)
	}

	if err == nil {
		t.Fatalf("default build: NewEBPFSource(Enabled: true) returned a nil error "+
			"(src=%v). The stub must refuse with ErrEBPFUnsupported so callers "+
			"degrade to auditd; a nil error here means the caller silently attaches "+
			"nothing and believes it succeeded.", src)
	}
	if !errors.Is(err, ErrEBPFUnsupported) {
		t.Fatalf("default build: want errors.Is(err, ErrEBPFUnsupported), got %v", err)
	}
	if src != nil {
		t.Fatalf("default build: a refusing stub must not also return a Source, got %v", src)
	}
}

func TestNewEBPFSourceDisabledReturnsNil(t *testing.T) {
	// A disabled config must yield (nil, nil) even in the tagged build, so the
	// collector attaches nothing.
	if src, err := NewEBPFSource(EBPFConfig{Enabled: false}, nil); err != nil || src != nil {
		t.Errorf("disabled config = %v, %v; want nil, nil", src, err)
	}
}

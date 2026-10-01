package collector

import (
	"bytes"
	"context"
	"log"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/crperdue73/AEGIS-Watchman/internal/schema"
	"github.com/crperdue73/AEGIS-Watchman/internal/telemetry"
)

type fakeSubmitter struct {
	got  ActionsRequest
	err  error
	call int
}

func (f *fakeSubmitter) SubmitActions(_ context.Context, req ActionsRequest) error {
	f.call++
	f.got = req
	return f.err
}

// writeAuditChain appends a CARBONATO process chain to an audit log.
func writeAuditChain(t *testing.T, path string) {
	t.Helper()
	lines := `type=SYSCALL msg=audit(1790597000.100:900): arch=x86_64 syscall=execve success=yes pid=4242 ppid=1 uid=0 auid=1000 exe="/usr/bin/bash"
type=EXECVE msg=audit(1790597000.100:900): argc=3 a0="bash" a1="-c" a2="cat ~/.ssh/id_ed25519"
type=SYSCALL msg=audit(1790597000.200:901): arch=x86_64 syscall=execve success=yes pid=4243 ppid=4242 uid=0 auid=1000 exe="/usr/bin/nohup"
type=EXECVE msg=audit(1790597000.200:901): argc=2 a0="nohup" a1="./beacon"
`
	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	if _, err := f.WriteString(lines); err != nil {
		t.Fatal(err)
	}
}

func TestBehaviorEngine_PollShipsMatches(t *testing.T) {
	tmp := t.TempDir()
	audit := filepath.Join(tmp, "audit.log")
	if err := os.WriteFile(audit, nil, 0o644); err != nil {
		t.Fatal(err)
	}
	cfg := &Config{Behavior: Behavior{RulesDir: "../../rules", AuditLog: audit}}
	sub := &fakeSubmitter{}
	be, err := NewBehaviorEngine(cfg, schema.Host{ID: "h1"}, &schema.Agent{ID: "a1"}, sub)
	if err != nil {
		t.Fatalf("NewBehaviorEngine: %v", err)
	}

	// First poll: nothing appended yet -> no events, no submission.
	if evs := be.Poll(); len(evs) != 0 {
		t.Fatalf("expected no events before activity, got %d", len(evs))
	}
	if sub.call != 0 {
		t.Fatalf("submitter called with nothing to send")
	}

	writeAuditChain(t, audit)
	evs := be.Poll()
	if len(evs) == 0 {
		t.Fatal("expected rule matches from the CARBONATO chain")
	}
	if sub.call != 1 {
		t.Fatalf("want 1 submission, got %d", sub.call)
	}
	if len(sub.got.Events) != len(evs) {
		t.Errorf("submitted %d events, polled %d", len(sub.got.Events), len(evs))
	}
	if sub.got.Host.ID != "h1" || sub.got.Agent.ID != "a1" {
		t.Errorf("submission attribution = %+v / %+v", sub.got.Host, sub.got.Agent)
	}

	// Second poll with no new bytes: quiet, no second submission.
	if evs := be.Poll(); len(evs) != 0 {
		t.Errorf("expected no new events on unchanged log, got %d", len(evs))
	}
	if sub.call != 1 {
		t.Errorf("submitter called again with no new data (call=%d)", sub.call)
	}
}

// TestBehaviorEngine_EBPFUnavailableDegradesToAuditdOnly is the half of the P2.0
// acceptance (docs/R2-EBPF-DESIGN.md, D6) that needs no toolchain and no kernel:
// when the optional eBPF source cannot be compiled in (the default build) or
// cannot load, the collector must log one clear line an operator can act on and
// keep running on auditd — never refuse to start because its *second* source is
// missing. The other half (the object loads and emits file.access) is gated on a
// toolchain; this half runs on every host, so it is pinned here.
func TestBehaviorEngine_EBPFUnavailableDegradesToAuditdOnly(t *testing.T) {
	tmp := t.TempDir()
	audit := filepath.Join(tmp, "audit.log")
	if err := os.WriteFile(audit, nil, 0o644); err != nil {
		t.Fatal(err)
	}
	cfg := &Config{Behavior: Behavior{
		RulesDir: "../../rules",
		AuditLog: audit,
		EBPF:     telemetry.EBPFConfig{Enabled: true},
	}}

	// The engine and the adapter log through the standard logger; capture it to
	// assert on the operator-visible line, then restore.
	var buf bytes.Buffer
	prev := log.Writer()
	log.SetOutput(&buf)
	defer log.SetOutput(prev)

	be, err := NewBehaviorEngine(cfg, schema.Host{ID: "h1"}, &schema.Agent{ID: "a1"}, &fakeSubmitter{})
	if err != nil {
		t.Fatalf("an unavailable eBPF source must degrade, not fail startup: %v", err)
	}
	logged := buf.String()
	if !strings.Contains(logged, "eBPF file.access adapter unavailable") {
		t.Errorf("expected a one-line degrade notice, got:\n%s", logged)
	}
	if strings.Contains(logged, "adapter attached") {
		t.Errorf("nothing should have been attached in this build:\n%s", logged)
	}

	// The auditd path must be intact: a real CARBONATO chain still yields matches
	// after the eBPF source failed to come up.
	writeAuditChain(t, audit)
	if evs := be.Poll(); len(evs) == 0 {
		t.Fatal("collector went blind: auditd matches missing after eBPF degrade")
	}

	// Disabled must be silent — no scary line for an operator who never asked for
	// the source. (The disabled contract is also (nil, nil) at the adapter level.)
	buf.Reset()
	cfg2 := &Config{Behavior: Behavior{RulesDir: "../../rules", AuditLog: audit}}
	if _, err := NewBehaviorEngine(cfg2, schema.Host{ID: "h1"}, nil, &fakeSubmitter{}); err != nil {
		t.Fatalf("disabled eBPF: %v", err)
	}
	if strings.Contains(buf.String(), "eBPF") {
		t.Errorf("a disabled eBPF config should log nothing about eBPF:\n%s", buf.String())
	}
}

// TestBehaviorEngineRejectsEmptyRulesDir pins the silent-blind-spot guard: a
// rules_dir that exists but holds no rules must fail startup, not start Layer 3
// with nothing it can ever fire.
func TestBehaviorEngineRejectsEmptyRulesDir(t *testing.T) {
	dir := t.TempDir() // exists, but empty
	cfg := &Config{Behavior: Behavior{RulesDir: dir}}
	if _, err := NewBehaviorEngine(cfg, schema.Host{ID: "h1"}, nil, &fakeSubmitter{}); err == nil {
		t.Fatal("expected an error for an empty rules dir")
	}
}

func TestBehaviorEngine_SubmitErrorDoesNotPanic(t *testing.T) {
	tmp := t.TempDir()
	audit := filepath.Join(tmp, "audit.log")
	if err := os.WriteFile(audit, nil, 0o644); err != nil {
		t.Fatal(err)
	}
	cfg := &Config{Behavior: Behavior{RulesDir: "../../rules", AuditLog: audit}}
	sub := &fakeSubmitter{err: context.DeadlineExceeded}
	be, err := NewBehaviorEngine(cfg, schema.Host{ID: "h1"}, nil, sub)
	if err != nil {
		t.Fatalf("NewBehaviorEngine: %v", err)
	}
	writeAuditChain(t, audit)
	// A central outage must log-and-continue, not crash the collector.
	if evs := be.Poll(); len(evs) == 0 {
		t.Fatal("events should still be returned even if shipping fails")
	}
}

// TestBehaviorEngine_ObserveDriftFeedsTimeline covers the wiring that makes
// correlation rules possible on a live collector: Layer 1/2 drift events must
// land in the same timeline Layer 3 consults, or "egress after identity change"
// can never fire.
func TestBehaviorEngine_ObserveDriftFeedsTimeline(t *testing.T) {
	tmp := t.TempDir()
	audit := filepath.Join(tmp, "audit.log")
	if err := os.WriteFile(audit, nil, 0o644); err != nil {
		t.Fatal(err)
	}
	cfg := &Config{Behavior: Behavior{RulesDir: "../../rules", AuditLog: audit}}
	be, err := NewBehaviorEngine(cfg, schema.Host{ID: "h1"}, &schema.Agent{ID: "a1"}, &fakeSubmitter{})
	if err != nil {
		t.Fatalf("NewBehaviorEngine: %v", err)
	}
	if be.timeline == nil {
		t.Fatal("behavior engine must own a timeline")
	}

	if n := be.timeline.Len("h1"); n != 0 {
		t.Fatalf("timeline should start empty, len=%d", n)
	}
	be.ObserveDrift([]schema.Event{{
		Event: schema.EventIdentityDrift,
		Host:  schema.Host{ID: "h1"},
	}})
	if n := be.timeline.Len("h1"); n != 1 {
		t.Fatalf("drift not recorded into timeline, len=%d", n)
	}

	// Nil engine (Layer 3 off) must no-op rather than panic.
	var nilBE *BehaviorEngine
	nilBE.ObserveDrift([]schema.Event{{Event: schema.EventIdentityDrift, Host: schema.Host{ID: "h1"}}})
}

// TestSourceWatchReportsEachTransitionOnce pins the runtime counterpart to the
// startup audit_log check: a primary source that goes away mid-run is reported
// on the transition (and on its return), never once per poll.
func TestSourceWatchReportsEachTransitionOnce(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "audit.log")
	if err := os.WriteFile(path, []byte("x\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	w := &sourceWatch{name: "audit_log", path: path}

	// Present and steady: silent.
	if msg, changed := w.observe(); changed {
		t.Fatalf("steady source reported a change: %q", msg)
	}
	if _, changed := w.observe(); changed {
		t.Fatal("steady source kept reporting")
	}

	// Vanishes: reported exactly once.
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	msg, changed := w.observe()
	if !changed || !strings.Contains(msg, "disappeared") {
		t.Fatalf("expected a disappearance report, got changed=%v msg=%q", changed, msg)
	}
	if _, changed := w.observe(); changed {
		t.Fatal("disappearance reported more than once")
	}

	// Returns: reported exactly once, then silent again.
	if err := os.WriteFile(path, []byte("y\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	msg, changed = w.observe()
	if !changed || !strings.Contains(msg, "back") {
		t.Fatalf("expected a recovery report, got changed=%v msg=%q", changed, msg)
	}
	if _, changed := w.observe(); changed {
		t.Fatal("recovery reported more than once")
	}
}

// A source already absent on the very first check is reported once (a caller who
// built the engine without LoadConfig's existence check), then stays quiet.
func TestSourceWatchReportsAbsentOnFirstCheck(t *testing.T) {
	w := &sourceWatch{name: "audit_log", path: filepath.Join(t.TempDir(), "gone.log")}
	msg, changed := w.observe()
	if !changed || !strings.Contains(msg, "not readable") {
		t.Fatalf("expected a first-check absence report, got changed=%v msg=%q", changed, msg)
	}
	if _, changed := w.observe(); changed {
		t.Fatal("absent-first check repeated itself")
	}
}

// The engine must actually surface the watch: losing the audit log mid-run has
// to emit one loud line, not vanish silently.
func TestBehaviorEngineWarnsWhenAuditLogVanishes(t *testing.T) {
	tmp := t.TempDir()
	audit := filepath.Join(tmp, "audit.log")
	if err := os.WriteFile(audit, nil, 0o644); err != nil {
		t.Fatal(err)
	}
	cfg := &Config{Behavior: Behavior{RulesDir: "../../rules", AuditLog: audit}}
	be, err := NewBehaviorEngine(cfg, schema.Host{ID: "h1"}, &schema.Agent{ID: "a1"}, &fakeSubmitter{})
	if err != nil {
		t.Fatalf("NewBehaviorEngine: %v", err)
	}

	var buf bytes.Buffer
	log.SetOutput(&buf)
	defer log.SetOutput(os.Stderr)

	be.Poll() // source present: nothing to say
	if strings.Contains(buf.String(), "primary source") {
		t.Fatalf("spoke up about a present source: %q", buf.String())
	}

	buf.Reset()
	if err := os.Remove(audit); err != nil {
		t.Fatal(err)
	}
	be.Poll()
	if !strings.Contains(buf.String(), "disappeared") {
		t.Fatalf("missing source went unreported: %q", buf.String())
	}

	buf.Reset()
	be.Poll() // still gone: no repeat
	if strings.Contains(buf.String(), "disappeared") {
		t.Fatalf("missing source reported every poll: %q", buf.String())
	}
}

// The scope shim's persistence path follows the cursor's derivation: explicit
// config wins, else it rides beside the spool, else it is off (in-memory only).
func TestScopeSeenPathDerivation(t *testing.T) {
	cases := []struct {
		name string
		cfg  Config
		want string
	}{
		{"explicit", Config{Behavior: Behavior{Scope: telemetry.ScopeConfig{Seen: "/x/seen.json"}}}, "/x/seen.json"},
		{"from spool", Config{Behavior: Behavior{Spool: "/var/lib/watchman/spool.jsonl"}}, "/var/lib/watchman/spool.jsonl.scope-seen"},
		{"off", Config{}, ""},
		{"explicit wins over spool", Config{Behavior: Behavior{Spool: "/s", Scope: telemetry.ScopeConfig{Seen: "/e"}}}, "/e"},
	}
	for _, c := range cases {
		if got := scopeSeenPath(&c.cfg); got != c.want {
			t.Errorf("%s: scopeSeenPath = %q, want %q", c.name, got, c.want)
		}
	}
}

// TestBehaviorEngine_ReportsInertRulesOnStockBuild pins the operator-visible
// half of the coverage accounting: on a stock build (auditd + the default-on
// scope shim; eBPF and docker both off) the collector must name the rules that
// cannot fire, so its pack count is never mistaken for coverage. See
// internal/rules/coverage.go and docs/rules.md.
func TestBehaviorEngine_ReportsInertRulesOnStockBuild(t *testing.T) {
	tmp := t.TempDir()
	audit := filepath.Join(tmp, "audit.log")
	if err := os.WriteFile(audit, nil, 0o644); err != nil {
		t.Fatal(err)
	}
	cfg := &Config{Behavior: Behavior{
		RulesDir: "../../rules",
		AuditLog: audit,
		// EBPF and Docker left disabled: the stock build.
	}}

	var buf bytes.Buffer
	prev := log.Writer()
	log.SetOutput(&buf)
	defer log.SetOutput(prev)

	if _, err := NewBehaviorEngine(cfg, schema.Host{ID: "h1"}, &schema.Agent{ID: "a1"}, &fakeSubmitter{}); err != nil {
		t.Fatalf("NewBehaviorEngine: %v", err)
	}
	logged := buf.String()

	// The shipped pack: 20 rules, 6 of them keyed on an action class a stock
	// build does not emit (three file.access, three container.start).
	if !strings.Contains(logged, "6 of 20 loaded rule(s) cannot fire with the attached sources") {
		t.Errorf("expected the stock-build inert count in startup logs, got:\n%s", logged)
	}
	for _, id := range []string{"carbonato.secret.read_model_key", "carbonato.container.privileged_start"} {
		if !strings.Contains(logged, id) {
			t.Errorf("inert rule %q not named in startup logs:\n%s", id, logged)
		}
	}
	// A rule that does fire on a stock build must not be listed as inert.
	if strings.Contains(logged, "carbonato.exfil.egress_after_identity_change") {
		t.Errorf("a live rule was wrongly reported inert:\n%s", logged)
	}
}

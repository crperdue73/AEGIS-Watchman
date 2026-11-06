package collector

import (
	"bytes"
	"context"
	"errors"
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

// TestBehaviorEngine_DockerDisabledIsSilentAndInert pins the container adapter's
// disabled contract at the COMPOSER level (the adapter-level (nil, nil) contract
// is covered in internal/telemetry). Beat 816: gomutate found `case dockerSrc ==
// nil:` and `if cfg.Behavior.Docker.Enabled` were both mutable without any test
// going red -- the enabled-and-failing path was tested, the disabled path never.
func TestBehaviorEngine_DockerDisabledIsSilentAndInert(t *testing.T) {
	tmp := t.TempDir()
	audit := filepath.Join(tmp, "audit.log")
	if err := os.WriteFile(audit, nil, 0o644); err != nil {
		t.Fatal(err)
	}
	cfg := &Config{Behavior: Behavior{RulesDir: "../../rules", AuditLog: audit}}

	var buf bytes.Buffer
	prev := log.Writer()
	log.SetOutput(&buf)
	defer log.SetOutput(prev)

	if _, err := NewBehaviorEngine(cfg, schema.Host{ID: "h1"}, nil, &fakeSubmitter{}); err != nil {
		t.Fatalf("NewBehaviorEngine: %v", err)
	}
	logged := buf.String()
	if strings.Contains(logged, "docker events adapter") {
		t.Errorf("a disabled docker config must log nothing about the adapter:\n%s", logged)
	}
	// With no docker source, container.start rules are inert -- that is the
	// observable consequence of "not attached", and it is what goes wrong if the
	// enabled check is inverted.
	if !strings.Contains(logged, "carbonato.container.") {
		t.Errorf("container rules should be reported inert with no docker source:\n%s", logged)
	}
}

// TestBehaviorEngine_DockerBuildFailureDegradesNotFails pins the error arm of the
// container adapter. A privileged detector that refuses to start because its
// OPTIONAL second source is misconfigured goes blind entirely; the documented
// contract (D6) is degraded, named, and still running. Beat 816: mutating
// `case err != nil:`->`case err == nil:` left the suite green.
func TestBehaviorEngine_DockerBuildFailureDegradesNotFails(t *testing.T) {
	tmp := t.TempDir()
	audit := filepath.Join(tmp, "audit.log")
	if err := os.WriteFile(audit, nil, 0o644); err != nil {
		t.Fatal(err)
	}
	cfg := &Config{Behavior: Behavior{
		RulesDir: "../../rules",
		AuditLog: audit,
		// Enabled with a bin that cannot be honoured, so NewDockerSource returns
		// a real error rather than (nil, nil).
		Docker: telemetry.DockerConfig{Enabled: true, Bin: "   "},
	}}

	var buf bytes.Buffer
	prev := log.Writer()
	log.SetOutput(&buf)
	defer log.SetOutput(prev)

	be, err := NewBehaviorEngine(cfg, schema.Host{ID: "h1"}, nil, &fakeSubmitter{})
	if err != nil {
		t.Fatalf("a broken docker config must degrade, not fail startup: %v", err)
	}
	logged := buf.String()
	if !strings.Contains(logged, "docker events adapter unavailable") {
		t.Errorf("expected a one-line degrade notice naming the docker failure, got:\n%s", logged)
	}
	if strings.Contains(logged, "docker events adapter attached") {
		t.Errorf("a failed adapter must not be reported attached:\n%s", logged)
	}

	// The auditd path must be intact after the degrade: detection does not stop
	// because a second source did.
	writeAuditChain(t, audit)
	if evs := be.Poll(); len(evs) == 0 {
		t.Fatal("collector went blind: auditd matches missing after docker degrade")
	}
}

// TestDockerNilArmIsUnreachable pins an EQUIVALENT MUTANT, measured rather than
// argued. Beat 816: gomutate flagged `case dockerSrc == nil:` as mutable with no
// test going red. The reason is not a weak test -- the arm cannot be reached:
// NewDockerSource returns (nil, nil) ONLY when !cfg.Enabled, and this switch is
// already inside `if cfg.Behavior.Docker.Enabled`. Corrupting the comparison
// changes no behaviour, which is exactly why it survived.
//
// This is a TRIPWIRE, not an endorsement. If docker.go ever gains a second way to
// return (nil, nil) while enabled, the first half goes red by name and whoever
// changed it has to decide what the collector should do.
func TestDockerNilArmIsUnreachable(t *testing.T) {
	// Half 1: the only (nil, nil) early return is the disabled one.
	src, err := os.ReadFile(filepath.Join("..", "telemetry", "docker.go"))
	if err != nil {
		t.Fatalf("read docker.go: %v", err)
	}
	body := src
	if i := strings.Index(string(src), "func NewDockerSource(cfg DockerConfig)"); i >= 0 {
		body = src[i:]
	}
	if j := strings.Index(string(body), "\nfunc CLIDockerInspector"); j > 0 {
		body = body[:j]
	}
	nilNil := strings.Count(string(body), "return nil, nil")
	if nilNil != 1 {
		t.Fatalf("NewDockerSource has %d `return nil, nil` sites, want exactly 1 (the disabled arm). "+
			"A new one means the collector's `case dockerSrc == nil` branch may now be REACHABLE and "+
			"the equivalence argument in this test no longer holds:\n%s", nilNil, body)
	}
	if !strings.Contains(string(body), "if !cfg.Enabled {\n\t\treturn nil, nil") {
		t.Errorf("the single (nil, nil) return is no longer the `!cfg.Enabled` arm -- re-derive the "+
			"equivalence argument before trusting it:\n%s", body)
	}

	// Half 2 (NEGATIVE CONTROL): the disabled path is the ONLY way to reach the
	// nil arm, and it produces an inert engine with no container source attached.
	tmp := t.TempDir()
	audit := filepath.Join(tmp, "audit.log")
	if err := os.WriteFile(audit, nil, 0o644); err != nil {
		t.Fatal(err)
	}
	var buf bytes.Buffer
	prev := log.Writer()
	log.SetOutput(&buf)
	defer log.SetOutput(prev)

	cfg := &Config{Behavior: Behavior{RulesDir: "../../rules", AuditLog: audit,
		Docker: telemetry.DockerConfig{Enabled: false}}}
	if _, err := NewBehaviorEngine(cfg, schema.Host{ID: "h1"}, nil, &fakeSubmitter{}); err != nil {
		t.Fatalf("disabled docker must not fail startup: %v", err)
	}
	if strings.Contains(buf.String(), "docker events adapter attached") {
		t.Errorf("the disabled arm must not attach:\n%s", buf.String())
	}
}

// TestEBPFNilArmIsUnreachable is the twin of TestDockerNilArmIsUnreachable, and for
// the same measured reason: gomutate flagged `case ebpfSrc == nil:` with the suite
// staying green. NewEBPFSource returns (nil, nil) only when !cfg.Enabled -- in BOTH
// the stub build and the `-tags ebpf` build, which are the only two implementations
// of that name in the tree. The offending switch is inside  `if
// cfg.Behavior.EBPF.Enabled`, so the arm cannot be entered and corrupting it is an
// equivalent mutant, not a gap in the tests.
//
// TRIPWIRE: if a future implementation returns (nil, nil) for an ENABLED config,
// this goes red by name, because the collector would then silently skip a source
// the operator explicitly asked for.
func TestEBPFNilArmIsUnreachable(t *testing.T) {
	impls := []struct{ file, what string }{
		{filepath.Join("..", "telemetry", "ebpf_stub.go"), "stub build"},
		{filepath.Join("..", "telemetry", "ebpf_linux.go"), "`-tags ebpf` build"},
	}
	for _, impl := range impls {
		src, err := os.ReadFile(impl.file)
		if err != nil {
			t.Fatalf("read %s: %v", impl.file, err)
		}
		text := string(src)
		if n := strings.Count(text, "return nil, nil"); n != 1 {
			t.Errorf("%s has %d `return nil, nil` sites, want exactly 1: a second one may make the "+
				"collector's nil arm reachable for an ENABLED config", impl.file, n)
		}
		if !strings.Contains(text, "if !cfg.Enabled {\n\t\treturn nil, nil") {
			t.Errorf("%s: the (nil, nil) return is no longer the `!cfg.Enabled` arm, so the "+
				"equivalence argument above needs re-deriving", impl.file)
		}
	}
}

// TestBehaviorEngine_CorruptScopeSeenFileLogsAndKeepsRunning pins the error arm of
// the scope.change persistence path. `NewBehaviorEngine`'s own comment promises
// "Best-effort: a read failure is logged and the shim runs in-memory, never
// aborting startup." Beat 817: gomutate showed `if err := shim.PersistSeen(p); err
// != nil` could be inverted with nothing going red -- the promise was documented
// and unwitnessed, the same shape as the docker arm in beat 816.
//
// The failure is induced the way it actually happens on a box: the seen-file
// exists but holds something that is not the JSON array it should be (a truncated
// write, a disk that filled, a human editing it). os.ReadFile succeeds, so this
// is the unmarshal arm, not the missing-file arm -- which is the arm that
// legitimately returns nil.
func TestBehaviorEngine_CorruptScopeSeenFileLogsAndKeepsRunning(t *testing.T) {
	tmp := t.TempDir()
	audit := filepath.Join(tmp, "audit.log")
	if err := os.WriteFile(audit, nil, 0o644); err != nil {
		t.Fatal(err)
	}
	seen := filepath.Join(tmp, "scope-seen.json")
	if err := os.WriteFile(seen, []byte(`["net.egress", "proc`), 0o644); err != nil {
		t.Fatal(err)
	}

	cfg := &Config{Behavior: Behavior{
		RulesDir: "../../rules",
		AuditLog: audit,
		Scope:    telemetry.ScopeConfig{Seen: seen},
	}}

	var buf bytes.Buffer
	prev := log.Writer()
	log.SetOutput(&buf)
	defer log.SetOutput(prev)

	be, err := NewBehaviorEngine(cfg, schema.Host{ID: "h1"}, nil, &fakeSubmitter{})
	if err != nil {
		t.Fatalf("an unreadable seen-file must not abort startup: %v", err)
	}

	// 1. The operator is told. Silence here means a host silently lost its
	//    baseline and will re-report established capabilities as novel.
	logged := buf.String()
	if !strings.Contains(logged, "seen file") {
		t.Errorf("expected the seen-file read failure to be logged, got:\n%s", logged)
	}
	if !strings.Contains(logged, "starting empty") {
		t.Errorf("the log line should say what the collector did about it (start empty), got:\n%s", logged)
	}

	// 2. It kept running AND the shim is attached -- degraded is not detached.
	if !strings.Contains(logged, "scope.change heuristic shim attached") {
		t.Errorf("a corrupt seen-file must not detach the shim, got:\n%s", logged)
	}

	// 3. Detection still works end to end through the composed source.
	writeAuditChain(t, audit)
	if evs := be.Poll(); len(evs) == 0 {
		t.Fatal("collector went blind: auditd matches missing after a corrupt seen-file")
	}
}

// TestBehaviorEngine_RestoresCursorAndDoesNotReplay pins the one line that makes
// restart-safe reading real: `auditSrc.SetCursor(cur)` at behavior.go:166. Beat 817:
// gomutate could `!=` -> `==` that guard and NOTHING went red, because SetCursor was
// never called by any test in the tree -- the collector's "once-per-restart-and-
// growing" duplicate-alert failure (internal/telemetry/engine.go) had no witness.
//
// This is an end-to-end assertion, not a call-recording one: it proves the cursor
// is WIRED through NewBehaviorEngine by showing the behaviour it buys -- a second
// engine over the same audit log and cursor sees nothing, because the offset was
// restored. Inverting the guard disables the cursor, the log is replayed, and the
// second engine reports the same CARBONATO chain again.
func TestBehaviorEngine_RestoresCursorAndDoesNotReplay(t *testing.T) {
	tmp := t.TempDir()
	audit := filepath.Join(tmp, "audit.log")
	cursor := filepath.Join(tmp, "audit.cursor")
	if err := os.WriteFile(audit, nil, 0o644); err != nil {
		t.Fatal(err)
	}
	cfg := &Config{Behavior: Behavior{RulesDir: "../../rules", AuditLog: audit, Cursor: cursor}}

	// First engine consumes a real chain and persists its offset.
	first, err := NewBehaviorEngine(cfg, schema.Host{ID: "h1"}, nil, &fakeSubmitter{})
	if err != nil {
		t.Fatalf("NewBehaviorEngine (first): %v", err)
	}
	writeAuditChain(t, audit)
	if evs := first.Poll(); len(evs) == 0 {
		t.Fatal("first engine found nothing -- the chain did not fire, so this test proves nothing")
	}
	// (BehaviorEngine has no Close; the FileSource is not left running.)

	// The cursor file is the evidence that the guard let SetCursor run at all.
	st, err := os.Stat(cursor)
	if err != nil {
		t.Fatalf("no cursor file was written at %q: SetCursor was never called, so a restart "+
			"re-reads the whole log and re-fires every retained match: %v", cursor, err)
	}
	if st.Size() == 0 {
		t.Errorf("cursor file %q is empty", cursor)
	}

	// Second engine: same log, same cursor. Nothing new was appended, so a
	// restored offset means silence -- and a lost offset means a full replay.
	var buf bytes.Buffer
	prev := log.Writer()
	log.SetOutput(&buf)
	defer log.SetOutput(prev)
	second, err := NewBehaviorEngine(cfg, schema.Host{ID: "h1"}, nil, &fakeSubmitter{})
	if err != nil {
		t.Fatalf("NewBehaviorEngine (second): %v", err)
	}
	if evs := second.Poll(); len(evs) != 0 {
		t.Errorf("restart replayed %d match(es) from an already-read log -- the offset was not "+
			"restored, which is the duplicate-alert flood the cursor exists to prevent", len(evs))
	}
}

// TestBehaviorEngine_AnnouncesPinnedSetOnStartup pins behavior.go:155. Pin ENFORCEMENT
// was already tested (a drifted pack refuses to start, an unpinned pack refuses to
// start); what was never tested is the success path SAYING SO. Beat 817: gomutate
// could invert that guard with the suite green.
//
// Why it matters beyond tidiness: the pin file is the only evidence an operator has
// that the rules they audited are the rules that are running. "The engine started"
// and "the engine started against the set you pinned" are different claims, and
// only the second one closes the supply-chain question. Silence here makes them
// indistinguishable in the log -- the one place an operator would look after an
// incident.
func TestBehaviorEngine_AnnouncesPinnedSetOnStartup(t *testing.T) {
	dir, pinsPath := pinnedRulesDir(t)
	cfg := &Config{Behavior: Behavior{RulesDir: dir, Pins: pinsPath}}

	var buf bytes.Buffer
	prev := log.Writer()
	log.SetOutput(&buf)
	defer log.SetOutput(prev)

	if _, err := NewBehaviorEngine(cfg, schema.Host{ID: "h"}, nil, &fakeSubmitter{}); err != nil {
		t.Fatalf("a matching set must start: %v", err)
	}
	logged := buf.String()
	if !strings.Contains(logged, "matches pinned set") {
		t.Errorf("startup did not announce that the loaded pack set matches the pinned set -- an "+
			"operator cannot tell 'ran my audited rules' from 'ran something else' in the log:\n%s", logged)
	}
	if !strings.Contains(logged, pinsPath) {
		t.Errorf("the announcement must name the pin file it was checked against (%q), got:\n%s",
			pinsPath, logged)
	}

	// Negative control: no pins configured -> no claim of one. A log line that
	// printed unconditionally would pass the assertions above and lie.
	buf.Reset()
	noPins := &Config{Behavior: Behavior{RulesDir: dir}}
	if _, err := NewBehaviorEngine(noPins, schema.Host{ID: "h"}, nil, &fakeSubmitter{}); err != nil {
		t.Fatalf("unpinned config must still start: %v", err)
	}
	if strings.Contains(buf.String(), "matches pinned set") {
		t.Errorf("claimed a pin match with no pins configured:\n%s", buf.String())
	}
}

// TestBehaviorEngine_SpoolAppendFailureIsLoud pins behavior.go:418.
//
// The branch exists for one reason, and the comment above it says so: "A spool failure is loud:
// it means the durability guarantee is broken." The mutant inverts the check, so the collector
// logs spool-append errors only when the append SUCCEEDED -- and says nothing at all when the
// write that was supposed to make this evidence survive a crash failed. Events are still returned
// to the caller, the poll looks healthy, and the missing durability shows up later as events that
// were seen in a live console and absent from the record.
//
// The failure is injected without permissions (this box runs as root): the SPOOL PATH'S PARENT is
// created as a regular file, so os.MkdirAll fails for any uid.
func TestBehaviorEngine_SpoolAppendFailureIsLoud(t *testing.T) {
	tmp := t.TempDir()
	audit := filepath.Join(tmp, "audit.log")
	if err := os.WriteFile(audit, nil, 0o644); err != nil {
		t.Fatal(err)
	}
	blocker := filepath.Join(tmp, "blocked")
	if err := os.WriteFile(blocker, []byte("not a directory\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	spoolPath := filepath.Join(blocker, "spool.jsonl")

	cfg := &Config{Behavior: Behavior{
		RulesDir: "../../rules",
		AuditLog: audit,
		Spool:    spoolPath,
	}}

	var buf bytes.Buffer
	prev := log.Writer()
	log.SetOutput(&buf)
	defer log.SetOutput(prev)

	be, err := NewBehaviorEngine(cfg, schema.Host{ID: "h1"}, &schema.Agent{ID: "a1"}, &fakeSubmitter{})
	if err != nil {
		t.Fatalf("NewBehaviorEngine: %v", err)
	}

	writeAuditChain(t, audit)
	evs := be.Poll()
	if len(evs) == 0 {
		t.Fatal("expected rule matches from the CARBONATO chain")
	}

	logged := buf.String()
	if !strings.Contains(logged, "spool append") {
		t.Fatalf("the spool could not be written (its parent is a regular file) but the failure "+
			"was not logged -- the durability guarantee broke silently:\n%s", logged)
	}
	if !strings.Contains(logged, "mkdir") {
		t.Errorf("the log line does not name the underlying cause:\n%s", logged)
	}
}

// TestBehaviorEngine_NoSpoolSubmitFailureIsLoud pins behavior.go:426 -- the same inversion as
// :418, on the other branch. With no spool configured, matches ship best-effort, and a submit
// failure is the ONLY evidence that central did not receive them. The mutant logs on success and
// stays silent on failure, so a dev setup whose central endpoint is down looks exactly like one
// where everything was delivered.
func TestBehaviorEngine_NoSpoolSubmitFailureIsLoud(t *testing.T) {
	tmp := t.TempDir()
	audit := filepath.Join(tmp, "audit.log")
	if err := os.WriteFile(audit, nil, 0o644); err != nil {
		t.Fatal(err)
	}
	cfg := &Config{Behavior: Behavior{RulesDir: "../../rules", AuditLog: audit}}

	var buf bytes.Buffer
	prev := log.Writer()
	log.SetOutput(&buf)
	defer log.SetOutput(prev)

	sub := &fakeSubmitter{err: errors.New("central unreachable")}
	be, err := NewBehaviorEngine(cfg, schema.Host{ID: "h1"}, &schema.Agent{ID: "a1"}, sub)
	if err != nil {
		t.Fatalf("NewBehaviorEngine: %v", err)
	}

	writeAuditChain(t, audit)
	if evs := be.Poll(); len(evs) == 0 {
		t.Fatal("expected rule matches from the CARBONATO chain")
	}
	if sub.call == 0 {
		t.Fatal("the submitter was never called, so this test proves nothing")
	}

	logged := buf.String()
	if !strings.Contains(logged, "submit actions") {
		t.Fatalf("submitting to central failed and nothing was logged; with no spool configured "+
			"this is the only signal that the events were lost:\n%s", logged)
	}
	if !strings.Contains(logged, "central unreachable") {
		t.Errorf("the log line does not name the cause:\n%s", logged)
	}
}

// TestBehaviorEngine_DrainFailureIsLoud pins behavior.go:448 -- drain's error branch.
//
// drain is the retry path: spooled batches are resubmitted each cycle until central takes them.
// If the drain's error is not logged, a permanently failing drain is indistinguishable from an
// empty spool -- the agent looks quiet while every event it ever collected sits undelivered.
func TestBehaviorEngine_DrainFailureIsLoud(t *testing.T) {
	tmp := t.TempDir()
	audit := filepath.Join(tmp, "audit.log")
	if err := os.WriteFile(audit, nil, 0o644); err != nil {
		t.Fatal(err)
	}
	spoolPath := filepath.Join(tmp, "spool.jsonl")
	if err := os.MkdirAll(filepath.Dir(spoolPath), 0o755); err != nil {
		t.Fatal(err)
	}

	cfg := &Config{Behavior: Behavior{RulesDir: "../../rules", AuditLog: audit, Spool: spoolPath}}

	sub := &fakeSubmitter{err: errors.New("central unreachable")}
	be, err := NewBehaviorEngine(cfg, schema.Host{ID: "h1"}, &schema.Agent{ID: "a1"}, sub)
	if err != nil {
		t.Fatalf("NewBehaviorEngine: %v", err)
	}

	// Get one real batch into the spool so drain has something to try.
	writeAuditChain(t, audit)
	if evs := be.Poll(); len(evs) == 0 {
		t.Fatal("expected rule matches from the CARBONATO chain")
	}
	sub.err = errors.New("central unreachable")

	var buf bytes.Buffer
	prev := log.Writer()
	log.SetOutput(&buf)
	defer log.SetOutput(prev)

	be.Poll() // drain runs at the end of every poll

	logged := buf.String()
	// Assert on the CAUSE, not the prefix. My first version checked only for "spool drain" -- and
	// the mutant ALSO prints "collector: spool drain: <nil>", because inverting the check makes
	// the branch run on success and print a nil error. The prefix matched, the test went green
	// against the defect it was written for. The string that distinguishes the two is the error.
	if !strings.Contains(logged, "central unreachable") {
		t.Fatalf("draining the spool failed but the log carries no cause -- undelivered evidence "+
			"would look like an idle agent:\n%s", logged)
	}
	if strings.Contains(logged, "spool drain: <nil>") {
		t.Errorf("drain reported an error branch with a nil cause:\n%s", logged)
	}
}

// TestBehaviorEngine_DrainCountIsReported pins behavior.go:452 -- `if n > 0`.
//
// The success line is how an operator knows the spool moved this cycle. Skipping it when n == 0
// is correct (nothing shipped, nothing to say); skipping it when n > 0 hides the only positive
// confirmation that a backlog cleared. The mutant `>= 0` prints "shipped 0 spooled events" every
// cycle, which trains the reader to ignore the line -- so this asserts BOTH directions: nothing
// noisy on a quiet cycle, a count on a shipping one.
func TestBehaviorEngine_DrainCountIsReported(t *testing.T) {
	tmp := t.TempDir()
	audit := filepath.Join(tmp, "audit.log")
	if err := os.WriteFile(audit, nil, 0o644); err != nil {
		t.Fatal(err)
	}
	spoolPath := filepath.Join(tmp, "spool.jsonl")
	if err := os.MkdirAll(filepath.Dir(spoolPath), 0o755); err != nil {
		t.Fatal(err)
	}
	cfg := &Config{Behavior: Behavior{RulesDir: "../../rules", AuditLog: audit, Spool: spoolPath}}

	sub := &fakeSubmitter{}
	be, err := NewBehaviorEngine(cfg, schema.Host{ID: "h1"}, &schema.Agent{ID: "a1"}, sub)
	if err != nil {
		t.Fatalf("NewBehaviorEngine: %v", err)
	}

	writeAuditChain(t, audit)

	// Poll spools the batch AND drains it in the same call -- I wrote this test expecting the
	// drain to happen on the NEXT poll, and it failed on correct code because of course it did:
	// the shipped line is printed by the poll that produced the events. Measured, not assumed.
	var withEvents bytes.Buffer
	prev := log.Writer()
	log.SetOutput(&withEvents)
	if evs := be.Poll(); len(evs) == 0 {
		log.SetOutput(prev)
		t.Fatal("expected rule matches from the CARBONATO chain")
	}
	log.SetOutput(prev)

	if !strings.Contains(withEvents.String(), "shipped 3 spooled events") {
		t.Fatalf("three events were spooled and drained but no shipped count was logged:\n%s",
			withEvents.String())
	}
	if strings.Contains(withEvents.String(), "shipped 0 ") {
		t.Errorf("a count of zero was reported on a cycle that shipped:\n%s", withEvents.String())
	}

	// And an empty spool must stay silent about shipping.
	var empty bytes.Buffer
	log.SetOutput(&empty)
	be.Poll()
	log.SetOutput(prev)
	if strings.Contains(empty.String(), "shipped") {
		t.Errorf("an empty spool produced a shipped line:\n%s", empty.String())
	}
}

// TestBehaviorEngine_EveryMatchCarriesItsRule is a PIN, not a kill attempt.
//
// behavior.go:412 reads `if ev.Rule != nil { id = ev.Rule.ID }`. I could not reach the false arm
// and I am not going to manufacture it: rules/engine.go:80 builds every matched event with
// `ev.Rule = &schema.Rule{...}` unconditionally, so a rule match that lacks its rule does not
// exist in this codebase. The guard is defensive against a caller there is not one of.
//
// What I CAN pin is the invariant the dead arm exists to protect: every event the engine returns
// names its rule. If someone later makes Rule optional -- or builds an Event without it -- this is
// what goes red, instead of a log line printing `RULE process.exec  (severity=high)` with a hole
// where the rule id belongs.
func TestBehaviorEngine_EveryMatchCarriesItsRule(t *testing.T) {
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

	writeAuditChain(t, audit)
	evs := be.Poll()
	if len(evs) == 0 {
		t.Fatal("expected rule matches from the CARBONATO chain")
	}
	for i, ev := range evs {
		if ev.Rule == nil {
			t.Fatalf("event %d matched but carries no Rule; the log line would have a blank id", i)
		}
		if ev.Rule.ID == "" {
			t.Fatalf("event %d carries a Rule with an empty ID", i)
		}
	}
}

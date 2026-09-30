package telemetry

import (
	"strings"
	"testing"
)

// watchKey is the audit rule key the deploy guide uses for every agentids rule.
const watchKey = "agentids"

// TestParseFileWriteCreate verifies a create-family openat on a watched path
// becomes a file.write naming the created file — not the PARENT directory.
func TestParseFileWriteCreate(t *testing.T) {
	log := strings.Join([]string{
		`type=SYSCALL msg=audit(1790593100.100:900): arch=x86_64 syscall=openat success=yes pid=4242 ppid=1 uid=1000 auid=1000 exe="/usr/bin/python3" key="agentids"`,
		`type=CWD msg=audit(1790593100.100:900): cwd="/home/vivi"`,
		`type=PATH msg=audit(1790593100.100:900): item=0 name="/etc/cron.d" inode=100 nametype=PARENT`,
		`type=PATH msg=audit(1790593100.100:900): item=1 name="/etc/cron.d/payload" inode=101 nametype=CREATE`,
	}, "\n")

	recs, err := ParseAuditStream(strings.NewReader(log))
	if err != nil {
		t.Fatal(err)
	}
	if len(recs) != 1 {
		t.Fatalf("got %d records, want 1", len(recs))
	}
	act, actor, ok := recs[0].AnyAction()
	if !ok {
		t.Fatal("AnyAction returned not-ok for a file-write record")
	}
	if act.Type != FileWrite {
		t.Fatalf("action type = %q, want %q", act.Type, FileWrite)
	}
	if got := act.Detail["path"]; got != "/etc/cron.d/payload" {
		t.Errorf("path = %v, want /etc/cron.d/payload (the target, not the PARENT dir)", got)
	}
	if got := act.Detail["nametype"]; got != "CREATE" {
		t.Errorf("nametype = %v, want CREATE", got)
	}
	if got := act.Detail["watch"]; got != watchKey {
		t.Errorf("watch = %v, want %q", got, watchKey)
	}
	if actor == nil || actor.UID == nil || *actor.UID != 1000 {
		t.Errorf("actor = %+v, want uid 1000", actor)
	}
}

// TestParseFileWriteAppendToWatchedExistingFile covers the case the argv rule
// cannot see: an append to an existing file. auditd tags it NORMAL (no create),
// so the only signal it is a write is that it came from a file watch.
func TestParseFileWriteAppendToWatchedExistingFile(t *testing.T) {
	log := strings.Join([]string{
		`type=SYSCALL msg=audit(1790593100.200:901): arch=x86_64 syscall=openat success=yes pid=4242 ppid=1 uid=1000 auid=1000 exe="/usr/bin/bash" key="agentids"`,
		`type=PATH msg=audit(1790593100.200:901): item=0 name="/home/vivi/.bashrc" inode=200 nametype=NORMAL`,
	}, "\n")

	recs, err := ParseAuditStream(strings.NewReader(log))
	if err != nil {
		t.Fatal(err)
	}
	if len(recs) != 1 {
		t.Fatalf("got %d records, want 1", len(recs))
	}
	act, _, ok := recs[0].AnyAction()
	if !ok || act.Type != FileWrite {
		t.Fatalf("got (%v, ok=%v), want a file.write", act.Type, ok)
	}
	if got := act.Detail["path"]; got != "/home/vivi/.bashrc" {
		t.Errorf("path = %v", got)
	}
	if got := act.Detail["nametype"]; got != "NORMAL" {
		t.Errorf("nametype = %v, want NORMAL", got)
	}
}

// TestParseUnlinkIsFileWrite: the DELETE nametype is a write with no ambiguity,
// so it fires regardless of the syscall (and would fire without a watch key).
func TestParseUnlinkIsFileWrite(t *testing.T) {
	log := strings.Join([]string{
		`type=SYSCALL msg=audit(1790593100.300:902): arch=x86_64 syscall=unlink success=yes pid=4242 ppid=1 uid=1000 auid=1000 exe="/usr/bin/rm"`,
		`type=PATH msg=audit(1790593100.300:902): item=0 name="/home/vivi/.ssh/authorized_keys" inode=300 nametype=DELETE`,
	}, "\n")

	recs, err := ParseAuditStream(strings.NewReader(log))
	if err != nil {
		t.Fatal(err)
	}
	if len(recs) != 1 {
		t.Fatalf("got %d records, want 1", len(recs))
	}
	act, _, ok := recs[0].AnyAction()
	if !ok || act.Type != FileWrite {
		t.Fatalf("got (%v, ok=%v), want a file.write", act.Type, ok)
	}
	if got := act.Detail["path"]; got != "/home/vivi/.ssh/authorized_keys" {
		t.Errorf("path = %v", got)
	}
}

// TestReadOpenIsNotAWrite is the false-positive guard: a plain open with no
// watch key is ambiguous and must be dropped, not reported as a write. This is
// what keeps the adapter from inventing writes on a host that has a broad
// openat audit rule.
func TestReadOpenIsNotAWrite(t *testing.T) {
	log := strings.Join([]string{
		`type=SYSCALL msg=audit(1790593100.400:903): arch=x86_64 syscall=openat success=yes pid=4242 ppid=1 uid=1000 auid=1000 exe="/usr/bin/cat"`,
		`type=PATH msg=audit(1790593100.400:903): item=0 name="/etc/passwd" inode=400 nametype=NORMAL`,
	}, "\n")

	recs, err := ParseAuditStream(strings.NewReader(log))
	if err != nil {
		t.Fatal(err)
	}
	if len(recs) != 0 {
		t.Fatalf("got %d records, want 0 (a bare read-open is not a write)", len(recs))
	}
}

// TestExecveWithNormalPathStaysProcessExec guards the dispatch: an execve
// record carries a PATH with nametype=NORMAL for the binary, and it must stay a
// process.exec — not be mistaken for a file write.
func TestExecveWithNormalPathStaysProcessExec(t *testing.T) {
	log := strings.Join([]string{
		`type=SYSCALL msg=audit(1790593100.500:904): arch=x86_64 syscall=execve success=yes pid=4242 ppid=1 uid=1000 auid=1000 exe="/usr/bin/bash" key="agentids"`,
		`type=EXECVE msg=audit(1790593100.500:904): argc=3 a0="bash" a1="-c" a2="cat ~/.ssh/id_ed25519"`,
		`type=PATH msg=audit(1790593100.500:904): item=0 name="/usr/bin/bash" inode=500 nametype=NORMAL`,
	}, "\n")

	recs, err := ParseAuditStream(strings.NewReader(log))
	if err != nil {
		t.Fatal(err)
	}
	if len(recs) != 1 {
		t.Fatalf("got %d records, want 1", len(recs))
	}
	act, _, ok := recs[0].AnyAction()
	if !ok {
		t.Fatal("AnyAction not ok")
	}
	if act.Type != ProcessExec {
		t.Fatalf("action type = %q, want %q", act.Type, ProcessExec)
	}
}

// TestWriteActionNoTarget: a record with no PATH cannot name a file, so it is
// dropped rather than guessed.
func TestWriteActionNoTarget(t *testing.T) {
	r := &AuditRecord{Syscall: "unlink"}
	if _, _, ok := r.WriteAction(); ok {
		t.Error("WriteAction returned ok for a record with no path")
	}
}

// TestFileWriteDoesNotFireWithoutWatchOnOpen is the pairing of
// TestParseFileWriteAppendToWatchedExistingFile: the identical openat record,
// minus the watch key, is not a write. Same bytes, one token different.
func TestFileWriteDoesNotFireWithoutWatchOnOpen(t *testing.T) {
	log := strings.Join([]string{
		`type=SYSCALL msg=audit(1790593100.600:905): arch=x86_64 syscall=openat success=yes pid=4242 ppid=1 uid=1000 auid=1000 exe="/usr/bin/bash"`,
		`type=PATH msg=audit(1790593100.600:905): item=0 name="/home/vivi/.bashrc" inode=200 nametype=NORMAL`,
	}, "\n")

	recs, err := ParseAuditStream(strings.NewReader(log))
	if err != nil {
		t.Fatal(err)
	}
	if len(recs) != 0 {
		t.Fatalf("got %d records, want 0 without a watch key", len(recs))
	}
}

// TestStrictWriteSyscallWithoutWatchStillFires: creat is unambiguous, so it is a
// write even when no rule key rode along.
func TestStrictWriteSyscallWithoutWatchStillFires(t *testing.T) {
	r := &AuditRecord{
		Syscall:  "creat",
		PathRecs: []PathRec{{Name: "/home/vivi/.ssh/authorized_keys", Nametype: "NORMAL"}},
	}
	act, _, ok := r.WriteAction()
	if !ok || act.Type != FileWrite {
		t.Fatalf("got (%v, ok=%v), want file.write", act.Type, ok)
	}
	if got := act.Detail["path"]; got != "/home/vivi/.ssh/authorized_keys" {
		t.Errorf("path = %v", got)
	}
}

package telemetry

import (
	"strings"
	"testing"
)

// watchKey is the audit rule key the deploy guide uses for every watchman rule.
const watchKey = "watchman"

// TestParseFileWriteCreate verifies a create-family openat on a watched path
// becomes a file.write naming the created file — not the PARENT directory.
func TestParseFileWriteCreate(t *testing.T) {
	log := strings.Join([]string{
		`type=SYSCALL msg=audit(1790593100.100:900): arch=x86_64 syscall=openat success=yes pid=4242 ppid=1 uid=1000 auid=1000 exe="/usr/bin/python3" key="watchman"`,
		`type=CWD msg=audit(1790593100.100:900): cwd="/home/agent"`,
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
		`type=SYSCALL msg=audit(1790593100.200:901): arch=x86_64 syscall=openat success=yes pid=4242 ppid=1 uid=1000 auid=1000 exe="/usr/bin/bash" key="watchman"`,
		`type=PATH msg=audit(1790593100.200:901): item=0 name="/home/agent/.bashrc" inode=200 nametype=NORMAL`,
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
	if got := act.Detail["path"]; got != "/home/agent/.bashrc" {
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
		`type=PATH msg=audit(1790593100.300:902): item=0 name="/home/agent/.ssh/authorized_keys" inode=300 nametype=DELETE`,
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
	if got := act.Detail["path"]; got != "/home/agent/.ssh/authorized_keys" {
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
		`type=SYSCALL msg=audit(1790593100.500:904): arch=x86_64 syscall=execve success=yes pid=4242 ppid=1 uid=1000 auid=1000 exe="/usr/bin/bash" key="watchman"`,
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
		`type=PATH msg=audit(1790593100.600:905): item=0 name="/home/agent/.bashrc" inode=200 nametype=NORMAL`,
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
		PathRecs: []PathRec{{Name: "/home/agent/.ssh/authorized_keys", Nametype: "NORMAL"}},
	}
	act, _, ok := r.WriteAction()
	if !ok || act.Type != FileWrite {
		t.Fatalf("got (%v, ok=%v), want file.write", act.Type, ok)
	}
	if got := act.Detail["path"]; got != "/home/agent/.ssh/authorized_keys" {
		t.Errorf("path = %v", got)
	}
}

// TestDirectoryOpsWithAndWithoutNametype pins the two gaps in FINDING-2026-10-26e (beat 604/605).
//
// rmdir, mkdir, and mkdirat are in NEITHER syscall map. A record for them is kept only if auditd
// tagged the PATH with CREATE or DELETE. Every ordinary case is tagged, which is why this has never
// shown up in practice -- but nothing tested the untagged case, so the drop was unproven and unguarded.
//
// These cases say exactly which rescue each shape gets, so a change to writeTarget's order or to the
// syscall maps fails here rather than silently moving the boundary.
func TestDirectoryOpsWithAndWithoutNametype(t *testing.T) {
	writeOf := func(lines ...string) (string, string, bool) {
		recs, err := ParseAuditStream(strings.NewReader(strings.Join(lines, "\n")))
		if err != nil {
			t.Fatalf("parse: %v", err)
		}
		// The parser drops non-actionable records. For the untagged directory ops that IS the
		// behaviour under test, so zero records is a legitimate answer, not a fixture error.
		if len(recs) == 0 {
			return "", "", false
		}
		if len(recs) > 1 {
			t.Fatalf("got %d records, want at most 1", len(recs))
		}
		act, _, ok := recs[0].AnyAction()
		if !ok {
			return "", "", false
		}
		p, _ := act.Detail["path"].(string)
		nt, _ := act.Detail["nametype"].(string)
		return p, nt, act.Type == FileWrite
	}

	sys := func(sc, key string) string {
		s := `type=SYSCALL msg=audit(1790593200.100:910): arch=x86_64 syscall=` + sc +
			` success=yes pid=4242 ppid=1 uid=0 auid=1000 exe="/usr/bin/rm"`
		if key != "" {
			s += ` key="` + key + `"`
		}
		return s
	}

	// 1. Tagged DELETE -- the ordinary `rm -rf` shape. Caught by the nametype check, which runs first.
	if p, nt, isWrite := writeOf(
		sys("rmdir", ""),
		`type=PATH msg=audit(1790593200.100:910): item=0 name="/etc/" inode=2 nametype=PARENT`,
		`type=PATH msg=audit(1790593200.100:910): item=1 name="/etc/cron.d" inode=999 nametype=DELETE`,
	); !isWrite || p != "/etc/cron.d" || nt != "DELETE" {
		t.Errorf("tagged rmdir: got (%q, %q, write=%v), want (/etc/cron.d, DELETE, true)", p, nt, isWrite)
	}

	// 2. Untagged rmdir WITH an operator watch key. rmdir is in neither syscall map, and the watch-key
	//    rescue at writeTarget only covers openSyscalls -- so this is DROPPED even though an operator
	//    explicitly watched the path. This is the real cost of the missing map entries.
	if _, _, isWrite := writeOf(
		sys("rmdir", "watch_etc"),
		`type=PATH msg=audit(1790593200.100:910): item=0 name="/etc/cron.d" inode=999`,
	); isWrite {
		t.Error("untagged rmdir WITH a watch key was kept -- rmdir was added to a syscall map; " +
			"update FINDING-2026-10-26e and this test together")
	}

	// 3. Untagged mkdir, same shape.
	if _, _, isWrite := writeOf(
		sys("mkdir", ""),
		`type=PATH msg=audit(1790593200.100:910): item=1 name="/etc/cron.d" inode=999`,
	); isWrite {
		t.Error("untagged mkdir was kept -- mkdir was added to a syscall map; update the finding")
	}

	// 4. The contrast: unlinkat IS in strictWriteSyscalls, so an untagged PATH survives with no key at all.
	if p, _, isWrite := writeOf(
		sys("unlinkat", ""),
		`type=PATH msg=audit(1790593200.100:910): item=1 name="/etc/cron.d/x" inode=999`,
	); !isWrite || p != "/etc/cron.d/x" {
		t.Errorf("untagged unlinkat: got (%q, write=%v), want a file.write to /etc/cron.d/x", p, isWrite)
	}
}

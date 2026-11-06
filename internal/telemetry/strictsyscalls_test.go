package telemetry

import "testing"

// Beat 834: filewatch.go declares 27 syscalls as "can only be a write or a metadata change". Each of
// those 27 `"name": true` entries is a mutation candidate, and the first full sweep of this package
// killed none of them -- 15 survivors, all in strictWriteSyscalls. That is not 15 near-misses: it is
// one defect counted 15 times. A dropped entry disarms a security rule. A flipped entry promotes an
// unrelated syscall into the write class. Neither is caught by testing one representative member.
//
// WHY THIS IS A LOOP AND NOT 27 HAND-WRITTEN CASES. A hand-written case per syscall is 27 chances to
// copy a typo -- and the test would then agree with the map, which is the failure the test exists to
// catch. The list below is written out in full, on purpose, and compared as a SET in both
// directions: nothing missing, nothing extra. It is the declaration restated from the syscall(2)
// families the comment names, not derived from the map.
//
// Be honest about the limit: this pins the map to a list I typed, so if the list is wrong the test
// is wrong the same way. What it CANNOT do is let the map change silently -- growing it (which is a
// detection change) or losing a member (which is a detection loss) both go red and need a decision.
var declaredStrictWriteSyscalls = []string{
	// creat(2) -- the syscall the map makes explicit because it is the odd one out of the open family.
	"creat",
	// truncate family.
	"truncate", "ftruncate",
	// rename family.
	"rename", "renameat", "renameat2",
	// unlink family.
	"unlink", "unlinkat",
	// node creation.
	"mknod", "mknodat", "symlink", "symlinkat", "link", "linkat",
	// permission and ownership.
	"chmod", "fchmod", "fchmodat", "chown", "fchown", "lchown", "fchownat",
	// extended attributes.
	"setxattr", "lsetxattr", "fsetxattr", "removexattr", "lremovexattr", "fremovexattr",
}

// TestStrictWriteSyscallsMatchTheirDeclaration makes the mutation sweep's blind spot impossible to
// reproduce silently: flipping ANY one of the 27 entries changes the set, and the set is compared
// exactly. Mutation-proven at beat 834 by deleting `unlinkat`, adding a bogus entry, and flipping
// `chmod` to false -- see WORKLOG.
func TestStrictWriteSyscallsMatchTheirDeclaration(t *testing.T) {
	want := map[string]bool{}
	for _, name := range declaredStrictWriteSyscalls {
		if want[name] {
			t.Fatalf("declaredStrictWriteSyscalls lists %q twice -- fix the list, it is the oracle", name)
		}
		want[name] = true
	}
	if len(want) != len(declaredStrictWriteSyscalls) {
		t.Fatalf("declared list has duplicates: %d entries, %d distinct", len(declaredStrictWriteSyscalls), len(want))
	}

	for name := range want {
		if !strictWriteSyscalls[name] {
			t.Errorf("strictWriteSyscalls lost %q: a %s record with a nametype-tagged PATH no longer reads as a write. "+
				"Unless the pack was changed to match, this is a detection loss.", name, name)
		}
	}
	for name, on := range strictWriteSyscalls {
		if !on {
			t.Errorf("strictWriteSyscalls has %q => false; a false entry is dead weight that reads like a declaration", name)
		}
		if !want[name] {
			t.Errorf("strictWriteSyscalls gained %q, which is not in the declared list: classify any write/attribute "+
				"syscall as a write, but promote it HERE deliberately rather than in the map alone", name)
		}
	}

	// The two families are disjoint and open is NOT strict -- the map's own comment says why, and a
	// syscall appearing in both would silently override that reasoning.
	for name := range openSyscalls {
		if strictWriteSyscalls[name] {
			t.Errorf("%q is in BOTH openSyscalls and strictWriteSyscalls; strict wins in writeTarget, "+
				"so the WatchKey requirement the open family documents would be bypassed", name)
		}
	}
}

// TestStrictWriteSyscallIsAWriteWithoutAWatchKey is the behaviour the map exists for, asserted on a
// member the other tests do not name, and -- critically -- in BOTH directions. A test that only
// asserts "unlinkat fires" passes against an engine where every syscall fires.
func TestStrictWriteSyscallIsAWriteWithoutAWatchKey(t *testing.T) {
	for _, syscall := range []string{"unlinkat", "fsetxattr", "fchownat", "renameat2"} {
		rec := &AuditRecord{Syscall: syscall, PathRecs: []PathRec{{Nametype: "NORMAL", Name: "/tmp/x"}}}
		if _, ok := rec.writeTarget(); !ok {
			t.Errorf("%s with a plain PATH and no WatchKey did not read as a write", syscall)
		}
		// Control in the other direction, same record shape: a read syscall must NOT read as a write.
		read := &AuditRecord{Syscall: "fstat", PathRecs: []PathRec{{Nametype: "NORMAL", Name: "/tmp/x"}}}
		if _, ok := read.writeTarget(); ok {
			t.Error("fstat read as a write -- the write class is not narrow enough to mean anything")
		}
	}

	// The open family keeps its WatchKey requirement: same path, no key, no write.
	open := &AuditRecord{Syscall: "openat", PathRecs: []PathRec{{Nametype: "NORMAL", Name: "/tmp/x"}}}
	if _, ok := open.writeTarget(); ok {
		t.Error("openat with no WatchKey read as a write; the open family requires the operator's watch key")
	}
	open.WatchKey = "w1"
	if _, ok := open.writeTarget(); !ok {
		t.Error("openat WITH a WatchKey did not read as a write; that is the documented -p wa contract")
	}
}

// TestExecActionArgvStopsAtZero is beat 834's second survivor treated honestly. telemetry.go:130 is
// `if len(r.Argv) > 0` and the mutant makes it `>= 0` -- which is true for every record, so detail
// gains `"argv": []any{}` (an EMPTY list) on every exec action that has no argv.
//
// Is that dead? No -- it depends on the consumer, and I only checked one. `detailStringSlice` in
// internal/rules returns an empty slice for a missing key AND for an empty list, so the RULES do not
// distinguish them. But a rule may also be written against the raw detail (`detail_glob_any` on
// "argv", a JSON export, a downstream consumer), and for those "the key is absent" and "the key says
// there were no arguments" are different statements. So the guard is real and the survivor is a gap,
// not an equivalence. Pinned here so it cannot be re-litigated from memory next sweep.
func TestExecActionArgvStopsAtZero(t *testing.T) {
	// No argv at all: the key must be ABSENT, not present-and-empty.
	rec := &AuditRecord{Syscall: "execve", Exe: "/usr/bin/curl"}
	act, _, ok := rec.Action()
	if !ok {
		t.Fatal("expected ok for an exec record with no argv")
	}
	if v, present := act.Detail["argv"]; present {
		t.Errorf("exec action with no argv carries detail[\"argv\"] = %#v; the field should be absent so a "+
			"consumer can tell 'no arguments recorded' from 'recorded, zero arguments'", v)
	}

	// One argv element: the key must be present and hold it. Without this half the test passes
	// against an engine that never sets argv at all.
	rec = &AuditRecord{Syscall: "execve", Exe: "/bin/bash", Argv: []string{"bash"}}
	act, _, ok = rec.Action()
	if !ok {
		t.Fatal("expected ok")
	}
	argv, present := act.Detail["argv"].([]any)
	if !present || len(argv) != 1 || argv[0] != "bash" {
		t.Errorf("detail[\"argv\"] = %#v (present=%v), want [bash]", act.Detail["argv"], present)
	}
}

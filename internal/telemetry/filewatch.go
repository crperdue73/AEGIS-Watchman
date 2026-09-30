package telemetry

import "github.com/CRPerdue/agentids/internal/schema"

// filewatch.go — the auditd file-watch adapter, emitting file.write.
//
// This is the *writable* half of the file telemetry story (coverage gap G3).
// The readable half — a process that opens a secret without naming it on the
// command line — needs eBPF and is still its own phase (docs/R2-EBPF-DESIGN.md).
// This half needs no kernel object at all: an operator's auditd file watch
// (`-w <path> -p wa -k agentids`) already reports every write, and auditd has
// been able to do that since forever.
//
// Why it matters: persistence is a *write to a known location* — a cron drop, an
// rc-file line, an authorized_keys addition. Today we catch that only when the
// command line names the path (`carbonato.persist.cron_or_rc` matches argv), so
// a script or a wrapped interpreter walks past it. The kernel names the file
// regardless, so this adapter closes that evasion for the write half — the same
// shape of win eBPF gives the read half.
//
// What auditd emits for a watch: a SYSCALL line (syscall, exe, pid, uid, and
// `key=` naming the watch) plus one or more PATH lines carrying `name=` and
// `nametype=` (PARENT for the containing directory, then NORMAL / CREATE /
// DELETE for the target). ParseAuditStream folds those together; this file turns
// the folded record into an action.

// strictWriteSyscalls are syscalls that can only be a write or a metadata
// change: whatever nametype auditd tags their PATH with, the record means the
// file changed. The unlink/rename/truncate/chmod/chown/mknod/xattr families.
// (open/openat are deliberately NOT here — see openSyscalls.)
var strictWriteSyscalls = map[string]bool{
	"creat":    true,
	"truncate": true, "ftruncate": true,
	"rename": true, "renameat": true, "renameat2": true,
	"unlink": true, "unlinkat": true,
	"mknod": true, "mknodat": true,
	"symlink": true, "symlinkat": true,
	"link": true, "linkat": true,
	"chmod": true, "fchmod": true, "fchmodat": true,
	"chown": true, "fchown": true, "lchown": true, "fchownat": true,
	"setxattr": true, "lsetxattr": true, "fsetxattr": true,
	"removexattr": true, "lremovexattr": true, "fremovexattr": true,
}

// openSyscalls are the open family. A PATH on one of these is ambiguous on its
// own: the same syscall reads and writes, and auditd does not record the open
// flags. It is treated as a write only when the record carries a WatchKey —
// i.e. it came from an operator file watch, and the documented watch is `-p wa`
// (write/attribute change). The deploy guide says plainly not to add `-p r` to
// the same watch; if an operator does, a read of a watched path will read as a
// write, and the guide's note is the place that is corrected, not this map.
var openSyscalls = map[string]bool{
	"open": true, "openat": true, "openat2": true,
}

// WriteAction converts a file-watch audit record into a file.write action. It
// returns false when the record does not name a changed path — a missing target
// is dropped rather than guessed, the same principle as uid resolution and
// saddr decoding.
func (r *AuditRecord) WriteAction() (schema.Action, *schema.Actor, bool) {
	target, ok := r.writeTarget()
	if !ok {
		return schema.Action{}, nil, false
	}
	detail := map[string]any{
		"path":    target,
		"syscall": r.Syscall,
	}
	if r.WatchKey != "" {
		detail["watch"] = r.WatchKey
	}
	if nt := r.nametypeFor(target); nt != "" {
		detail["nametype"] = nt
	}
	if r.Exe != "" {
		detail["exe"] = r.Exe
	}
	if r.PID != 0 {
		detail["pid"] = r.PID
	}
	return schema.Action{Type: FileWrite, Detail: detail}, actorOf(r), true
}

// writeTarget returns the path this record says was changed, and whether the
// record is a write at all. It is the single place the read/write decision is
// made, so AnyAction and isActionable cannot disagree about it — a record the
// parser keeps is a record the dispatcher can convert, and vice versa.
func (r *AuditRecord) writeTarget() (string, bool) {
	// An explicit create or delete is a write whatever the syscall.
	for _, p := range r.PathRecs {
		if (p.Nametype == "CREATE" || p.Nametype == "DELETE") && p.Name != "" {
			return p.Name, true
		}
	}
	// Otherwise the syscall itself has to rule a read out.
	target := r.firstTargetPath()
	if target == "" {
		return "", false
	}
	if strictWriteSyscalls[r.Syscall] {
		return target, true
	}
	if r.WatchKey != "" && openSyscalls[r.Syscall] {
		return target, true
	}
	return "", false
}

// firstTargetPath returns the first non-PARENT path on the record — the file the
// syscall acted on. auditd emits a PATH for the containing directory
// (nametype=PARENT) before the target on create-family syscalls, so PARENT
// entries are skipped.
func (r *AuditRecord) firstTargetPath() string {
	for _, p := range r.PathRecs {
		if p.Nametype == "PARENT" {
			continue
		}
		if p.Name != "" {
			return p.Name
		}
	}
	return ""
}

// nametypeFor returns the nametype auditd tagged a given path with, for the
// record detail. Best-effort: an untagged path yields "".
func (r *AuditRecord) nametypeFor(name string) string {
	for _, p := range r.PathRecs {
		if p.Name == name {
			return p.Nametype
		}
	}
	return ""
}

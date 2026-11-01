#!/usr/bin/env python3
"""auditsplit — an auditd record split across a poll boundary is LOST, not duplicated.

WHAT I WAS LOOKING FOR AND WHAT I FOUND
---------------------------------------
Beat 668 set out to find the false-positive side: the collector's noise. `Engine.Match`
(rules/engine.go:56-79) has no dedupe and no memory -- it emits one event per matching
rule per action, every time that action appears. So noise is entirely a question of
whether the SOURCE re-emits an action. I expected duplication. I measured the opposite.

THE MECHANISM, MEASURED
-----------------------
auditd writes one logical event as SEVERAL lines sharing a `msg=audit(TS:SERIAL)` trailer
-- a `SYSCALL` line and a `SOCKADDR`/`PATH` line. `ParseAuditStream` groups by serial
*within a single read* (telemetry.go:215-228). That is correct -- for one read.

`FileSource.Actions` (engine.go:156-214) reads only `offset .. last complete newline` and
then advances `f.offset` past what it consumed. Its state is exactly three fields
(engine.go:103-107: path, offset, cursor). **There is no buffer for a partial record.**

So when a poll boundary falls between the SYSCALL line and its SOCKADDR line:

  read 1: SYSCALL only -> AuditRecord{Syscall:"bind", BindAddr:""}
          isActionable(): `case ListenBind, ListenListen: return r.BindAddr != ""` -> FALSE
          => the record is DROPPED and the bytes are already past the offset.

  read 2: SOCKADDR only -> a NEW AuditRecord{Serial:42}, Syscall "" (it was never seen).
          applyConnectLine() routes on `rec.Syscall == ListenBind`, which is false, so the
          address is filed in **SockAddr**, not BindAddr (egress.go:110-118).
          isActionable(): Syscall "" falls through to the execve case; Exe and Argv are
          both empty -> FALSE. => dropped again.

The bind exists in the log in full. It is matched by `carbonato.net.listener_bound`
(`detail_non_empty: ["listen"]`) when the two lines share a read. Split them and the rule
never fires -- and nothing anywhere says a line was skipped.

WHY THIS IS THE COLD CASE, NOT AN EXOTIC RACE
----------------------------------------------
`watch.poll_interval` defaults to 60s. A SYSCALL line and its SOCKADDR line are written by
auditd back to back, but the read window ends at the last newline present at that instant.
Any live auditd stream crosses a poll boundary between two lines of one event routinely --
in fact, since the boundary is defined by "wherever it happened to be," roughly one event
per poll is at risk on a busy host. This is not a tail-eating race; it is the ordinary
operation of a line-oriented reader over a record-oriented source.

CLAIM / NOT CLAIMED
-------------------
CLAIMED: `FileSource` carries no partial-record state; `isActionable` rejects a
    SYSCALL-only bind record; the orphaned SOCKADDR is misfiled into SockAddr because
    `rec.Syscall` is empty on the second read; both halves are dropped, silently, with the
    bytes already consumed. Read from source + the isActionable/applyConnectLine routing.
NOT CLAIMED: that I observed this on a live host (no auditd here, no Go toolchain -- this
    is static analysis, and the routing claim is a reading of two functions that I have
    transcribed rather than executed). NOT CLAIMED: that execve events suffer the same way
    -- execve is actionable on the SYSCALL line alone (Exe != ""), so it survives a split
    with the argv/other-line detail possibly missing. **The loss is specific to the action
    types whose actionability depends on the SECOND line** -- bind/listen, connect, and
    file writes -- which is a real limitation of this instrument's blast-radius claim and
    is why I say "listener and egress rules" rather than "all rules".
"""

import sys
import pathlib
import re


def main():
    root = pathlib.Path(__file__).resolve().parents[1]
    fails = 0

    def check(name, ok):
        nonlocal fails
        print(("ok   " if ok else "FAIL ") + name)
        if not ok:
            fails += 1

    try:
        eng = (root / "internal/telemetry/engine.go").read_text()
        tel = (root / "internal/telemetry/telemetry.go").read_text()
        egr = (root / "internal/telemetry/egress.go").read_text()
        rules_eng = (root / "internal/rules/engine.go").read_text()
        pack = (root / "rules/carbonato.yaml").read_text()
    except Exception as e:  # noqa: BLE001
        print(f"FAIL could not read input ({e}); a missing input is not a pass")
        return 1

    print("-- the source keeps only an offset, no partial-record state --")
    body = eng.split("type FileSource struct {")[1].split("\n}")[0]
    fields = re.findall(r"^\t([a-z]\w*)\s+\S", body, re.M)
    print(f"     FileSource fields: {fields}")
    check("FileSource has exactly offset/path/cursor and no buffer/pending field",
          set(fields) == {"path", "offset", "cursor"})
    check("no pending/partial/buffer concept anywhere in FileSource",
          not re.search(r"\b(pending|partial|leftover|carry|remainder)", body))
    check("Actions advances offset past the bytes it parsed (bytes are not re-read)",
          "f.offset += int64(len(complete))" in eng)

    print("-- the SYSCALL-only half is not actionable --")
    act = tel.split("func (r *AuditRecord) isActionable() bool {")[1].split("\n}")[0]
    check("bind/listen actionable ONLY with a BindAddr (so SYSCALL-only = dropped)",
          'case ListenBind, ListenListen:' in act and 'return r.BindAddr != ""' in act)
    check("connect has the same second-line dependency",
          'case EgressConnect:' in act and 'return r.SockAddr != ""' in act)

    print("-- the orphaned SOCKADDR half is misfiled, then dropped --")
    check("applyConnectLine routes on rec.Syscall, which is empty on a fresh read",
          "if rec.Syscall == ListenBind || rec.Syscall == ListenListen {" in egr)
    check("...and the else branch files the address under SockAddr (a connect field)",
          re.search(r"rec\.BindAddr = v\s*\} else \{\s*rec\.SockAddr = v", egr) is not None)
    # property: isActionable for Syscall=="" falls through to the execve case
    check("Syscall \"\" is treated as an exec record (Exe/Argv), so a lone"
          " SOCKADDR with neither is not actionable",
          'case "", "execve", "execveat":' in act and "return r.Exe != \"\" || len(r.Argv) > 0" in act)

    print("-- the rule that goes quiet --")
    check("pack ships a bind-based listener rule keyed on the 'listen' detail",
          "carbonato.net.listener_bound" in pack and 'detail_non_empty:' in pack)

    print("-- the engine has no memory to paper over it --")
    ms = rules_eng.split("func (e *Engine) Match(")[1].split("\n}\n")[0]
    check("Engine.Match holds no seen-set / dedupe / per-rule state",
          not re.search(r"\b(seen|dedupe|lastFired|once)\b", ms))

    print("-- exec is the exception, and this is the limit of my claim --")
    check("execve is actionable on the SYSCALL line alone (Exe != \"\"), so it survives a split",
          'case "", "execve", "execveat":' in act)

    if fails:
        print(f"\nAUDIT SPLIT RED: {fails}")
        return 1

    print("\n=> An auditd event whose actionability depends on its SECOND line (bind/listen,")
    print("   connect, file write) is silently lost when a poll boundary falls between the")
    print("   lines -- the first half is dropped for lacking the address, the second is filed")
    print("   as the wrong shape and dropped for lacking a syscall. Bytes are already consumed,")
    print("   so it can never be recovered, and nothing logs that a line was skipped.")
    print("   Fix, Dad's call: carry the unterminated tail (and any record whose serial is")
    print("   still open) across polls -- a small `pending []byte` on FileSource, prepended to")
    print("   the next read, so one logical event is always parsed in one pass. The alternative")
    print("   (re-read from the last serial) is more state and more failure modes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

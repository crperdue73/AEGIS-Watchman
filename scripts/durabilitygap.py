#!/usr/bin/env python3
"""durabilitygap — "at-least-once" holds only if the disk write succeeds, and a failed
write is logged and then thrown away.

WHY LOOK HERE
-------------
Beats 668 and 669 established that an event can be lost before the spool and that the wire
can never report it. So I went looking for a *guarantee in the docs* to test rather than
another hole to find. `docs/R2-EBPF-DESIGN.md:55` says, in D1's rationale:

    "matches are idempotent by rule id + action, and **the spool is at-least-once by design**."

That is a precise claim, and it is mostly earned. `Spool.Append` (spool.go:60-85) marshals,
writes, and **checks `f.Sync()`** -- a real `fsync`, not a buffered write. `Drain`
(spool.go:100+) stops at the first submit failure so a backlog is not consumed by a central
that is down. Beat 659 already measured that a *stopped central* is bounded, self-healing
loss. That part is sound and this instrument does not dispute it.

THE HOLE IS THE BRANCH THAT HANDLES FAILURE
-------------------------------------------
`BehaviorEngine.Poll` (behavior.go:406-436):

    if b.spool.Enabled() {
        if err := b.spool.Append(b.host, b.agent, events); err != nil {
            // A spool failure is loud: it means the durability guarantee is broken.
            log.Printf("collector: spool append (%d events): %v", len(events), err)
        }
    } else if b.submitter != nil {
        ... best-effort submit, error logged ...
    }
    b.drain()

Read the failure branch literally. `Append` failed => the events are **not on disk**. The
only action taken is `log.Printf`. `Poll` does not retain `events`, does not requeue them,
does not retry, and `drain()` only reads **the spool** -- which does not contain them. The
comment names the consequence exactly right ("the durability guarantee is broken") and then
continues as though it were not.

Verified there is no second chance: `BehaviorEngine` has **no** pending/buffer/retry/backlog
field (grep over the struct body). So the events are garbage after Printf.

WHEN THE BRANCH ACTUALLY RUNS -- this is not theoretical
----------------------------------------------------------
`Append` returns an error for mkdir, open, marshal, write, **and sync**. `Sync` is the one
that fails in production: ENOSPC, EIO, EDQUOT, or a full/read-only disk. Those are exactly
the conditions under which a compromised or thrashing agent host is interesting -- and they
are the conditions under which the detector silently stops reporting. A full disk is not an
exotic case; it is the ordinary end-state of a chatty host.

Note the asymmetry that makes this a *design* defect rather than a bug: the `else` branch has
a doc comment explaining that shipping without durability is "best-effort"; the spool branch,
which is supposed to be the durable one, degrades to *silence* with equal ease.

CLAIM / NOT CLAIMED
-------------------
CLAIMED: `Append` checks Sync (durability is attempted, not assumed); the spool-branch error
    path consists solely of a log line; `Poll` retains no copy of `events`; `drain` reads only
    the spool; `BehaviorEngine` has no retry buffer; therefore a failed Sync loses the batch
    permanently.
NOT CLAIMED: that Append fails often, or that I observed a failure (no Go toolchain, and I
    ran no collector). NOT CLAIMED: that beat 659's spool analysis was wrong -- the spool IS
    at-least-once *for events that reached it*. The claim here is narrower and stacks on top:
    **events that fail to reach the spool are at-most-once, and nobody counts them.** That
    distinction is the whole finding, so the doc must not overstate it as "the spool is not
    durable" -- the spool is fine; the caller's failure path is not.
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
        beh = (root / "internal/collector/behavior.go").read_text()
        spool = (root / "internal/collector/spool.go").read_text()
        design = (root / "docs/R2-EBPF-DESIGN.md").read_text()
    except Exception as e:  # noqa: BLE001
        print(f"FAIL could not read input ({e}); a missing input is not a pass")
        return 1

    print("-- credit: the durability that IS there --")
    ap = spool.split("func (s *Spool) Append(")[1].split("\n}\n")[0]
    check("Append checks f.Sync() (a real fsync, and the error is returned)",
          "f.Sync()" in ap and re.search(r'if err := f\.Sync\(\); err != nil', ap) is not None)
    check("Append returns the write error rather than swallowing it",
          re.search(r'if _, err := f\.Write\(.*?\); err != nil', ap, re.S) is not None)
    check("Drain stops at the first submit failure (a backlog is not consumed)",
          "return err" in spool and "Drain" in spool)

    print("-- the claim in the design doc --")
    check("R2-EBPF-DESIGN claims the spool is at-least-once by design",
          "at-least-once by design" in design)

    print("-- the failure branch, read literally --")
    poll = beh.split("func (b *BehaviorEngine) Poll(")[1].split("\n}\n")[0]
    seg = poll.split("b.spool.Enabled()")[1].split("} else if")[0]
    print("     spool branch body:")
    for ln in seg.strip().splitlines():
        print("       " + ln.strip()[:100])
    check("the spool-error branch logs and does nothing else",
          "log.Printf" in seg
          and "return events" not in seg
          and "Append" not in seg.replace("b.spool.Append(", "", 1))
    # property: after the Append error, the only statements are a log call
    stmts = [s.strip() for s in re.split(r"[;\n]", seg) if s.strip()]
    nontrivial = [s for s in stmts
                  if not s.startswith(("if err := b.spool.Append", "log.Printf", "}", "//"))
                  and s not in ("{", "")]
    print(f"     non-log statements in the error branch: {nontrivial}")
    check("no requeue / retry / retain statement in the Append error branch",
          not any(re.search(r"(requeue|retry|pending|append\(events|buffer|backlog)", s)
                  for s in nontrivial))

    print("-- and there is no second chance later --")
    struct = beh.split("type BehaviorEngine struct {")[1].split("\n}")[0]
    fields = re.findall(r"^\t([a-z]\w*)\s", struct, re.M)
    print(f"     BehaviorEngine fields: {fields}")
    check("BehaviorEngine has no pending/retry/backlog/buffer field",
          not re.search(r"(pending|retry|backlog|buffer|unsent)", struct, re.I))
    dr = beh.split("func (b *BehaviorEngine) drain()")[1].split("\n}\n")[0]
    check("drain() only reads the spool — it never sees Poll's events",
          "b.spool.Drain(" in dr and "events" not in dr.split("b.spool.Drain(")[0])
    check("Poll does not return the events anywhere durable (they are the return value only)",
          poll.rstrip().endswith("return events"))

    print("-- the failure mode is a full or failing disk, not an exotic race --")
    check("Append fails on write OR sync, i.e. ENOSPC/EIO/EDQUOT reach this branch",
          "fmt.Errorf(\"spool: write: %w\", err)" in ap
          and "fmt.Errorf(\"spool: sync: %w\", err)" in ap)

    if fails:
        print(f"\nDURABILITY GAP RED: {fails}")
        return 1

    print("\n=> 'At-least-once by design' is true for events that REACH the spool. An event")
    print("   whose Append fails (a full or read-only disk, an EIO) is logged once and gone:")
    print("   Poll keeps no copy, the spool never got it, and drain only reads the spool.")
    print("   So the one condition that makes a host interesting -- a disk that is failing --")
    print("   is also the condition that silently stops the durable path, and per beat 669")
    print("   the wire has no field in which to report the loss.")
    print("   Fix, Dad's call: on Append error, keep the batch in an in-memory pending buffer")
    print("   on BehaviorEngine and retry it on the next Poll ahead of new events (bounded --")
    print("   drop oldest past a cap and count the drops, so the cap is a number an operator")
    print("   can read, not a silence). Ship the pending buffer best-effort when the spool is")
    print("   unwritable, because a degraded copy beats a discarded one.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""gapsilence — nothing in the system can tell "nothing happened" from "we lost it".

THE QUESTION
------------
Beat 668 measured that FileSource drops an auditd record split across a poll boundary,
silently. The natural next question is not "fix that" -- it is **would anyone ever find out?**
That is a property of the WIRE, not of the reader, so it outlives any particular reader bug.

MEASURED: THE WIRE HAS NO WAY TO SAY "I MISSED SOMETHING"
----------------------------------------------------------
schema.Event (schema.go:29-42) is: Schema, Event, TS, Host, Agent, File, Action, Rule, Actor,
Severity. The collector wire message (collector/client.go:98-102 and its central twin,
central/http.go:32-36) is: Host, Agent, Events.

  - no sequence number            (can't say "I skipped #412")
  - no batch id                   (can't say "batch 9 never arrived")
  - no per-collector counter      (can't detect a gap at all)
  - no dropped/skipped count      (beat 668's exact loss has no field to travel in)
  - no heartbeat / keepalive message

A batch of zero events and a batch that was lost in transit are **the same absence** on this
wire. Central has no arithmetic with which to notice a hole, because it is never told what
number to expect.

MEASURED: THE ONE FIELD THAT LOOKS LIKE A WATCHDOG ISN'T ONE
-------------------------------------------------------------
`fleetHost.LastSeen` (fleet.go:17) exists, and it is tempting to read it as liveness. Three
reasons it is not:

  1. It is derived from `rec.When` -- and beat 666 measured that `When` is the **ingest**
     stamp, not the event time (service.go:86, `s.nowTime()`), documented as "server time at
     ingest".
  2. It only advances when a **record** exists. Records are created by rule MATCHES. An agent
     that is polled fine, ships fine, and simply matches no rule produces **no records** --
     so it has no LastSeen at all, and is indistinguishable from an agent that is dead.
  3. The fleet view is assembled by iterating records (`byHost[id]`, fleet.go:96-136). **A
     host that produced no records does not appear in the fleet view at all.** Absence is
     rendered as absence -- not as "unknown", not as "silent", not as a row saying "0 events,
     last heard 40 minutes ago".

So the console shows the hosts that are TALKING. It cannot show the ones that have stopped,
because stopping is what removes you from the list. That is the same shape as beats 663, 664,
666 and 667 -- failure and calm rendering identically -- now at the fleet level.

CLAIM / NOT CLAIMED
-------------------
CLAIMED: the event envelope and both ActionsRequest definitions carry no sequence, batch id,
    gap count or heartbeat (fields enumerated from the struct bodies); no gap/sequence/watchdog
    concept exists anywhere in internal/ (grep, listed); LastSeen is an ingest stamp derived
    from matched-rule records only; a zero-record host is absent from the fleet view.
NOT CLAIMED: that central *should* have sequence numbers for v1 -- additive fields are
    explicitly allowed by the schema policy (schema.go:5), so this is a cheap fix, but the
    wire contract is a Dad decision, not mine. NOT CLAIMED: that a lost batch is currently
    likely (beat 659 showed the spool makes loss a bounded, self-healing event for a stopped
    central -- this is about SILENT loss inside the collector, e.g. beat 668's split records,
    which the spool never sees because they never became events).
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
        sch = (root / "internal/schema/schema.go").read_text()
        cli = (root / "internal/collector/client.go").read_text()
        chttp = (root / "internal/central/http.go").read_text()
        fleet = (root / "internal/central/fleet.go").read_text()
        svc = (root / "internal/central/service.go").read_text()
        allgo = "\n".join(p.read_text() for p in (root / "internal").rglob("*.go")
                          if not p.name.endswith("_test.go"))
    except Exception as e:  # noqa: BLE001
        print(f"FAIL could not read input ({e}); a missing input is not a pass")
        return 1

    print("-- the event envelope has no sequence, batch id, or gap count --")
    ev = sch.split("type Event struct {")[1].split("\n}")[0]
    evfields = re.findall(r"^\t([A-Z]\w*)\s", ev, re.M)
    print(f"     Event fields: {evfields}")
    check("Event carries no sequence/seq/batch/gap/heartbeat field",
          not re.search(r"(Seq|Sequence|Batch|Gap|Dropped|Skipped|Missed|Heartbeat)", ev))

    print("-- nor does the collector->central action message --")
    for label, src in (("collector", cli), ("central", chttp)):
        body = src.split("type ActionsRequest struct {")[1].split("\n}")[0]
        f = re.findall(r"^\t([A-Z]\w*)\s", body, re.M)
        print(f"     {label}.ActionsRequest fields: {f}")
        check(f"{label} ActionsRequest is Host/Agent/Events only — no seq/batch/gap",
              set(f) == {"Host", "Agent", "Events"})

    print("-- no gap/watchdog machinery exists anywhere --")
    # Exclude CSS (`gap:`), comments about coverage gaps, and hex-escape prose.
    # Strip comments FIRST. Grepping prose is the trap that has bitten this project
    # seven times (652/655/658/661/662/664 and again here on the first run): the words
    # "missed" and "gap" appear in English sentences about unrelated things. A mechanism
    # is an IDENTIFIER, so look only at comment-free code.
    def strip_comments(src):
        out = []
        for ln in src.splitlines():
            st = ln.strip()
            if st.startswith("//"):
                continue
            if "//" in ln:
                ln = ln.split("//", 1)[0]
            out.append(ln)
        return "\n".join(out)

    code = strip_comments(allgo)
    hits = []
    for m in re.finditer(r"(?i)\b(sequence|seqno|batch_?id|missed|watchdog|heartbeat"
                         r"|gapseq|next_?seq|expected_?seq)\b", code):
        line = code[:m.start()].count("\n")
        ctx = code.splitlines()[line]
        if re.search(r"flex|gap:|\\xHH|decodeHex", ctx):
            continue
        hits.append(ctx.strip()[:80])
    print(f"     non-trivial hits: {hits if hits else 'none'}")
    check("no sequence/batch-id/watchdog/heartbeat mechanism in internal/",
          not hits)

    print("-- LastSeen is not a liveness signal --")
    check("LastSeen advances only from a retained RECORD's When",
          "h.LastSeen = rec.When" in fleet)
    # Property: `when` is computed ONCE, outside the per-event loop, from s.nowTime().
    rt = svc.split("func (s *Service) retain(")[1].split("\n}\n")[0]
    check("retain stamps `when` from s.nowTime() (ingest clock)",
          "s.nowTime()" in rt)
    check("...and stamps it ONCE before the loop (so it cannot be the event clock)",
          rt.index("nowTime()") < rt.index("for _, ev := range"))
    fb = fleet.split("type fleetHost struct {")[1].split("\n}")[0]
    check("fleetHost has no 'expected'/'last_poll'/'silent' field to compare against",
          not re.search(r"(Expected|LastPoll|Silent|Missing)", fb))

    print("-- a silent host is absent from the fleet view, not flagged --")
    check("fleet view is built by iterating records and keying by host",
          "byHost[rec.HostID]" in fleet or re.search(r"byHost\[\w+\]\s*=\s*&fleetHost", fleet) is not None)
    check("hosts are one per RECORD-bearing host (order tracks records, not a roster)",
          re.search(r"for\s+_,\s+id\s*:=\s*range\s+order", fleet) is not None)

    if fails:
        print(f"\nGAP SILENCE RED: {fails}")
        return 1

    print("\n=> Central cannot distinguish a quiet fleet from a blind one, by construction: the")
    print("   wire never says how many events to expect, and the fleet view lists hosts that")
    print("   produced RECORDS, so the hosts that went silent are exactly the ones it omits.")
    print("   Fix, Dad's call: schema is explicitly additive-friendly (schema.go:5) --")
    print("   1. a per-collector `seq` (monotonic) on ActionsRequest + `dropped` count, so")
    print("      central can see a hole and see the count it was told to skip;")
    print("   2. a periodic empty 'poll heartbeat' so a healthy-but-quiet agent still has a")
    print("      liveness timestamp that is NOT a rule match;")
    print("   3. a fleet row for 'known host, no recent contact', rendering silence as")
    print("      SILENT rather than as an absent row.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

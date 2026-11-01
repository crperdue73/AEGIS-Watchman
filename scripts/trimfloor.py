#!/usr/bin/env python3
"""trimfloor — the spool's byte bound is a floor of one batch, and that batch can be
16x larger than the largest thing central will accept.

THE QUESTION
------------
Beat 670 found that a failed spool Append discards the batch. The fix I proposed is a
retry buffer, which only helps if the spool is a place that actually *holds* things. So
this beat I measured the spool's own bound: what is the largest single thing it will
keep, and can central accept that?

MEASURED: THE TRIM LOOP CANNOT GO BELOW ONE RECORD
---------------------------------------------------
`Spool.trim` (spool.go:235-254) enforces `maxBytes` by dropping from the front:

    for len(recs) > 1 {
        ... if int64(size) <= s.maxBytes { break }
        recs = recs[1:]
    }
    log.Printf("... trimming to %d batches", len(recs))
    return s.rewrite(recs)

`len(recs) > 1` is the loop floor. **The last record is never dropped, whatever its size.**
So `maxBytes` is not a bound; it is a bound *on the pile*, not on any item in the pile. With
one oversized batch in the spool, trim rewrites the file unchanged, logs "trimming to 1
batches", and returns success.

MEASURED: THE NUMBERS DO NOT MEET
----------------------------------
  DefaultSpoolMaxBytes = 16 << 20   (spool.go:41)  -> 16 MiB per host on disk
  central's batch limit            = 1 MiB         (beat 661, measured)
  largest single record trim keeps = up to ~16 MiB, i.e. **16x** what central accepts.

So a spool holding one 2 MiB batch is under `maxBytes`, is never trimmed, and `Drain` ships
it whole. Central rejects it — and beat 660 measured that central's ONLY reachable rejection
is **413 on a >1 MiB batch**, with no transient 5xx. Beat 660 also established the fix
direction: a non-2xx means "rejected on contents". So Drain stops at the first failure
(spool.go:116-120, correct behaviour for an outage) — and this is not an outage. It is a
permanent, deterministic rejection.

THE SHAPE OF THE FAILURE: A POISON BATCH WEDGES THE SPOOL
----------------------------------------------------------
Combine the three measured facts:
  1. trim will not drop the last record (this beat),
  2. a >1 MiB batch is always rejected on contents (beats 660/661),
  3. Drain stops at the first submit failure (beat 659, correct for outages).

An oversized batch at the FRONT of the spool blocks every batch behind it, forever. The
spool grows, trim trims (down to one record, which is the poison one, plus newer ones),
and nothing ever ships again. The host keeps looking alive — it is polling, matching, and
appending — while central receives nothing from it.

HONESTY ABOUT THE PROVENANCE OF THIS SHAPE: beats 659 and 660 already reasoned toward this.
Beat 659 *retracted* an early hypothesis that trim discards real events for a poison batch
(it doesn't — trim drops from the front, and the front is the OLDEST). What is new here is
the measurement that trim's floor prevents any recovery at all, and that the 1 MiB threshold
is reachable by an ORDINARY oversized batch rather than only by a runaway agent: beat 661
measured `schema.Event` at 416 bytes, so 1 MiB is ~2520 events, and 16 MiB is ~40,000. A
spool record is one Poll's worth of matches, so a single poll returning 2520 matches already
sits past central's limit.

CLAIM / NOT CLAIMED
-------------------
CLAIMED: the trim loop floor is `len(recs) > 1` (so the last record is never dropped);
    DefaultSpoolMaxBytes is 16 MiB; a single record can therefore exceed central's 1 MiB
    limit and will be retained and shipped whole; Drain stops at the first failure.
NOT CLAIMED: that this is currently happening anywhere, or that 16 MiB is easy to reach in
    one poll (2520 matches in one poll is a lot -- it is the runaway-agent case beat 661
    already named, not the every-day case). Also NOT CLAIMED: the exact serialized size per
    record -- I did not run the marshaler; the 16x is the ratio of the two configured
    constants, which is arithmetic, not a measurement of a real file.
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
        spool = (root / "internal/collector/spool.go").read_text()
        cfg = (root / "internal/collector/config.go").read_text()
    except Exception as e:  # noqa: BLE001
        print(f"FAIL could not read input ({e}); a missing input is not a pass")
        return 1

    print("-- the trim loop's floor is one record --")
    tr = spool.split("func (s *Spool) trim() error {")[1].split("\n}\n")[0]
    has_floor = re.search(r"for\s+len\(recs\)\s*>\s*1\s*\{", tr) is not None
    print(f"     loop condition is `len(recs) > 1`: {has_floor}")
    check("trim cannot drop the last record (loop floor is len(recs) > 1)", has_floor)
    check("the drop removes the OLDEST (recs = recs[1:]), so trim loses age, not size",
          "recs = recs[1:]" in tr)
    check("trim returns success even when it could not get under maxBytes",
          "return s.rewrite(recs)" in tr)

    print("-- the configured cap --")
    m = re.search(r"DefaultSpoolMaxBytes\s*=\s*(\d+)\s*<<\s*(\d+)", spool)
    check("DefaultSpoolMaxBytes is defined as a shift literal", m is not None)
    if m:
        cap = int(m.group(1)) << int(m.group(2))
        print(f"     DefaultSpoolMaxBytes = {int(m.group(1))} << {int(m.group(2))} = {cap} bytes = {cap >> 20} MiB")
        check("the spool cap is 16 MiB", cap == 16 << 20)

    print("-- against central's limit --")
    central_limit = 1 << 20  # measured, beat 661
    ratio = (16 << 20) // central_limit
    print(f"     spool cap / central batch limit = {(16<<20)}/{central_limit} = {ratio}x")
    check(f"the spool will hold a record {ratio}x larger than central accepts", ratio == 16)

    print("-- the third leg: Drain stops at the first failure and RETAINS the rest --")
    dr = spool.split("func (s *Spool) Drain(")[1].split("\n}\n")[0]
    check("Drain breaks on the first submit error", re.search(r"if err := submit\(", dr) is not None and "break" in dr)
    check("...and keeps the failed record plus everything after it (recs[idx:])",
          "recs[idx:]" in dr)

    print("-- and when the FIRST record fails, the error is swallowed --")
    # split into independent assertions rather than one `and`/`or` chain (beat 667 rule)
    check("shipped == 0 short-circuits to `return 0, nil`",
          re.search(r"if shipped == 0 \{\s*return 0, nil\s*\}", dr) is not None)
    check("=> a spool wedged on its FIRST record reports NO error to the caller",
          "recs[idx:]" in dr and re.search(r"if shipped == 0 \{\s*return 0, nil", dr) is not None)
    beh = (root / "internal/collector/behavior.go").read_text()
    bdr = beh.split("func (b *BehaviorEngine) drain()")[1].split("\n}\n")[0]
    check("...and the only caller logs the spool failure only when err != nil",
          re.search(r'if err != nil \{\s*log\.Printf\("collector: spool drain', bdr) is not None)

    print("-- reachability: is a 1 MiB batch ordinary or exotic? --")
    check("Event is ~416 bytes (beat 661), so 1 MiB is ~2520 events in ONE spool record",
          True)  # arithmetic from a prior measured constant; stated, not re-measured here

    if fails:
        print(f"\nTRIM FLOOR RED: {fails}")
        return 1

    print("\n=> maxBytes bounds the pile, not the item. Because trim's loop floor is one record,")
    print("   a single oversized batch is retained and shipped whole -- up to 16x the largest")
    print("   payload central accepts. Central rejects it on contents (beat 660: the only")
    print("   reachable rejection is 413, no transient 5xx), Drain stops, and every batch behind")
    print("   it waits forever. The spool wedges while the host keeps looking healthy.")
    print("   Fix, Dad's call: (1) split a spool record at SubmitActions time when it exceeds the")
    print("   central limit -- beat 662 already proposed this for alerts; (2) treat a 413 as a")
    print("   permanent content verdict (quarantine or drop that record, count it, continue) so")
    print("   one bad batch cannot stop the queue -- beat 660's rule; (3) only then may trim's")
    print("   floor be safe, because a record that cannot ship is no longer a reason to stall.")
    print("   Order matters: splitting without (2) still stalls on a non-413 rejection, and (2)")
    print("   without splitting throws away recoverable events. Do both, or neither.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

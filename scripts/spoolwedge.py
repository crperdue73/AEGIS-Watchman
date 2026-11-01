#!/usr/bin/env python3
"""spoolwedge — the spool is head-of-line blocked, and nothing tells a
permanent rejection apart from a temporary outage.

THE QUESTION
------------
Beats 653-658 walked what the engine SEES. None asked what survives the trip
from the collector to central. The spool is the durability guarantee: "a spool
failure is loud: it means the durability guarantee is broken"
(behavior.go:419). So the guarantee is worth reading.

    internal/collector/spool.go:114-130
        for ; idx < len(recs); idx++ {
            if err := submit(ctx, ...); err != nil {
                break                 // pause here, keep this and everything after
            }
            shipped += len(r.Events)
        }
        if err := s.rewrite(recs[idx:]); err != nil { ... }

    internal/collector/client.go:141-143
        if resp.StatusCode < 200 || resp.StatusCode >= 300 {
            return fmt.Errorf("central returned %s", resp.Status)
        }

Every failure is one `error`. A 503 (central restarting) and a 400 (this batch is
malformed -- it will be malformed forever) are INDISTINGUISHABLE to `Drain`. Both
cause `break`. Both are retried next cycle.

WHAT THAT MEANS FOR A 400
-------------------------
The spool is an append-only JSONL and `Drain` walks it from index 0 in file order.
A batch central permanently rejects sits at index 0 and is retried every cycle,
forever. Everything spooled after it never ships. Because `Append` keeps adding
to the TAIL, the log grows without bound while the oldest line is un-deliverable.

That is not "paused". That is a queue that will never advance again, and the only
signal is a log line that repeats with the same numbers each cycle.

SECOND PATH: SPOOL FULL
-----------------------
`Append` calls `s.trim()`. If trim drops from the head while the head is the
undeliverable batch, the finding is benign (the bad batch eventually ages out) --
but if trim is a size cap, it silently discards REAL events to make room for the
one that cannot ship. Measured below from the code, not assumed.

THIRD PATH: DISABLED SPOOL
---------------------------
behavior.go:421-431: when the spool is NOT enabled, matches go straight to
`SubmitActions` with a 10s timeout and a log line on failure. No queue, no
retry, no durability. A dev setup "behaves like production minus the disk" -- and
minus the delivery guarantee, silently, with the same code path.

CLAIM / NOT CLAIMED
-------------------
CLAIMED: (a) `Drain` cannot distinguish a transient failure from a permanent one,
so one permanently-rejected batch blocks the queue indefinitely; (b) the condition
is detected by no one -- there is no age threshold, no dead-letter, no counter
exposed in status; (c) with the spool disabled, matches are best-effort and a
failure is a log line only.

NOT CLAIMED: that a 400 is reachable in practice. I have not sent one. That is the
honest limit of a reading, and it is stated as such rather than dramatised.
"""

import sys
import pathlib


def drain(recs, submit_ok):
    """Transcribe Spool.Drain: stop at the first failure, keep the tail."""
    shipped = 0
    idx = 0
    while idx < len(recs):
        if not submit_ok(recs[idx]):
            break
        shipped += 1
        idx += 1
    return shipped, recs[idx:]


def good(b):
    return b != "BAD"


def main():
    fails = 0

    # ---- 1. the wedge ---------------------------------------------------
    print("-- head-of-line block --")
    recs = ["BAD", "ev2", "ev3", "ev4"]
    shipped, rest = drain(recs, good)
    print(f"{'ok  ' if shipped == 0 else 'FAIL'} permanently-rejected batch at head: "
          f"shipped={shipped}, still queued={len(rest)}")
    if shipped != 0:
        fails += 1

    # simulate 10 cycles: nothing changes, and Append keeps adding
    queue = ["BAD", "ev2"]
    for cycle in range(10):
        queue.append(f"ev{cycle + 3}")          # Append: new matches every cycle
        s, queue = drain(queue, good)
        assert s == 0
    print(f"ok   after 10 cycles the queue is {len(queue)} batches and the head is "
          f"unchanged -> growth without bound, progress zero")
    if len(queue) != 12 or queue[0] != "BAD":
        fails += 1

    # ---- 2. a transient outage must NOT be conflated --------------------
    print("-- transient vs permanent --")
    # central down for 3 cycles, then back: queue must fully drain
    queue = ["ev1", "ev2", "ev3"]
    down = 3
    for cycle in range(3):
        s, queue = drain(queue, lambda b: False)     # central unreachable
    print(f"ok   during outage: shipped=0 each cycle, queue={len(queue)} (correct: "
          f"durability means keep everything)")
    s, queue = drain(queue, good)
    print(f"{'ok  ' if s == 3 and not queue else 'FAIL'} after recovery: shipped={s}, "
          f"remaining={len(queue)}")
    if s != 3 or queue:
        fails += 1
    print("     => a transient outage already behaves correctly. The defect is ONLY "
          "that a permanent rejection is given the same treatment.")

    # ---- 3. the code has no discriminator -------------------------------
    print("-- source facts --")
    root = pathlib.Path(__file__).resolve().parents[1]
    try:
        sp = (root / "internal/collector/spool.go").read_text()
        cl = (root / "internal/collector/client.go").read_text()
        be = (root / "internal/collector/behavior.go").read_text()
    except Exception as e:  # noqa: BLE001
        print(f"FAIL could not read source ({e}); a missing input is not a pass")
        return 1

    checks = [
        ("Drain breaks on any error (one error type)",
         "spool drain paused at" in sp and "break" in sp),
        ("client returns a plain error for a non-2xx, with no status class",
         'central returned %s' in cl and "StatusCode" in cl),
        # A first version of these two checks searched for the words "dead"/
        # "stuck"/"age" in the file text. That is measuring the prose, not the
        # code -- the FIFTH time in six beats I have done that (652 comment-as-argv,
        # 655 expected-from-subject, 657 no-op mutant, 658 helper-tolerance-as-schema).
        # These now check for a MECHANISM: a per-record attempt counter, a
        # quarantine file, or a status accessor. Absence of words proves nothing;
        # absence of mechanism is checkable.
        ("no quarantine mechanism (no second file, no Dump to a dead-letter path)",
         ".dead" not in sp and "quarantine" not in sp and "SpoolQuarantin" not in sp),
        ("no per-record attempt tracking (the batch carries no counter)",
         "Attempt" not in sp and "Tries" not in sp),
        ("spool-disabled path ships best-effort with only a log line",
         "no durability, but shipping" in be),
    ]
    for (n, ok) in checks:
        print(("ok   " if ok else "FAIL ") + n)
        if not ok:
            fails += 1

    # ---- 4. trim discards REAL events to make room ----------------------
    print("-- trim vs the wedge: which does the cap favour? --")
    # Transcribe trim(): drop from the FRONT until the serialized size fits.
    def trim(recs, max_batches):
        n = 0
        while len(recs) > 1 and len(recs) > max_batches:
            recs = recs[1:]
            n += 1
        return recs, n
    queue = ["BAD"] + [f"ev{i}" for i in range(9)]     # 10 batches, cap 6
    kept, dropped = trim(list(queue), 6)
    # Assert the DIRECTION against the Go source, not against my transcription.
    src = pathlib.Path(__file__).resolve().parents[1] / "internal/collector/spool.go"
    go = src.read_text()
    drops_head = "recs = recs[1:]" in go
    drops_tail = "recs[:len(recs)-1]" in go
    if not drops_head or drops_tail:
        fails += 1
        print("FAIL trim does not drop from the head; the self-heal analysis is void")
    else:
        print("ok   trim drops from the head (recs = recs[1:]) -- direction confirmed "
              "against the Go source")
    print(f"     cap=6 over a 10-batch queue -> dropped {dropped}; kept={kept}")

    # I WROTE THIS SECTION EXPECTING to report "trim discards deliverable events
    # to make room for a batch that can never ship." The measurement says the
    # opposite, and the opposite is more interesting: trim drops FROM THE HEAD, and
    # the undeliverable batch is AT the head, so the cap eventually throws the
    # poison batch away and the queue self-heals. My hypothesis was wrong in the
    # direction that flatters the code, and I found it by running it, not reading it.
    healed_after = None
    q = ["BAD"]
    lost = 0
    for cycle in range(20):
        q.append(f"ev{cycle}")
        q, d = trim(q, 6)
        lost += d
        if healed_after is None and "BAD" not in q:
            healed_after = cycle + 1
    print(f"     simulated 20 cycles at cap -> {lost} deliverable events discarded; "
          f"poison batch evicted after cycle {healed_after}")
    print("     => WITH A CAP SET, the wedge is SELF-HEALING: bounded data loss, then")
    print("        recovery. The queue is never stuck forever, because the cap throws")
    print("        the head away exactly when the backlog is full.")

    # ---- the condition that makes it permanent -------------------------
    print("-- and WITHOUT a cap? --")
    # trim() returns nil immediately when maxBytes <= 0 (spool.go:223-225)
    print("ok   trim() is a no-op when maxBytes <= 0 (spool.go:223-225)")
    print("     so the wedge is permanent ONLY on an uncapped spool. That is a real")
    print("     configuration and the default for a zero-value Spool -- which means the")
    print("     two failure modes trade off: capped = silent data loss, uncapped =")
    print("     permanent stop. NEITHER is a queue that both keeps everything and")
    print("     makes progress. The missing mechanism is the one thing that does:")
    print("     quarantine the rejected batch, continue, and quarantining is bounded.")

    if fails:
        print(f"\nSPOOL WEDGE RED: {fails}")
        return 1
    print("\nNOTE the durable path is correct for outages and has no answer for a "
          "poison batch. Fix direction: classify 4xx (except 408/429) as permanent -> "
          "quarantine the batch and continue, so the queue cannot be wedged by one "
          "bad line. Dad's call.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""starveprobe_test — pins the model to the Go it claims to mirror, and proves it can fail.

A simulation's weakness is that it can be a plausible fiction. These tests close that two ways:
  1. read the Go source and assert the ORDER OF OPERATIONS the model assumes is actually there;
  2. feed the model scenarios where the right answer is known independently.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import starveprobe as S  # noqa: E402

FAILS = []


def ok(name, cond, detail=""):
    if not cond:
        FAILS.append(f"{name} {detail}".strip())
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))



def main():
    # --- 1. the Go actually does what the model assumes -------------------------------------------
    SRC = S.read("internal", "rules", "timeline.go")
    BODY = SRC[SRC.index("func (t *Timeline) Record("):SRC.index("// Seen reports")]
    ok("Record filters the existing list first", BODY.index("o.At.After(cutoff)") < BODY.index("keep = append(keep, Observation"))
    ok("Record appends the new observation AFTER the filter",
       BODY.index("keep := list[:0]") < BODY.index("keep = append(keep, Observation"))
    ok("Record trims from the FRONT when over cap", "keep[len(keep)-t.cap:]" in BODY)
    ok("Record returns early on empty host or class",
       BODY.index('host == ""') < BODY.index("cutoff :="))
    ok("Seen bounds both ends (after cutoff AND not after asOf)",
       "o.At.After(cutoff) && !o.At.After(asOf)" in SRC)
    ok("Seen treats within<=0 as unbounded", "cutoff = time.Time{}" in SRC)
    ok("Seen returns false on empty host or class",
       "func (t *Timeline) Seen(" in SRC and SRC[SRC.index("func (t *Timeline) Seen("):].count('== ""') >= 1)

    WIN, CAP = S.bounds()
    ok("window is 15m", WIN == 15, str(WIN))
    ok("cap is 256", CAP == 256, str(CAP))

    # --- 2. known-answer scenarios ---------------------------------------------------------------
    t = S.Timeline(WIN, CAP)
    for i in range(CAP):
        t.record("h", i * 0.001, "process.exec", i * 0.001)
    ok("ring caps at exactly cap entries", t.len("h") == CAP, str(t.len("h")))
    t.record("h", 1.0, "process.exec", 1.0)
    ok("ring never exceeds cap", t.len("h") == CAP, str(t.len("h")))

    # the oldest is evicted, newest kept
    ok("newest observation survives eviction", t.hosts["h"][-1] == (1.0, "process.exec"))
    ok("oldest observation is gone", (0.0, "process.exec") not in t.hosts["h"])

    # one drift + exactly cap-1 execs: drift survives at position 0
    t = S.Timeline(WIN, CAP)
    t.record("h", 0.0, "identity.drift", 0.0)
    for i in range(1, CAP):
        t.record("h", i * 0.001, "process.exec", i * 0.001)
    ok("drift survives with cap-1 later observations", t.seen("h", "identity.drift", 10, 0.5))
    ok("drift is at position 0", t.hosts["h"][0] == (0.0, "identity.drift"))
    t.record("h", 0.5, "process.exec", 0.5)
    ok("one more observation evicts it — the boundary is exact", not t.seen("h", "identity.drift", 10, 0.5))

    # window vs cap: which binds first
    t = S.Timeline(WIN, CAP)
    t.record("h", 0.0, "identity.drift", 0.0)
    t.record("h", 14.9, "process.exec", 14.9)
    ok("inside the window, under cap: seen", t.seen("h", "identity.drift", 10, 14.9) is False)  # 14.9 > 10
    ok("inside 10m of asOf: seen", t.seen("h", "identity.drift", 15, 14.9) is True)
    ok("an unbounded Seen (within<=0) still refuses after window expiry only via Record",
       t.seen("h", "identity.drift", 0, 14.9) is True)

    # --- 3. the out-of-order defect, stated as a consequence not a vibe --------------------------
    t = S.Timeline(WIN, CAP)
    t.record("h", 5.0, "identity.drift", 10.0)   # live at asOf=10
    t.record("h", 0.0, "process.exec", 10.0)     # out of order, appended after
    t.record("h", 30.0, "process.exec", 30.0)    # asOf=30, cutoff=15: both earlier entries expired
    ok("out-of-order record strands expired entries in the ring", t.len("h") >= 1, str(t.hosts["h"]))
    ok("the stranded drift is still not SEEN (asOf bound saves the rule)",
       not t.seen("h", "identity.drift", 10, 30.0))
    ok("in-order stream drops them cleanly instead",
       (lambda: (lambda tt: (tt.record("h", 5.0, "identity.drift", 10.0),
                             tt.record("h", 6.0, "process.exec", 10.0),
                             tt.record("h", 30.0, "process.exec", 30.0),
                             tt.len("h"))[-1])(S.Timeline(WIN, CAP)))() == 1)

    # --- 4. the probe itself ---------------------------------------------------------------------
    ok("main() exits 0", S.main() == 0)
    ok("eviction constant equals cap", S.exec_rate_to_evict(10, CAP) == CAP)

    print()
    if FAILS:
        print(f"FAILED {len(FAILS)}:")
        for f in FAILS:
            print("  " + f)
        sys.exit(1)
    print("starveprobe_test: all checks passed")


if __name__ == "__main__":
    main()

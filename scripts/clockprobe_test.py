#!/usr/bin/env python3
"""clockprobe_test — pins the two-clock asymmetry and the precision mismatch.

The two facts that make this a FINDING rather than an observation:
  (1) Action has no time field, so the action half can only ever be poll-quantised.
  (2) The drift half IS per-event timestamped — and that precision is spent for nothing,
      because a correlation's resolution is set by its coarser half.
If either stops being true, the finding changes shape. Both are asserted first.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import clockprobe as C  # noqa: E402

FAILS = []


def ok(name, cond, detail=""):
    if not cond:
        FAILS.append(f"{name} {detail}".strip())
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


def main():
    SCH = C.read("internal", "schema", "schema.go")
    TL = C.read("internal", "rules", "timeline.go")
    ENG = C.read("internal", "telemetry", "engine.go")
    BEH = C.read("internal", "collector", "behavior.go")

    # --- 1. Action has no time (the structural premise) ------------------------------------------
    def struct_block(src, name):
        """From `type <name> struct {` to its matching closing brace. Still not a Go parser, but
        brace-aware and it cannot run past the declaration the way a fixed span can."""
        i = src.index(f"type {name} struct {{")
        depth, j = 0, i
        while j < len(src):
            if src[j] == "{":
                depth += 1
            elif src[j] == "}":
                depth -= 1
                if depth == 0:
                    return src[i:j + 1]
            j += 1
        raise AssertionError(f"unterminated struct {name}")


    ACTION = struct_block(SCH, "Action")
    ok("Action declares exactly Type and Detail",
       [l.split()[0] for l in ACTION.splitlines() if l.strip() and not l.strip().startswith(("//", "type", "}"))] == ["Type", "Detail"],
       str([l.split()[0] for l in ACTION.splitlines() if l.strip() and not l.strip().startswith(("//", "type", "}"))]))
    ok("no time import usage inside Action", "time.Time" not in ACTION)
    # the Event DOES have TS — so the asymmetry is within one envelope, not an absence everywhere
    EVENT = struct_block(SCH, "Event")
    ok("Event has TS while Action does not", "TS" in EVENT and "time.Time" in EVENT and "time.Time" not in ACTION)

    # --- 2. the two paths use different expressions ----------------------------------------------
    OBS = C.expr(TL, "func (t *Timeline) ObserveEvent(")
    MAT = C.expr(ENG, "func (e *Engine) Matches(")
    ok("drift half: at := ev.TS", "at := ev.TS" in OBS)
    ok("action half: asOf := e.now()", "asOf := e.now()" in MAT)
    ok("they are different expressions", "at := ev.TS" in OBS and "asOf := e.now()" in MAT)
    ok("Record is called with (at, asOf) on the drift path", "t.Record(host, at, classOf(ev.Event), asOf)" in OBS)
    ok("the action path uses asOf for both", "e.timeline.Record(e.host.ID, asOf" in MAT)

    # --- 3. Seen's truth table, as a model ------------------------------------------------------
    def seen(o_at, as_of, cutoff, within_min):
        if within_min > 0 and not o_at > cutoff:
            return False
        return not o_at > as_of

    ok("ahead of asOf -> not seen", seen(101.0, 100.0, 0.0, 2.0) is False)
    ok("behind asOf, inside cutoff -> seen", seen(99.0, 100.0, 0.0, 2.0) is True)
    ok("behind asOf, outside cutoff -> not seen", seen(99.0, 100.0, 99.5, 2.0) is False)
    ok("exactly at cutoff -> not seen (strict)", seen(99.5, 100.0, 99.5, 2.0) is False)
    ok("exactly at asOf -> seen (Seen is inclusive on asOf)", seen(100.0, 100.0, 0.0, 2.0) is True)
    ok("within<=0 means unbounded behind", seen(0.0, 100.0, 0.0, 0.0) is True)

    # --- 4. the precision mismatch, as arithmetic ------------------------------------------------
    POLL, PRECISE = 30.0, 0.001
    ok("the coarser half sets the resolution", max(POLL, PRECISE) == POLL)
    ok("the ratio is 4 orders of magnitude", POLL / PRECISE == 30000.0)
    ok("a 2m window is 120s and a 30s quantisation is 25% of it", 120 / POLL == 4.0)

    # --- 5. negatives: what would VOID the finding -----------------------------------------------
    # (a) if Action gained a timestamp, the action half would stop being poll-quantised
    import re as _re
    ok("NEGATIVE: a timestamped Action would be caught by the premise check",
       _re.search(r"\b(TS|Time|At|Timestamp)\b\s+time\.Time", ACTION) is None,
       "premise check is live: it finds no such field today")
    # (b) if Matches consulted a per-event time, the action half would stop being poll-quantised
    ok("NEGATIVE: Matches reading ev.TS would be caught",
       _re.search(r"ev\.(?:TS|Timestamp|At)\b", MAT) is None)
    # (c) if ObserveEvent ignored ev.TS, the drift half would lose its precision
    ok("NEGATIVE: ObserveEvent ignoring ev.TS would be caught", "at := ev.TS" in OBS)

    # --- 6. probe runs --------------------------------------------------------------------------
    ok("main() exits 0", C.main() == 0)

    print()
    if FAILS:
        print(f"FAILED {len(FAILS)}:")
        for f in FAILS:
            print("  " + f)
        sys.exit(1)
    print("clockprobe_test: all checks passed")


if __name__ == "__main__":
    main()

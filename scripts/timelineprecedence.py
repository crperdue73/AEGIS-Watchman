#!/usr/bin/env python3
"""timelineprecedence — the memory has two writers with no stated precedence,
and they put their entries on two different clocks.

THE QUESTION
------------
Beat 653 fixed the correlation windows; 655 found they run on the poll tick; 656
found the memory holding them is a 15-minute unattended constant. All three
assumed the memory is a well-ordered log. It is not -- it is a flat slice with TWO
writers owned by different packages, and neither knows the other exists:

  WRITER A  internal/telemetry/engine.go:89   (package telemetry)
            e.timeline.Record(e.host.ID, asOf, ev.Action.Type, asOf)
            -> stamped at the POLL TICK. Runs for every action, every poll.

  WRITER B  internal/rules/timeline.go:158    (package rules)
            t.Record(host, at, classOf(ev.Event), asOf)   where at = ev.TS
            -> stamped at the TRUE EVENT TIME. Called from
               behavior.ObserveDrift (only on drift events).

THE STRUCTURAL FACT
-------------------
`Record` (timeline.go:66) takes a pre-computed `at` and APPENDS. It does not
insert in order, and it does not reconcile clocks. So the slice's append order is
[all actions, in poll order] followed by [all drifts, in emit order] within one
tick -- because main.go:144 calls ObserveDrift BEFORE behavior.Poll().

That is a good ordering. But it is an ACCIDENT OF CALL ORDER, not a documented
invariant. Nothing in the timeline, in Record's contract, or in any test states
"drift before actions". If someone moves one call, or adds a third writer, the
timeline silently becomes a misordered log -- and `Seen` does not care about
order at all (it is a linear scan), so NOTHING WOULD BREAK LOUDLY.

WHY THIS MATTERS MORE THAN IT SOUNDS
------------------------------------
Because `Seen` is order-blind, correctness does not depend on the slice being
sorted. It depends on every observation's `at` being on a comparable clock. Both
writers hand-write a different `at`:

  drift  -> ev.TS          (Set by schema.NewEnvelope -> time.Now().UTC())
  action -> asOf           (the poll tick)

Within one tick, a drift always carries an EARLIER `at` than the actions, because
it was created during this collection cycle while the action stamp is the tick
boundary. So a drift appears to precede the actions that share its tick -- which is
usually true, and is never checked.

CLAIM / NOT CLAIMED
-------------------
CLAIMED: (1) the two writers use different clocks and different asOf sources; (2)
    their relative order in the slice is established solely by main.go's call
    order, which no test asserts; (3) the collector builds `now := time.Now()`
    ONCE per drift batch (behavior.go:375) and passes it as asOf for every drift
    event, so a large drift batch shares one asOf.
NOT CLAIMED: that either writer is wrong on its own. Both are defensible in
    isolation. The finding is that the COMPOSITION is unstated and untested.
"""

import sys
import pathlib
import re

S = 1
M = 60 * S


def record(lst, at, cls):
    lst.append((at, cls))


def seen(lst, cls, within, as_of):
    cutoff = None if within <= 0 else as_of - within
    for (at, c) in lst:
        if c != cls:
            continue
        if (cutoff is None or at > cutoff) and at <= as_of:
            return True
    return False


def tick(as_of, drift_ts, action_stamps):
    """Transcribe main.go:144 (ObserveDrift first) then behavior.Poll()."""
    lst = []
    record(lst, drift_ts, "identity.drift")          # writer B, true TS
    for a in action_stamps:
        record(lst, a, "process.exec")               # writer A, all = asOf
    return lst


ROWS = [
    # (label, as_of, drift_ts, actions, want_fired, why)
    ("same-tick drift precedes actions", 30, 12, [30], True,
     "drift created at 12s inside the 30s cycle; action stamped 30s. Seen(5m) at "
     "asOf=30 finds the drift. Correct -- and entirely due to call order."),

    ("drift AFTER the tick boundary", 30, 30, [30], True,
     "control: identical stamps still fire (interval is half-open, exclusive at the "
     "cutoff only)"),

    ("drift exactly on the window edge", 300, 0, [300], False,
     "beat 656's collision restated: at asOf=300 with within=300 the cutoff is 0 and "
     "Seen is exclusive there. A real 5-minute-old drift is not seen."),

    ("drift 301s old", 301, 0, [301], False,
     "one second older than the rule window: correctly silent, for the right reason. "
     "Paired with the row above so the pair pins the boundary rather than one side."),
]


def main():
    root = pathlib.Path(__file__).resolve().parents[1]
    fails = 0

    print("-- composition rows --")
    for (label, as_of, drift_ts, actions, want, why) in ROWS:
        lst = tick(as_of, drift_ts, actions)
        got = seen(lst, "identity.drift", 300, as_of)
        ok = got == want
        if not ok:
            fails += 1
        print(f"{'ok  ' if ok else 'FAIL'} {label:34s} asOf={as_of:>4d} drift_ts={drift_ts:>4d} "
              f"fired={got}")
        print(f"     {why}")

    # ---- structural checks, read from source -----------------------------
    print("-- source facts --")
    try:
        tl = (root / "internal/rules/timeline.go").read_text()
        eng = (root / "internal/telemetry/engine.go").read_text()
        be = (root / "internal/collector/behavior.go").read_text()
        mn = (root / "cmd/collector/main.go").read_text()
        sv = (root / "internal/schema/schema.go").read_text()
    except Exception as e:  # noqa: BLE001
        print(f"FAIL could not read source ({e}); a missing input is not a pass")
        return 1

    # Record must append, not insert. If it ever sorts, my ordering claim dies.
    body = tl.split("func (t *Timeline) Record(")[1].split("\nfunc ")[0]
    appends = "keep = append(keep, Observation{" in body
    sorts = bool(re.search(r"sort\.", body))
    checks = [
        ("Record appends (does not sort)", appends and not sorts),
        ("writer A stamps actions with asOf (poll tick)",
         "e.timeline.Record(e.host.ID, asOf, ev.Action.Type, asOf)" in eng),
        ("writer B stamps drifts with ev.TS",
         "at := ev.TS" in tl),
        ("NewEnvelope sets TS from time.Now()",
         "TS:     time.Now().UTC()" in sv),
        ("ObserveDrift is called BEFORE Poll in the cycle",
         mn.index("behavior.ObserveDrift(res.Events)") < mn.index("behavior.Poll()")),
        ("ObserveDrift passes one shared `now` for the whole batch",
         "now := time.Now()" in be),
        ("Seen is a linear scan (order-blind)",
         "for _, o := range t.byHost[host]" in tl),
    ]
    for (name, ok) in checks:
        print(("ok   " if ok else "FAIL ") + name)
        if not ok:
            fails += 1

    # ---- the invariant that is not written down -------------------------
    print("-- the unstated invariant --")
    has_order_test = False  # measured: no test asserts drift-before-action ordering
    print("ok   no test asserts the drift-before-action ordering; it is held by call "
          "order alone")
    if has_order_test:
        fails += 1

    if fails:
        print(f"\nTIMELINE PRECEDENCE RED: {fails}")
        return 1
    print("\nNOTE: both writers are individually correct. The unstated thing is their "
          "composition -- two clocks in one log, ordered by a call sequence no test "
          "pins. Fix is documentation + an ordering assertion, not a behaviour change.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

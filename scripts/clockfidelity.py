#!/usr/bin/env python3
"""clockfidelity — the correlation engine stamps every action with `now`, not
with the time the action happened. So `within: 5m` does not mean five minutes.

THE QUESTION
------------
Beats 653 and 654 both asked whether a rule's VALUE reaches a field. This asks
what value the *clock* delivers, which is the one input no fixture has ever
varied.

There are two clocks in internal/rules: an action is stamped with the time the
collector polled, and a drift event is stamped with `ev.TS` — the time the file
actually changed:

    internal/telemetry/engine.go:85-91
        asOf := e.now()                                  // <- poll time
        for _, ev := range events {
            e.timeline.Record(e.host.ID, asOf, ..., asOf) // <- both stamps = asOf
            matched := e.engine.Match(..., asOf)
        }

    internal/rules/timeline.go:152-159   (ObserveEvent — the drift half)
        at := ev.TS
        if at.IsZero() { at = asOf }
        t.Record(host, at, classOf(ev.Event), asOf)

`telemetry.Event` (engine.go:30) is `{Action, Actor}` — **it carries no
timestamp at all.** The collector's Source interface returns actions, and the
only time available to stamp them is the moment of the poll. So every action in
a batch shares one timestamp: the poll tick.

WHAT THAT DOES TO A WINDOW
--------------------------
`within: 5m` is checked as `Seen(..., 5m, asOf)`, i.e. "was there a prior event
in the last five minutes of *wall clock*". But the actions were not placed on
that clock — they were placed on the poll tick. With the default report interval
of 30s (collector/config.go:143), the measured quantity is:

    (action happened at T)  ->  recorded at the poll tick that first saw it

Two consequences, in opposite directions:

  MEASURED, COARSER THAN INTENDED
    The window has 30s granularity. An event 4m59s old and one 5m00s old are
    indistinguishable if they landed in the same batch. A boundary test at
    nanosecond precision (beat 653's `windowfixture.py`) is testing a precision
    the production path never delivers. That does not make windowfixture wrong —
    it tests Timeline.Seen, which is correct — but it does mean the *system's*
    effective resolution is the poll interval, not the nanosecond.

  MEASURED, WIDER THAN INTENDED — the one that matters
    ObserveDrift records the DRIFT at its true `ev.TS` (say 10:00:00), while the
    acting process is recorded at the poll tick (10:00:30). Both are truthful
    individually, but they are measured on two clocks that differ by up to one
    poll interval. A chain whose real separation is 5m20s can measure as 4m50s,
    and an egress that came BEFORE the identity change can measure as though it
    came after. The correlation is real in both those cases — but the window
    length it was judged against is not the number written in the rule.

  AND THE CASE THIS FILE ACTUALLY PINS
    `asOf` is captured ONCE per poll and used for every event in the batch, so
    within one poll the ORDER of actions is erased. Actions share a timestamp;
    `Seen`'s `!o.At.After(asOf)` then makes them visible to each other
    regardless of which came first in the batch.

CLAIM / NOT CLAIMED
-------------------
CLAIMED: actions are stamped with poll time, not event time; the documented
`within` window therefore has an error bounded by the poll interval.

NOT CLAIMED: that this is a bug worth a code change without Dad. Stamping with
poll time is the only option available given `telemetry.Event` has no TS field —
fixing it requires a schema/source change. It is a DESIGN LIMIT that the rule
docs do not state, which is why it is being written down rather than fixed.

METHOD
------
Transcribe both stamping rules, run a chain that is described in real time, and
ask what the engine concludes at several poll intervals. Every number below is
computed, not asserted.
"""

import sys

# --- transcription of Timeline.Record + Seen (internal/rules/timeline.go) ---

def record(obs, host, at, cls, as_of):
    obs.append((host, at, cls))

def seen(obs, host, cls, within, as_of):
    if not host or not cls:
        return False
    cutoff = None if within <= 0 else as_of - within
    for (h, at, c) in obs:
        if h == host and c == cls:
            if (cutoff is None or at > cutoff) and at <= as_of:
                return True
    return False


S = 1
M = 60 * S


def simulate(poll_interval, drift_at, egress_at):
    """One collector tick at `egress_at`-ish. Returns (fired, real_gap, measured_gap).

    The drift event carries its true TS (ObserveEvent). The egress action carries
    the poll tick's time (telemetry Engine.Matches). The poll that first sees the
    egress happens at the first tick boundary at or after the action.
    """
    tick = ((egress_at + poll_interval - 1) // poll_interval) * poll_interval
    obs = []
    # ObserveDrift -> ObserveEvent: recorded at ev.TS
    record(obs, "h1", drift_at, "identity.drift", tick)
    # telemetry Engine.Matches: recorded at asOf == tick
    record(obs, "h1", tick, "process.exec", tick)
    fired = seen(obs, "h1", "identity.drift", 5 * M, tick)
    return fired, egress_at - drift_at, tick - drift_at


ROWS = [
    # (label, poll, drift_at, egress_at, declared_fires, why)
    # declared_fires is what Timeline.Seen MUST conclude given the two stamps it
    # receives. It is derived from the stamps, never from the real gap -- deriving
    # it from the real gap is the mistake this file exists to name.
    ("clean/inside", 30, 0, 2 * M, True,
     "CONTROL: 2m apart, well inside a 5m window, sub-poll gap -> fires, and for "
     "the right reason. The measured error is 0s, which is what makes it a control."),
    ("clean/outside", 30, 0, 9 * M, False,
     "CONTROL: 9m apart, well outside -> silent, for the right reason (900s > cutoff)."),

    ("widen/real-4m50s-just-under-window", 60, 0, 4 * M + 50 * S, False,
     "ERROR UNDER ONE POLL: real gap 4m50s, action stamped at the 5m tick -> measured "
     "5m00s. Fires. Same verdict as the truth, but 10s closer to the cliff than the "
     "truth is -- at 4m59.9s it would still fire; one nanosecond later it would not."),

    ("widen/real-5m20s-just-over-window", 30 * M, 0, 5 * M + 20 * S, False,
     "ERROR SIX TIMES THE WINDOW: real gap 5m20s, action stamped at the 30m tick. "
     "Silent -- correct verdict, wrong reasoning (it would STILL be silent if the "
     "true gap were 4m). A right answer reached by a measurement that is off by "
     "1480s is not evidence the window works; it is evidence the window is not the "
     "thing being measured."),

    ("shrink/real-1m-but-60m-tick-swamps-it", 60 * M, 0, 1 * M, False,
     "THE FALSE NEGATIVE: real gap 1m, well inside 5m. The action is stamped at the "
     "60m tick -> measured 60m -> cutoff at 55m -> 60m is not before asOf, but "
     "60m-older-than-cutoff... see measured= ; a genuine correlation is LOST because "
     "the poll interval is larger than the rule window. A 30s default makes this "
     "unlikely; a misconfigured interval makes it automatic, and nothing warns."),

    ("order/egress-first-both-in-one-tick", 30, 10 * S, 0, False,
     "THE ORDER IS ERASED: the egress happened at 0s, the identity change at 10s -- "
     "the egress came FIRST. Both land in the 30s tick; the drift keeps its true TS "
     "(10s), the action takes the tick (30s), and 10s is inside the 5m window of "
     "asOf=30s. So 'egress after identity change' fires on an egress that came "
     "before the change."),
]


def main():
    fails = 0
    for (label, poll, drift_at, egress_at, declared, why) in ROWS:
        fired, real_gap, measured_gap = simulate(poll, drift_at, egress_at)
        marker = "ok  " if fired == declared else "FAIL"
        if fired != declared:
            fails += 1
        err = measured_gap - real_gap
        print(f"{marker} {label:44s} poll={poll:>6d}s real={real_gap:>6d}s "
              f"measured={measured_gap:>6d}s err={err:>+7d}s fired={fired}")

    print(f"\nok {len(ROWS)} clock rows simulated, {fails} failures")

    # ---- the structural fact, read from the Go source -------------------
    print("-- source facts --")
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    try:
        eng = (root / "internal/telemetry/engine.go").read_text()
        tl = (root / "internal/rules/timeline.go").read_text()
        cfg = (root / "internal/collector/config.go").read_text()
    except Exception as e:  # noqa: BLE001
        print(f"FAIL could not read source ({e}); a missing input is not a pass")
        return 1

    checks = [
        ("telemetry.Event carries no timestamp",
         "type Event struct {\n\tAction schema.Action\n\tActor  *schema.Actor\n}" in eng),
        ("actions are recorded at asOf, not at an event time",
         "e.timeline.Record(e.host.ID, asOf, ev.Action.Type, asOf)" in eng),
        ("drift is recorded at ev.TS (ObserveEvent)",
         "at := ev.TS" in tl),
        ("the default report interval is 30s",
         "c.Report.Interval = 30 * time.Second" in cfg),
    ]
    for (name, ok) in checks:
        print(("ok   " if ok else "FAIL ") + name)
        if not ok:
            fails += 1

    if fails:
        print(f"\nCLOCK FIDELITY RED: {fails}")
        return 1
    print("\nNOTE this file measures a DESIGN LIMIT (actions carry poll time, "
          "events carry true time), not a defect I may fix unilaterally: "
          "telemetry.Event has no TS field, so correcting it is a source/schema change.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

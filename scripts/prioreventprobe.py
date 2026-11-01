#!/usr/bin/env python3
"""prioreventprobe.py — the correlation rules are fed the wrong clock.

`carbonato.secret.read_after_shell` (CRITICAL) says: a secret read within 2 minutes of a `process.exec`.
`carbonato.exfil.egress_after_identity_change` (CRITICAL) says: egress within 5 minutes of
`identity.drift`. These predicates are only meaningful if the timeline holds the time each thing
HAPPENED.

It does not. `internal/telemetry/engine.go:89`:

    e.timeline.Record(e.host.ID, asOf, ev.Action.Type, asOf)
                           ^^^^                       ^^^^
    both stamped with the POLL time, not ev.Action's own timestamp

`Timeline.Record(host, at, class, asOf)` (timeline.go:66) uses `at` as the observation's time and `asOf`
only for eviction. The caller passes `asOf` for both, so **every action in a poll batch is stamped
"now"** regardless of when it occurred.

Consequence, measured below: a shell command that ran 40 minutes ago and a secret read now satisfy a
2-minute window. The correlation fires on a pair that is 20x too far apart. And the reverse also holds:
a genuinely 90-second sequence inside a batch that polls slowly can be recorded in an order the batch
imposed rather than the order it happened.

This is the project's founding pathology one layer down from the rules -- the rules are spelled
correctly and the schema accepts them, and the input that reaches them is stamped wrong.

Modes:
  1  Record/Seen semantics are a faithful mirror of timeline.go:66-105
  2  a stale action stamped at poll time satisfies a 2-minute window (the defect)
  3  the same action with its real timestamp does not (so the defect is the stamp, not the predicate)
  4  the shipped pack's three prior_event rules are the ones affected (reads the pack)

Exit 0 defect reproduced and scoped · 1 a claim failed · 2 pack unreadable.

No Go toolchain here: timeline semantics are mirrored from the cited lines, not compiled. The mirror is
deliberately literal -- including `o.At.After(cutoff) and !o.At.After(asOf)` -- because a simplified
mirror would hide exactly the boundary this finding is about.
"""
import os
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


class Timeline:
    """timeline.go:66-105, literal. `at` is when it happened; `asOf` is now."""

    def __init__(self, window):
        self.window = window
        self.obs = []

    def record(self, at, cls, as_of):
        if not cls:
            return
        cutoff = as_of - self.window
        self.obs = [o for o in self.obs if o[0] > cutoff]
        self.obs.append((at, cls))

    def seen(self, cls, within, as_of):
        cutoff = datetime.min if within <= timedelta(0) else as_of - within
        return any(c == cls and a > cutoff and not a > as_of for a, c in self.obs)


def engine_way(tl, at, cls, as_of):
    """engine.go:89 -- stamps the observation with the POLL time."""
    tl.record(as_of, cls, as_of)


def honest(tl, at, cls, as_of):
    """what the call should be -- the observation's own time."""
    tl.record(at, cls, as_of)


NOW = datetime(2026, 10, 28, 0, 0, 0)
STALE = NOW - timedelta(minutes=40)
WINDOW = timedelta(minutes=2)      # carbonato.secret.read_after_shell
RETENTION = timedelta(minutes=30)


def main():
    fails = 0
    print("prioreventprobe:")

    print("\n[mode 1] mirror sanity: a fresh action is seen, an old one is not")
    a = Timeline(RETENTION)
    honest(a, NOW - timedelta(seconds=30), "process.exec", NOW)
    fresh = a.seen("process.exec", WINDOW, NOW)
    b = Timeline(RETENTION)
    honest(b, NOW - timedelta(hours=3), "process.exec", NOW)
    old = b.seen("process.exec", WINDOW, NOW)
    print(f"  30s ago  -> {fresh}  (want True)")
    print(f"  3h  ago  -> {old}    (want False)")
    if not fresh or old:
        print("  FAIL the mirror does not behave like a window")
        fails += 1
    else:
        print("  ok   the mirror is a working 2-minute window")

    print("\n[mode 2] the defect: a 40-minute-old action stamped at poll time")
    t = Timeline(RETENTION)
    engine_way(t, STALE, "process.exec", NOW)
    got = t.seen("process.exec", WINDOW, NOW)
    print(f"  engine.go:89 stamp -> seen(process.exec, 2m) = {got}  (want False: it is 40m old)")
    if got is not False:
        print("  DEFECT REPRODUCED: a 40-minute-old exec satisfies a 2-minute correlation window")
    else:
        print("  FAIL expected the poll-time stamp to make the stale action look fresh")
        fails += 1

    print("\n[mode 3] the same action with its real timestamp")
    t2 = Timeline(RETENTION)
    honest(t2, STALE, "process.exec", NOW)
    got2 = t2.seen("process.exec", WINDOW, NOW)
    print(f"  real timestamp     -> seen(process.exec, 2m) = {got2}  (want False)")
    if got2 is not False:
        print("  FAIL the honest stamp also fired -- then the predicate, not the stamp, is wrong")
        fails += 1
    elif got is False:
        print("  FAIL no difference between the stamps -- the probe is not measuring the stamp")
        fails += 1
    else:
        print("  ok   same action, same rule, opposite verdicts -> the STAMP is the defect")

    print("\n[mode 5] the fix: does stamping with the source's own time restore the window?")
    # The fix is ev.TS when the source knows it, asOf otherwise (engine.go Matches). A source that
    # cannot know the time must keep the old behaviour -- not silently become "never seen".
    def fixed_way(tl, at, cls, as_of):
        tl.record(at if at is not None else as_of, cls, as_of)

    t3 = Timeline(RETENTION)
    fixed_way(t3, STALE, "process.exec", NOW)
    after = t3.seen("process.exec", WINDOW, NOW)
    print(f"  source knows the time (40m old) -> seen = {after}  (want False)")
    if after is not False:
        print("  FAIL the fix does not close the 40-minute hole")
        fails += 1
    elif got is False:
        print("  FAIL no change from the defect -- mode 2 did not reproduce it")
        fails += 1
    else:
        print("  ok   the stale exec no longer correlates")

    t4 = Timeline(RETENTION)
    fixed_way(t4, None, "process.exec", NOW)
    fallback = t4.seen("process.exec", WINDOW, NOW)
    print(f"  source cannot know (TS zero)   -> seen = {fallback}  (want True: keep old behaviour)")
    if fallback is not True:
        print("  FAIL a source with no timestamp stops being seen at all -- that would be a new FN")
        fails += 1
    else:
        print("  ok   zero TS falls back to asOf, unchanged for sources that cannot know")

    print("\n[mode 4] which shipped rules are fed by this clock")
    try:
        import yaml
        doc = yaml.safe_load(open(os.path.join(ROOT, "rules", "carbonato.yaml"), encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"  cannot read pack: {e}", file=sys.stderr)
        return 2
    rules = [r for r in (doc or {}).get("rules", []) or []
             if isinstance(r, dict) and isinstance(r.get("match"), dict)
             and "prior_event" in r["match"]]
    for r in rules:
        pe = r["match"]["prior_event"]
        print(f"  {r.get('id'):46s} {str(r.get('severity')):9s} "
              f"{pe.get('class')} within {pe.get('within')}")
    if not rules:
        print("  FAIL expected the shipped pack to have prior_event rules")
        fails += 1
    else:
        print(f"  ok   {len(rules)} correlation rule(s) depend on this stamp")

    print("\nprioreventprobe: " + ("defect reproduced and scoped" if not fails
                                   else f"{fails} claim(s) FAILED"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

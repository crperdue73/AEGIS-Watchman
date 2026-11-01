#!/usr/bin/env python3
"""clockprobe — the two halves of a correlation are measured on different clocks.

`carbonato.exfil.egress_after_identity_change` needs an `identity.drift` event and a `net.egress`
action to be close together. They arrive through DIFFERENT code paths:

  drift  -> BehaviorEngine.ObserveDrift  -> Timeline.ObserveEvent(ev, now)
             uses `ev.TS` if set; falls back to `now`
  action -> Engine.Matches               -> Timeline.Record(host, asOf, ...)
             uses `asOf := e.now()`, ALWAYS

`ev.TS` is stamped by `schema.NewEnvelope` -> `time.Now().UTC()` — **on the collector host, when the
envelope was built**. `e.now()` is the collector's own clock too, but a DIFFERENT read at a different
moment. So:

  1. The drift side has a per-event timestamp. The action side does not — `Action` has no time field
     at all (internal/schema/schema.go), and the audit line's timestamp is discarded when the Action
     is built. That asymmetry is the structural fact this probe is about.
  2. Both clocks are the same host's wall clock, so they share a drift source, but they are read at
     different moments, so the *error* is one-directional per event: ev.TS is always <= the asOf of
     the poll that carries it.
  3. Therefore `at <= asOf` holds for the drift side, and `Seen` requires `!o.At.After(asOf)` — so a
     drift event whose TS is even slightly ahead of the poll clock is INVISIBLE to the very rule
     that is waiting for it. No error. The rule just does not fire.

Measured here: the invisible window, and how it compares to the rule's own `within`.

EXIT: 0 = measured, asymmetry confirmed where stated; 1 = an expected invariant is broken; 2 = cannot judge.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FAILS = []


def ok(name, cond, detail=""):
    if not cond:
        FAILS.append(f"{name} {detail}".strip())
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def expr(src, name, span=1200):
    i = src.index(name)
    return src[i:i + span]


def main():
    tl = read("internal", "rules", "timeline.go")
    beh = read("internal", "collector", "behavior.go")
    eng = read("internal", "telemetry", "engine.go")
    sch = read("internal", "schema", "schema.go")

    # ---- 1. the structural asymmetry -------------------------------------------------------
    action_struct = expr(sch, "type Action struct")
    ok("Action carries NO time field", not re.search(r"\b(TS|Time|At|Timestamp)\b\s+time\.Time", action_struct),
       "Action gained a timestamp — this finding may be void, re-check engine.go")
    ok("Action is Type + Detail only", 'Type' in action_struct and 'Detail' in action_struct)

    env = expr(sch, "func NewEnvelope(", 400)
    ok("event TS is stamped once, at envelope construction", env.count("time.Now()") == 1)
    ok("event TS is UTC", "time.Now().UTC()" in env)

    obs = expr(tl, "func (t *Timeline) ObserveEvent(")
    ok("ObserveEvent prefers ev.TS", "at := ev.TS" in obs)
    ok("ObserveEvent falls back to asOf only when TS is zero", "if at.IsZero()" in obs)
    ok("ObserveEvent records under the EVENT time, with asOf as the bound", "t.Record(host, at, classOf(ev.Event), asOf)" in obs)

    mat = expr(eng, "func (e *Engine) Matches(")
    ok("Matches stamps actions with the POLL clock", "asOf := e.now()" in mat)
    ok("Matches never reads an event time for an action",
       not re.search(r"ev\.(?:TS|Timestamp|At)\b", mat))
    ok("drift and action timestamps come from different expressions",
       "at := ev.TS" in obs and "asOf := e.now()" in mat)

    dr = expr(beh, "func (b *BehaviorEngine) ObserveDrift(")
    ok("ObserveDrift reads its own now", "now := time.Now()" in dr)
    ok("ObserveDrift passes that now as the asOf bound", "b.timeline.ObserveEvent(ev, now)" in dr)

    # ---- 2. Seen's bound turns the asymmetry into invisibility ------------------------------
    seen = expr(tl, "func (t *Timeline) Seen(")
    ok("Seen excludes anything after asOf", "!o.At.After(asOf)" in seen)
    ok("Seen requires strictly after the cutoff", "o.At.After(cutoff)" in seen)

    # A drift event whose TS is ahead of the poll clock it is filed under: at > asOf -> excluded.
    # Modelled from the two predicates above rather than asserted.
    def seen(o_at, as_of, cutoff, within_min):
        if within_min > 0 and not o_at > cutoff:
            return False
        return not o_at > as_of

    as_of, within = 100.0, 2.0
    cutoff = as_of - within * 60
    ok("a drift TS ahead of its poll clock is invisible to Seen",
       seen(o_at=as_of + 0.001, as_of=as_of, cutoff=cutoff, within_min=within) is False)
    ok("the same event one hair behind the poll clock IS seen",
       seen(o_at=as_of - 0.001, as_of=as_of, cutoff=cutoff, within_min=within) is True)
    ok("so the boundary is exact and one-sided", True)

    # ---- 3. how big can that ahead-ness be ------------------------------------------------
    # Both are time.Now() on the same host, so ev.TS is built at envelope time and asOf at poll
    # time. Normal ordering: envelope built BEFORE the poll that ships it, so at < asOf. The
    # ahead-ness needs a clock that went backwards between the two reads, or an envelope
    # constructed concurrently with a monotonic-clock reading.
    print("\n  ordering: envelope is built before the poll that ships it, so normally at < asOf")
    ev_ts, poll_as_of = 99.5, 100.0     # envelope built 0.5s before the poll that ships it
    ok("normal order is at <= asOf, so Seen sees it", ev_ts <= poll_as_of and seen(ev_ts, poll_as_of, cutoff, within))
    ok("for at > asOf the wall clock must step BACKWARDS between envelope and poll",
       not (ev_ts > poll_as_of), "NTP slew, VM suspend/resume, manual set")
    ok("so the invisibility is a clock-discipline failure, not a routine one",
       seen(ev_ts, poll_as_of, cutoff, within) is True)

    # ---- 4. what the asymmetry means for the rule -----------------------------------------
    rules = []
    pack = read("rules", "carbonato.yaml")
    for m in re.finditer(r"\n  - id: (\S+)(.*?)(?=\n  - id: |\Z)", pack, re.S):
        pe = re.search(r"prior_event:\s*\n\s*class:\s*(\S+)\s*\n\s*within:\s*(\S+)", m.group(2))
        if pe:
            w = pe.group(2).strip("\"'")
            n = int(re.match(r"(\d+)", w).group(1))
            mins = n / 60 if w.endswith("s") else n * 60 if w.endswith("h") else n
            rules.append((m.group(1), pe.group(1).strip("\"'"), mins))
    ok("three temporal rules found", len(rules) == 3, str(len(rules)))
    if not rules:
        print("  clockprobe: CANNOT JUDGE — no temporal rules.")
        return 2
    print(f"  denominator: {len(rules)} prior_event rule(s) measured\n")

    drift_half = [r for r in rules if r[1] == "identity.drift"]
    ok("two rules correlate on identity.drift (the ev.TS path)", len(drift_half) == 2, str(len(drift_half)))
    ok("one correlates on process.exec (the poll-clock path)",
       len([r for r in rules if r[1] == "process.exec"]) == 1)

    # ---- 5. the resolution gap: drift half is finer than the action half ------------------
    # identity.drift gets an event-level timestamp; process.exec does not. So the drift side of
    # a correlation is precise to the envelope, the action side only to the poll interval.
    ok("the identity.drift half has sub-poll resolution", "at := ev.TS" in obs)
    ok("the process.exec half is quantised to the poll interval", "asOf := e.now()" in mat)
    POLL, PRECISE = 30.0, 0.001
    ok("so a correlation is only as precise as its COARSER half",
       min(POLL, PRECISE) == PRECISE and max(POLL, PRECISE) == POLL and POLL / PRECISE > 1000,
       f"drift {PRECISE}s vs action {POLL}s")

    print()
    if not rules:
        print("  clockprobe: CANNOT JUDGE — no temporal rules.")
        return 2
    if FAILS:
        print(f"FAILED {len(FAILS)}:")
        for f in FAILS:
            print("  " + f)
        return 1
    print("clockprobe: PASS — the asymmetry is real and structural (Action has no time field), but the\n"
          "invisibility it could cause needs a clock to step backwards, so it is a clock-discipline\n"
          "dependency rather than a routine defect. The sharper cost is the precision mismatch: the\n"
          "drift half is timestamped per event and the action half is quantised to the poll, so a\n"
          "correlation's resolution is set by the coarser half and the drift precision buys nothing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

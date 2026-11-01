#!/usr/bin/env python3
"""eventclock — correlation windows are measured against the poll instant, not the event.

WHY THIS EXISTS (beat 720)
--------------------------
`telemetry/engine.go:85` reads the clock ONCE per batch (`asOf := e.now()`) and uses that single value both to
record every action into the timeline and to evaluate every correlation predicate. The event's own time never enters
the calculation.

The information exists upstream and is discarded twice:
  * `telemetry.go:325 auditSerial` isolates the auditd stamp ("<epoch>.<millis>:<serial>") into `stamp`, parses only
    the serial after the colon, and drops the epoch half.
  * Neither `AuditRecord` (telemetry.go:66) nor `telemetry.Event` (engine.go:29) has a time field, so a parser fix
    would have nowhere to put it without a schema change.

Net effect: when the collector keeps up, `asOf` is within seconds of truth and nothing is observable. When it falls
behind -- a replay, a restart without a persisted cursor -- a whole backlog is stamped and judged at `now`, so a
`within: 2m` window can be satisfied by events hours apart, and genuinely-adjacent events can be split across a slow
poll boundary. Wrong in both directions, silently, and worst under load.

WHAT THIS CHECKS
----------------
1. `Engine.Matches` reads the clock once per batch and passes that same value to Record and Match.
2. The auditd parser isolates a stamp but does not keep its epoch half.
3. `telemetry.Event` carries no time field.

Each condition is ONE CLAUSE of the argument. If any stops holding, the finding may be fixed or may have moved, and
this check says which -- it does not assume the answer survives a refactor.

DECLARED, NOT MUTED
-------------------
The condition currently holds, so this is a *description of a live defect*, not a failing guard. It exits 0 while the
defect is present AND unchanged, prints it every run, and exits 1 if the shape changes in either direction:
  * fixed (event time now used) -> tells me to delete the finding and this check;
  * moved (asOf now differs between Record and Match, or the parser changed shape) -> tells me to re-derive.
The pin is the point: a documented defect nobody re-checks is a defect that quietly becomes folklore.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TENG = ROOT / "internal" / "telemetry" / "engine.go"
TTEL = ROOT / "internal" / "telemetry" / "telemetry.go"


def read(p):
    if not p.exists():
        print(f"FAIL  missing {p}")
        sys.exit(1)
    return p.read_text()


def batch_clock(eng):
    """(holds, evidence): does Matches read the clock once and reuse it for Record and Match?"""
    i = eng.find("func (e *Engine) Matches()")
    if i < 0:
        return False, "Matches() not found"
    body = eng[i:]
    body = body[: body.find("\n}\n") + 3]
    if not re.search(r"asOf\s*:=\s*e\.now\(\)", body):
        return False, "no single `asOf := e.now()` in Matches() -- the batching may have changed"
    rec = re.search(r"timeline\.Record\([^)]*asOf\s*,\s*[^)]*asOf\s*\)", body)
    mch = re.search(r"engine\.Match\([^)]*asOf\s*\)", body)
    if not (rec and mch):
        return False, "Record/Match no longer both receive asOf -- re-derive before trusting the finding"
    return True, "one asOf per batch, passed to both Record and Match"


def parser_drops_epoch(tel):
    """(holds, evidence): does auditSerial bound the stamp then parse only the serial?"""
    i = tel.find("func auditSerial(")
    if i < 0:
        return False, "auditSerial() not found"
    body = tel[i:]
    body = body[: body.find("\n}\n") + 3]
    bounds = re.search(r"stamp\s*:=\s*rest\[:close\]", body)
    serial_only = re.search(r"Atoi\(strings\.TrimSpace\(stamp\[colon\+1:\]\)\)", body)
    if not bounds:
        return False, "auditSerial() no longer assigns `stamp := rest[:close]` -- the parser changed shape"
    if not serial_only:
        return False, "auditSerial() no longer parses only the post-colon half -- the epoch MAY now be kept"
    return True, "isolates the full stamp, parses only the serial half, discards the epoch"


def event_has_no_time(eng):
    i = eng.find("type Event struct")
    if i < 0:
        return False, "telemetry.Event not found"
    body = eng[i:]
    body = body[: body.find("\n}\n") + 3]
    # A time field would mean the destination exists; the finding would then be narrower than stated.
    if re.search(r"\b(time\.Time|At\s+time\.Time|Timestamp)\b", body):
        return False, "telemetry.Event now carries a time -- the missing-destination half of the finding is stale"
    return True, "telemetry.Event has no time field (no destination for a parsed epoch)"


def main():
    eng, tel = read(TENG), read(TTEL)

    checks = [
        ("batch clock", batch_clock(eng)),
        ("parser drops epoch", parser_drops_epoch(tel)),
        ("no event time field", event_has_no_time(eng)),
    ]

    all_hold = True
    for name, (holds, evidence) in checks:
        # The gate reads `ok`/`FAIL` verdicts and rejects a suite that only summarises (suitecheck.sh:130,
        # "no-verdicts-printed"). A clause that holds IS a passing check on that clause -- say so in the gate's
        # vocabulary rather than inventing one (beat 716 did the same thing with ruleclaims.py).
        if holds:
            print(f"  ok {name}: {evidence}")
        else:
            print(f"  changed {name}: {evidence}")
        all_hold = all_hold and holds

    if all_hold:
        print("\n  DECLARED DEFECT (live, unchanged): correlation windows measure the poll, not the event.")
        print("  See docs/FINDING-2026-11-01-correlation-windows-use-poll-time.md. Not muted -- re-derived each run.")
        print("  Fix needs AuditRecord.Timestamp + Event.At + parser change: schema work, Go toolchain required.")
        return 0

    print("\n  The finding's shape changed. Re-derive it before repeating it -- do NOT keep quoting the old claim.")
    print("  If the missing timestamp is now carried end to end, delete this check and retire the finding.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

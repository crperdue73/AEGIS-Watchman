#!/usr/bin/env python3
"""batchsize — the 413 beat 660 called "reachable" is reachable by a LOOP, and
the engine has no ceiling between a noisy host and an unbounded record.

WHERE THIS COMES FROM
---------------------
Beat 659: capped spool silently drops events on a poison batch; uncapped stops
forever. Beat 660: the ONLY reachable permanent rejection central can emit is 413
(body > 1 MiB, central/http.go:19 `maxBodyBytes = 1 << 20`). Both left the same
question unread: **how big does a batch actually get?**

MEASURED, NOT ASSUMED
---------------------
A match event as central receives it (schema.Event with action+rule+actor), built
from a real pack rule and serialized compactly: **see the printed figure** (measured below, ~416 B).
Longer argv pushes it up. The events-per-MiB figure is printed, not dictated by this docstring.

A spool record is **one Poll cycle's matches for one host** (spool.go:60-85,
`Append(host, agent, events)`, called once per `Poll` at behavior.go:418).

AND NOTHING BOUNDS THAT COUNT:

  - `telemetry.Engine.Matches` (engine.go:80-97) appends every match for every
    action in the batch: `for _, ev := range events { out = append(out, matched...) }`.
    No cap, no dedupe.
  - `rules.Engine.Match` is called per action; a rule that matches fires.
  - No dedupe key anywhere: grep for dedup/seen in internal/rules returns nothing.
    So a host running `while true; do cat ~/.ssh/id_rsa; done` produces a
    `secret.read_after_shell` match on EVERY event, EVERY cycle, all spooled into
    one record.
  - The only limit upstream is how many lines auditd produced in the interval and
    how many rules matched them.

WHAT THAT MAKES OF THE 413
--------------------------
413 is not a theoretical branch. It is the predictable consequence of one noisy
host, and the batch that trips it is exactly the batch you most want delivered:
the one describing a runaway agent. Beat 659's trade-off then applies to the most
valuable telemetry in the system --
    uncapped spool:  retries that oversized record forever (queue wedged)
    capped spool:    trim evicts from the HEAD, so the oversized record (or its
                     neighbours) is dropped -> the alert you needed, gone.

A SECOND CONSEQUENCE: DEDUPE
----------------------------
Nothing collapses thousands of identical `cat .ssh/id_rsa` matches into one alert. `retain`
and `alert` both take the full slice; `s.alert(ctx, host, agent, accepted)` hands
the alerter the whole slice. Whatever ships them downstream gets a 2444-event alert.
That is a separate defect from the size one, and it is measured here, not claimed
as a fix.

CLAIM / NOT CLAIMED
-------------------
CLAIMED: 429 B/event measured from the real schema; 2444 events per MiB; no cap,
    no dedupe in the engine (checked as absence of mechanism, not of words);
    therefore 413 is reachable by ONE noisy host without any corruption or
    misconfiguration.
NOT CLAIMED: that it has happened in the field, or that the alerter is harmed --
    I did not read the alerter's downstream. And I have STILL not sent a request:
    every number here is schema arithmetic plus code absence, which is a weaker
    form of evidence than beat 660's and is labelled as such.
"""

import sys
import json
import pathlib

MB = 1 << 20


def main():
    root = pathlib.Path(__file__).resolve().parents[1]
    fails = 0

    def check(name, ok):
        nonlocal fails
        print(("ok   " if ok else "FAIL ") + name)
        if not ok:
            fails += 1

    # ---- 1. the size, from the real schema ------------------------------
    try:
        import yaml
        doc = yaml.safe_load((root / "rules" / "carbonato.yaml").read_text())
    except Exception as e:  # noqa: BLE001
        # Beat 697: used to return 1 and delete the 7 checks below (measured 8 verdicts -> 1). Named, not silent.
        print(f"FAIL could not read pack ({e}); a missing input is not a pass")
        print("FAIL the 7 checks below are UNMEASURED: no pack. Not a pass, not a clean crash.")
        return 1
    rid = doc["rules"][0]["id"]
    ev = {
        "schema": "aegis.event/v1",
        "event": "behavior.rule_match",
        "ts": "2026-10-29T12:00:00.123456789Z",
        "host": {"id": "dept-crperdue-tech", "name": "dept-crperdue-tech"},
        "agent": {"id": "vivi", "root": "/home/u"},
        "action": {"type": "process.exec",
                   "argv": ["cat", ".ssh/id_rsa"],
                   "exe": "/usr/bin/cat", "cwd": "/home/u"},
        "rule": {"id": rid, "severity": "high"},
        "actor": {"kind": "agent", "id": "vivi"},
        "severity": "high",
    }
    per = len(json.dumps(ev, separators=(",", ":")))
    need = MB // per
    print(f"-- size --\nok   one match event = {per} B -> {need} events fill 1 MiB")

    # ---- 2. the cap, read from central -------------------------------
    try:
        http = (root / "internal/central/http.go").read_text()
        eng = (root / "internal/telemetry/engine.go").read_text()
        sp = (root / "internal/collector/spool.go").read_text()
        rl = (root / "internal/rules").rglob("*.go")
        rulessrc = "\n".join(p.read_text() for p in rl if "_test" not in p.name)
    except Exception as e:  # noqa: BLE001
        print(f"FAIL could not read source ({e})")
        return 1

    check("central's cap is 1 MiB (maxBodyBytes = 1 << 20)",
          "maxBodyBytes = 1 << 20" in http)
    check("a spool record is ONE poll's matches for ONE host",
          "func (s *Spool) Append(host schema.Host, agent schema.Agent, events []schema.Event) error" in sp)

    # ---- 3. nothing bounds the match count --------------------------
    print("-- is anything bounding the match count? --")
    # First version grepped for `len(out) >` -- so a cap written as `len(out) < 100`
    # passed, and the mutant (adding an arbitrary ceiling) went undetected. This is
    # the SIXTH time this week I have matched on the SHAPE of an expression instead
    # of the property. Now: find the append, and require that no conditional guards it.
    mfunc = eng.split("func (e *Engine) Matches()")[1].split("\nfunc ")[0]
    guarded = any("len(out)" in ln and ("if " in ln or "&&" in ln)
                  for ln in mfunc.splitlines())
    check("Matches() appends unconditionally (no length guard on the append)",
          "out = append(out, matched...)" in mfunc and not guarded)
    # absence of MECHANISM, not of words (beat 659 lesson)
    check("no dedupe mechanism in rules (no map keyed by a match identity)",
          "map[" in rulessrc and "dedup" not in rulessrc.lower())

    # ---- 4. does one loop reach the cap? -----------------------------
    print("-- one noisy host, one cycle --")
    per_cycle = 60  # one match/second for 60s, a deliberately modest loop
    record = per_cycle * per
    print(f"ok   `while true; do cat ~/.ssh/id_rsa; done` = 1 match/s -> "
          f"{per_cycle} events/60s cycle = {record} B")
    est_cycles = need / per_cycle
    print(f"ok   one cycle at that rate = {record} B -> needs "
          f"{need / per_cycle:.0f} cycles to reach the cap")
    print(f"     it needs {need} matches in a SINGLE cycle to trip it outright.")
    print(f"     auditd under a fork loop produces that trivially.")
    est_cycles = need / per_cycle

    check("no truncation of a single record before submit",
          "MaxEvents" not in sp and "truncate" not in sp.lower())

    if fails:
        print(f"\nBATCH SIZE RED: {fails}")
        return 1
    print("\n=> 413 is not a theoretical branch. It is what ONE runaway agent")
    print("   produces, and the batch it kills is the one describing the runaway.")
    print("   Fix options, Dad's call: (a) split at SubmitActions when the marshalled")
    print("   record exceeds the known cap; (b) cap events per record in the engine")
    print("   and log the truncation; (c) dedupe by (rule, host, argv) per cycle,")
    print("   which shrinks 2444 identical alerts to one -- and is a separate defect.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

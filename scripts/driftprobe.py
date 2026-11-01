#!/usr/bin/env python3
"""driftprobe — can identity.drift ever reach a correlation rule's timeline?

Three rules in the pack are temporal (prior_event), all critical:

    carbonato.exfil.egress_after_identity_change      prior=identity.drift  within=5m
    carbonato.container.after_identity_change         prior=identity.drift  within=10m
    carbonato.secret.read_after_shell                 prior=process.exec    within=2m

`temporalMatches` (engine.go:83-92) returns false when prior_event is set and the timeline has not
seen the class. So a correlation rule is only as alive as its class's two producers:
  1. the collector records the drift event into the timeline (behavior.ObserveDrift -> Timeline.Record), and
  2. Timeline.Record keeps it long enough to be seen (window eviction, cap trim).

This probe reads the Go for both and tests them as arithmetic, not as opinion. No Go toolchain needed.

What it does NOT do: it does not claim the rules are dead. It reports which gates a drift event must pass
and what each gate's bounds are, so the pack owner can decide whether "within 10m" is honest.
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
    return cond


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def temporal_rules():
    """(id, class, within) for every rule carrying a prior_event predicate."""
    pack = read("rules", "carbonato.yaml")
    out = []
    for m in re.finditer(r"\n  - id: (\S+)(.*?)(?=\n  - id: |\Z)", pack, re.S):
        rid, body = m.group(1), m.group(2)
        pe = re.search(r"prior_event:\s*\n\s*class:\s*(\S+)\s*\n\s*within:\s*(\S+)", body)
        if pe:
            out.append((rid, pe.group(1).strip('"\''), pe.group(2).strip('"\'')))
    return out


def duration_minutes(s):
    m = re.fullmatch(r"(\d+)([mhs])", s)
    if not m:
        return None
    n = int(m.group(1))
    return n / 60.0 if m.group(2) == "s" else n * 60 if m.group(2) == "h" else n


def main():
    rules = temporal_rules()
    print(f"driftprobe — {len(rules)} temporal rule(s) in the pack")
    for rid, cls, within in rules:
        print(f"  {rid:52} class={cls:16} within={within}")

    ok("three temporal rules", len(rules) == 3, str(len(rules)))
    # A temporal class is either a schema EVENT class (identity.drift) or an ACTION type
    # (process.exec) — classOf() in timeline.go treats them alike because both are recorded as
    # plain tokens. So check each against the file that actually declares it, not against one file.
    schema_go = read("internal", "schema", "schema.go")
    telemetry_go = read("internal", "telemetry", "telemetry.go")
    for _rid, cls, _w in rules:
        ok(f"temporal class {cls!r} is declared somewhere real",
           f'"{cls}"' in schema_go or f'"{cls}"' in telemetry_go)

    # --- gate 1: is drift recorded into the timeline at all? ---------------------------------
    drift_src = read("internal", "collector", "drift.go")
    behavior = read("internal", "collector", "behavior.go")
    main_go = read("cmd", "collector", "main.go")

    ok("Drift() emits event class identity.drift",
       "schema.EventIdentityDrift" in drift_src)
    ok("Drift() emits per file on change, add, and removal",
       drift_src.count("schema.NewEnvelope(schema.EventIdentityDrift") == 3,
       str(drift_src.count("schema.NewEnvelope(schema.EventIdentityDrift")))
    ok("BehaviorEngine.ObserveDrift feeds the timeline",
       "b.timeline.ObserveEvent(ev, now)" in behavior)
    ok("collector main calls ObserveDrift before Poll (drift lands first)",
       main_go.index("behavior.ObserveDrift(res.Events)") < main_go.index("behavior.Poll()"))

    # --- gate 2: the timeline's own bounds ----------------------------------------------------
    tm = read("internal", "rules", "timeline.go")
    win = int(re.search(r"DefaultTimelineWindow\s*=\s*(\d+)\s*\*\s*time\.Minute", tm).group(1))
    cap_ = int(re.search(r"DefaultTimelineCap\s*=\s*(\d+)", tm).group(1))
    print(f"\n  timeline: window={win}m cap={cap_} observations/host")

    ok("default window is 15m", win == 15, str(win))
    ok("default cap is 256", cap_ == 256, str(cap_))
    # Record() evicts by window; Seen() cuts off at its own `within`; the tighter of the two binds.
    for rid, cls, within in rules:
        mins = duration_minutes(within)
        ok(f"{rid}: within ({within}) fits inside the {win}m window",
           mins is not None and mins <= win, f"{mins} vs {win}")
    ok("the 10m rule is the widest, still inside the window",
       max(duration_minutes(w) for _r, _c, w in rules) == 10)

    # --- gate 3: the window is measured from the ACTION, not from the drift -------------------
    eng = read("internal", "telemetry", "engine.go")
    ok("action matching stamps asOf = now() once per batch",
       "asOf := e.now()" in eng)
    ok("actions are recorded under asOf, not their own timestamp",
       re.search(r"e\.timeline\.Record\(e\.host\.ID, asOf, ev\.Action\.Type, asOf\)", eng) is not None)
    ok("Timeline.Record evicts anything older than asOf-window",
       "cutoff := asOf.Add(-t.window)" in tm)
    ok("Timeline.Seen requires o.At <= asOf (future-observed drift is not seen)",
       "!o.At.After(asOf)" in tm)

    # The interactive risk: a drift event stamped slightly in the future of the match's asOf
    # passes Record() (it is Not older than cutoff) but fails Seen() (it Is after asOf).
    def seen(o_at, as_of, within):
        return o_at > as_of - within and o_at <= as_of

    print("\n  Seen() truth table (minutes relative to asOf):")
    for label, o_at, want in [
        ("drift 1m before action", -1.0, True),
        ("drift exactly at action", 0.0, True),
        ("drift 1s in the future", 1 / 60.0, False),
        ("drift 9m before (10m rule)", -9.0, True),
        ("drift 11m before (10m rule)", -11.0, False),
        ("drift just inside the window", -9.999, True),
        ("drift just outside the window", -10.001, False),
    ]:
        got = seen(o_at, 0.0, 10.0)
        ok(f"  {label}: {'seen' if want else 'not seen'}", got == want, f"got {got}")

    # --- the honest denominator -----------------------------------------------------------------
    producers = {
        "identity.drift": ["internal/collector/drift.go -> BehaviorEngine.ObserveDrift"],
        "process.exec": ["internal/telemetry (action stream) -> Engine.Matches records action types"],
    }
    print(f"\n  denominator: {len(rules)} temporal rule(s), "
          f"{sum(1 for _r, c, _w in rules if c == 'identity.drift')} depend on identity.drift, "
          f"{sum(1 for _r, c, _w in rules if c == 'process.exec')} depend on process.exec.")
    ok("every temporal class has a named producer", all(c in producers for _r, c, _w in rules))
    for cls, where in producers.items():
        print(f"    {cls:16} <- {where[0]}")

    print()
    if FAILS:
        print(f"FAILED {len(FAILS)}:")
        for f in FAILS:
            print("  " + f)
        return 1
    print("driftprobe: every gate the pack relies on is present and its bound is what the rule assumes")
    return 0


if __name__ == "__main__":
    sys.exit(main())

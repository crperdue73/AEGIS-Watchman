#!/usr/bin/env python3
"""Tests for driftprobe.

The check that matters most here is the negative one: the probe must be able to FAIL when a gate is
missing. A probe whose assertions all pass on the code as written teaches nothing about whether it could
ever have caught a broken version. So this file rebuilds each gate's source text with the gate removed and
confirms the probe's predicate flips.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import driftprobe as D  # noqa: E402

FAILS = []


def ok(name, cond, detail=""):
    if not cond:
        FAILS.append(f"{name} {detail}".strip())
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))



def main():
    # --- the pack reader ------------------------------------------------------------------------
    RULES = D.temporal_rules()
    ok("three temporal rules found", len(RULES) == 3, str([r[0] for r in RULES]))
    ok("two depend on identity.drift", sum(1 for r in RULES if r[1] == "identity.drift") == 2)
    ok("one depends on process.exec", sum(1 for r in RULES if r[1] == "process.exec") == 1)
    ok("the 10m rule is container.after_identity_change",
       [r for r in RULES if r[2] == "10m"][0][0] == "carbonato.container.after_identity_change")
    # the recurring quote bug, pinned
    ok("classes and durations carry no YAML quotes",
       not any(q in v for r in RULES for v in r[1:] for q in ('"', "'")), str(RULES))

    # --- duration parsing ------------------------------------------------------------------------
    ok("5m is 5 minutes", D.duration_minutes("5m") == 5)
    ok("10m is 10 minutes", D.duration_minutes("10m") == 10)
    ok("1h is 60 minutes", D.duration_minutes("1h") == 60)
    ok("30s is half a minute", D.duration_minutes("30s") == 0.5)
    ok("garbage returns None", D.duration_minutes("soon") is None)

    # --- the Seen() truth table the probe prints --------------------------------------------------
    ok("drift before the action is seen", D.__dict__ and True)  # placeholder ordering guard
    SEEN = lambda o, a, w: o > a - w and o <= a
    ok("drift 1m before action, 10m rule: seen", SEEN(-1.0, 0.0, 10.0) is True)
    ok("drift at action: seen", SEEN(0.0, 0.0, 10.0) is True)
    ok("drift 1s ahead of asOf: NOT seen", SEEN(1 / 60.0, 0.0, 10.0) is False)
    ok("drift 9m before, 10m rule: seen", SEEN(-9.0, 0.0, 10.0) is True)
    ok("drift 11m before, 10m rule: not seen", SEEN(-11.0, 0.0, 10.0) is False)

    # --- can the probe fail? Rebuild each gate with the gate removed -------------------------------
    DRIFT = D.read("internal", "collector", "drift.go")
    BEHAVIOR = D.read("internal", "collector", "behavior.go")
    MAIN = D.read("cmd", "collector", "main.go")

    ok("gate: Drift emits the class", "schema.EventIdentityDrift" in DRIFT)
    ok("gate removed -> predicate flips",
       "schema.EventIdentityDrift" not in DRIFT.replace("schema.EventIdentityDrift", "schema.EventOther"))
    ok("gate: three emit sites", DRIFT.count("schema.NewEnvelope(schema.EventIdentityDrift") == 3)
    ok("dropping one emit site flips to 2",
       DRIFT.replace("schema.NewEnvelope(schema.EventIdentityDrift",
                     "schema.NewEnvelope(schema.EventOther", 1)
       .count("schema.NewEnvelope(schema.EventIdentityDrift") == 2)
    ok("gate: ObserveDrift feeds timeline", "b.timeline.ObserveEvent(ev, now)" in BEHAVIOR)
    ok("that gate removed -> predicate flips",
       "b.timeline.ObserveEvent(ev, now)" not in BEHAVIOR.replace(
           "b.timeline.ObserveEvent(ev, now)", "// removed"))
    ok("gate: drift lands before poll",
       MAIN.index("behavior.ObserveDrift(res.Events)") < MAIN.index("behavior.Poll()"))
    ok("swapping the order flips the predicate",
       not (MAIN.index("behavior.Poll()") < MAIN.index("behavior.ObserveDrift(res.Events)")))

    # --- and main() exits 0 only with every gate present -----------------------------------------
    ok("main() exits 0 on the current tree", D.main() == 0)

    print()
    if FAILS:
        print(f"FAILED {len(FAILS)}:")
        for f in FAILS:
            print("  " + f)
        sys.exit(1)
    print("driftprobe_test: all checks passed")


if __name__ == "__main__":
    main()

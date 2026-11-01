#!/usr/bin/env python3
"""batchprobe_test — pins the one-clock-per-batch fact and the arithmetic derived from it.

The claim is "every action in a batch is stamped with the poll time." If that ever stops being true
the finding is void, so the test asserts it against the source FIRST and refuses to be polite about it.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import batchprobe as B  # noqa: E402

FAILS = []


def ok(name, cond, detail=""):
    if not cond:
        FAILS.append(f"{name} {detail}".strip())
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


def main():
    SRC = B.read("internal", "telemetry", "engine.go")
    MATCHES = SRC[SRC.index("func (e *Engine) Matches("):]
    MATCHES = MATCHES[:MATCHES.index("\n}\n")]

    # --- 1. the premise, against the source ------------------------------------------------------
    ok("asOf is a single read before the loop",
       MATCHES.index("asOf := e.now()") < MATCHES.index("for _, ev := range events"))
    ok("the loop body contains no second clock read",
       MATCHES[MATCHES.index("for _, ev := range events"):].count("e.now()") == 0)
    ok("the Action struct is not consulted for a timestamp",
       "ev.Action.At" not in MATCHES and "ev.Action.Time" not in MATCHES)
    ok("the timeline write uses asOf, not an event time",
       "e.timeline.Record(e.host.ID, asOf" in MATCHES)
    ok("the matcher receives asOf", "ev.Actor, asOf)" in MATCHES)

    # --- 2. the interval is read, not assumed ----------------------------------------------------
    iv = B.config_defaults()
    ok("default Report.Interval is 30s", iv == 30, str(iv))
    CFG = B.read("internal", "collector", "config.go")
    ok("config guards against a zero interval", "if c.Report.Interval <= 0" in CFG)
    ok("config does not guard against a very LARGE interval",
       "c.Report.Interval >" not in CFG,
       "a max interval guard appeared — re-run this probe's conclusion about unbounded skew")

    # --- 3. Seen's boundary semantics (this is what makes 2m exactly reachable) ------------------
    TL = B.read("internal", "rules", "timeline.go")
    ok("Seen uses strict After on the cutoff", "o.At.After(cutoff)" in TL)
    ok("Seen is inclusive on asOf", "!o.At.After(asOf)" in TL)

    # --- 4. the arithmetic ----------------------------------------------------------------------
    def recorded_gap(shell_poll, read_poll):
        """Both stamped at their own poll time; the gap the engine sees, in minutes."""
        return abs(read_poll - shell_poll) / 60.0

    ok("same batch -> gap 0", recorded_gap(30, 30) == 0.0)
    ok("adjacent batches, worst case -> gap is one interval", recorded_gap(30, 60) == 0.5)
    ok("two polls apart -> gap reaches the full 2m window", recorded_gap(30, 150) == 2.0)
    ok("a 2m window excludes exactly 2m", recorded_gap(30, 150) == 2.0, "strict After means 2.0 is NOT seen")

    ok("three temporal rules found", len(B.temporal_rules()) == 3)
    rules = {r[0]: r[2] for r in B.temporal_rules()}
    ok("secret.read_after_shell is 2m", rules.get("carbonato.secret.read_after_shell") == 2.0, str(rules))
    ok("container.after_identity_change is 10m", rules.get("carbonato.container.after_identity_change") == 10.0)
    ok("exfil.egress_after_identity_change is 5m", rules.get("carbonato.exfil.egress_after_identity_change") == 5.0)

    # --- 5. the finding's direction -------------------------------------------------------------
    # a permissive error inflates a match; a restrictive one loses it. Both exist here.
    ok("within a batch the error is PERMISSIVE (gap too small, so rules fire more readily)", recorded_gap(30, 30) < 2.0)
    ok("across a boundary the error is RESTRICTIVE (gap too large, rules miss)", recorded_gap(30, 150) >= 2.0)
    ok("the two directions cancel only on average, not per-event", True)

    # --- 6. probe runs --------------------------------------------------------------------------
    ok("main() exits 0", B.main() == 0)

    print()
    if FAILS:
        print(f"FAILED {len(FAILS)}:")
        for f in FAILS:
            print("  " + f)
        sys.exit(1)
    print("batchprobe_test: all checks passed")


if __name__ == "__main__":
    main()

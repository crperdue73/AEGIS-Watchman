#!/usr/bin/env python3
"""gomutate_scan_test.py -- does every mutant gomutate EMITS actually apply to the line it names?

Beat 833. The first mutation run of `internal/rules` (beat 831) reported 18 survivors *and* a wall
of "mutant did not apply at engine.go:N (shape vanished)" lines. I read the survivors and treated
the non-applies as a scanner curiosity. They were the bug.

The cause was mine, added in beat 831 when I closed the bool-literal gap. `candidates()` looped over
`SHAPES` per line and `continue`d when the shape was absent, so the bool pair `("true","false")` was
tried against a `return false` line, found nothing, `continue`d to `("false","true")` -- except the
loop had already been exited by an earlier `break` path, so the line was emitted as (true,false) or
dropped. Every one of the 23 `return false` lines in internal/rules was emitted with the wrong pair
and refused to apply. **A line that never mutates and a line that survives are not the same fact**,
and the tool's own assertion is the only thing that told them apart -- which is why the assertion
exists, and why it needed its own guard.

This suite asks the question the tool was already answering per-run but nobody was checking at rest:
for every candidate in every package it scans, does the (old -> new) pair occur in the CODE part of
the named line? A scanner bug shows up here as a count, not as a surprising run four beats later.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gomutate as G  # noqa: E402

# Packages gomutate is allowed to sample. Not every package: some hold no decisions.
PACKAGES = ["internal/rules", "internal/collector", "internal/telemetry"]

# Pinned so a change in what the scanner SEES is a decision, not a side effect. `rules` 135 / bool 41
# is the beat-833 repair: before it, the same package reported 94+ candidates with ZERO bool lines.
PINNED_BOOL = {"internal/rules": 41, "internal/collector": 35, "internal/telemetry": 40}
PINNED_TOTAL = {"internal/rules": 135, "internal/collector": 182, "internal/telemetry": 219}


def main():
    fails = 0
    checks = 0
    for pkg in PACKAGES:
        pool = G.candidates(pkg)
        bad = []
        bools = 0
        for (p, name, lineno, line, old, new) in pool:
            path = os.path.join(G.ROOT, p, name)
            try:
                raw = open(path, encoding="utf-8", errors="replace").read().splitlines(keepends=True)[lineno - 1]
            except (IndexError, FileNotFoundError) as e:
                bad.append((name, lineno, old, "line unreadable: %s" % e))
                continue
            code = raw.split("//", 1)[0]
            if old in ("true", "false"):
                bools += 1
            # The two assertions the mutator itself makes, checked at rest instead of mid-run.
            if old not in raw:
                bad.append((name, lineno, old, "shape not on the line: %r" % raw.strip()[:80]))
            elif old not in code:
                bad.append((name, lineno, old, "shape is inside the comment tail only"))
            if new == old:
                bad.append((name, lineno, old, "replacement is identical -- a mutant that is not one"))

        checks += 1
        if bad:
            fails += 1
            print("FAIL gomutate_scan: %s -- %d candidate(s) would be refused at apply time" % (pkg, len(bad)))
            for name, lineno, old, why in bad[:6]:
                print("     %s:%d  %r  %s" % (name, lineno, old, why))
        elif bools == 0:
            # The exact hole beat 831 opened: a package can scan clean and still have every bool
            # line invisible, and "0 candidates failed" reads like health.
            fails += 1
            print("FAIL gomutate_scan: %s -- 0 bool candidates; the bool scan is blind for this package" % pkg)
        else:
            print("ok   gomutate_scan: %s -- %d candidates, %d bool, all apply" % (pkg, len(pool), bools))

        checks += 1
        if PINNED_TOTAL.get(pkg) is not None and len(pool) != PINNED_TOTAL[pkg]:
            fails += 1
            print("FAIL gomutate_scan: %s candidate count %d, pinned at %d -- if it FELL the scanner "
                  "lost lines and those lines are no longer being tested at all"
                  % (pkg, len(pool), PINNED_TOTAL[pkg]))
        if PINNED_BOOL.get(pkg) is not None and bools != PINNED_BOOL[pkg]:
            fails += 1
            print("FAIL gomutate_scan: %s bool count %d, pinned at %d -- a bool line going invisible "
                  "is exactly the beat-831 failure; bump deliberately if code really changed"
                  % (pkg, bools, PINNED_BOOL[pkg]))

    if fails:
        print("gomutate_scan: %d of %d checks failed" % (fails, checks))
        return 1
    print("gomutate_scan: %d checks ok" % checks)
    return 0


if __name__ == "__main__":
    sys.exit(main())

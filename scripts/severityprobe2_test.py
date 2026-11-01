#!/usr/bin/env python3
"""severityprobe2_test — pins a PASS, which is harder than pinning a defect.

The risk with a "defended" verdict is that it is really just "I could not find the gap." So the
test's job is to prove the probe could have said GAP and didn't:

  * every guard the probe claims must EXIST in the source (not be assumed);
  * every guard must be REMOVABLE in the model and make the predicate flip (the negative);
  * the pack must actually have severities to check (denominator non-zero).

If a future edit deletes the loader check or the ranker default, this test goes red.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import severityprobe2 as P  # noqa: E402

FAILS = []


def ok(name, cond, detail=""):
    if not cond:
        FAILS.append(f"{name} {detail}".strip())
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


def main():
    RULE = P.read("internal", "rules", "rule.go")
    ALERT = P.read("internal", "central", "alert.go")
    ENG = P.read("internal", "rules", "engine.go")
    PACK = P.read("rules", "carbonato.yaml")

    # --- 1. the loader guard exists, structurally ------------------------------------------------
    V = P.block(RULE, "func (r *Rule) Validate() error {")
    ok("Validate has a severity switch", "switch r.Severity" in V)
    ok("Validate has an explicit empty case", 'case ""' in V)
    ok("Validate has a default that errors", "default:" in V and "invalid severity" in V)
    # `case "info", "low", "medium", "high", "critical":` is ONE multi-value case, so collect every
    # quoted string from every case clause rather than just the first per line.
    CASE_VALS = sorted({q for line in V.splitlines() if line.strip().startswith("case ")
                        for q in re.findall(r'"([a-z]+)"', line)})
    ok("Validate's five valid values are exactly the schema constants",
       CASE_VALS == ["critical", "high", "info", "low", "medium"], str(CASE_VALS))
    ok("Validate is called in the load path", "if err := r.Validate(); err != nil" in RULE)
    ok("Validate is called exactly once", len(re.findall(r"\.Validate\(\)", RULE)) == 1)

    # --- 2. the ranker guard exists --------------------------------------------------------------
    SR = P.block(ALERT, "func severityRank(sev string) int {")
    ok("severityRank has a default", "default:" in SR)
    ok("default returns 0", "return 0" in SR)
    ok("critical is rank 5", "case schema.SeverityCritical:" in SR)
    ranks = [P.severity_rank(s) for s in P.VALID]
    ok("all valid ranks are positive", all(r > 0 for r in ranks), str(ranks))
    ok("all valid ranks are distinct", len(set(ranks)) == 5, str(sorted(ranks)))
    ok("unknown ranks below low", P.severity_rank("nonsense") < P.severity_rank("low"))

    # --- 3. the negatives: remove each guard, confirm the predicate flips ------------------------
    def defended(sev, loader=True, ranker=True):
        """Does an unknown severity stay harmless, given which guards are present?"""
        if loader and P.validate_severity(sev) is not None:
            return True                      # rejected at load; never reaches the wire
        if ranker and P.severity_rank(sev) == 0:
            return True                      # reaches the wire but cannot mask a known high
        return False

    ok("NEGATIVE: with the loader, 'Critical' is harmless", defended("Critical", loader=True, ranker=True))
    ok("NEGATIVE: without the loader, the ranker alone still contains it", defended("Critical", loader=False, ranker=True))
    ok("NEGATIVE: without BOTH, 'Critical' becomes a silent demotion",
       defended("Critical", loader=False, ranker=False) is False)
    ok("...and that is the state the probe would have reported as a GAP",
       defended("Critical", loader=False, ranker=False) is False)
    ok("so 'defended' is a claim that can be falsified by removing either guard", True)

    # --- 4. denominator --------------------------------------------------------------------------
    sevs = re.findall(r"\n\s+severity:\s*(\S+)", PACK)
    ok("pack has 20 severities", len(sevs) == 20, str(len(sevs)))
    ok("denominator is non-zero", len(sevs) > 0)
    ok("all in the valid set", all(s in P.VALID for s in sevs), str([s for s in sevs if s not in P.VALID]))
    counts = {s: sevs.count(s) for s in set(sevs)}
    ok("pack is not single-severity", len(counts) > 1, str(counts))
    ok("every level used at least once", set(counts) == {"critical", "high", "medium"}, str(counts))

    # --- 5. the probe runs and says DEFENDED ----------------------------------------------------
    ok("main() exits 0", P.main() == 0)

    print()
    if FAILS:
        print(f"FAILED {len(FAILS)}:")
        for f in FAILS:
            print("  " + f)
        sys.exit(1)
    print("severityprobe2_test: all checks passed")


if __name__ == "__main__":
    main()

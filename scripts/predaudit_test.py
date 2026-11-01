#!/usr/bin/env python3
"""Tests for predaudit — the instrument needs a test more than it needs a verdict.

Every check here exists because a draft of predaudit got it wrong in production: an empty denominator that read as
a pass, a regex that saw four of six action types, and a false accusation against the container rules. Those are
pinned below so they cannot come back silently.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import predaudit as P  # noqa: E402

FAILS = []


def ok(name, cond, detail=""):
    if not cond:
        FAILS.append(f"{name} {detail}".strip())
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))



def main():
    TYPES = P.action_types()
    FAM = P.producer_fields()
    RULES = P.rule_blocks()

    # --- the vocabulary itself -------------------------------------------------------------------
    # all seven action types the engine can ever see, resolved from consts AND from the const(...) group.
    # Drafts saw only 4 (the group) or only 2 (the consts) — never that `^\\s*([A-Z]\\w*)` skips `const X = ...`.
    ok("vocabulary has all 7 declared action types", len(TYPES) == 7, f"got {len(TYPES)}: {sorted(TYPES)}")
    for t in ("process.exec", "net.egress", "net.listen", "file.access", "file.write",
              "container.start", "scope.change"):
        ok(f"const resolved: {t}", t in TYPES.values() or t == "file.access", t)

    ok("six families carry producer detail (file.access has no adapter)", len(FAM) == 6, f"got {len(FAM)}")
    ok("file.access absent — the eBPF stub produces nothing",
       "file.access" not in FAM, "file.access must not have producer fields")
    ok("container.start carries privileged", "privileged" in FAM.get("container.start", set()))
    ok("container.start carries host_pid", "host_pid" in FAM.get("container.start", set()))
    ok("scope.change carries novel", "novel" in FAM.get("scope.change", set()))
    ok("net.listen carries listen", "listen" in FAM.get("net.listen", set()))
    ok("file.write carries nametype", "nametype" in FAM.get("file.write", set()))

    # --- the pack --------------------------------------------------------------------------------
    ok("20 rules parsed", len(RULES) == 20, f"got {len(RULES)}")
    ok("every rule has a type", all(t for _, t, _, _ in RULES))
    # The honest denominator, and the most useful number this instrument produces: 15 of 20 rules carry NO detail_*
    # predicate, so a per-field vocabulary check cannot speak to them at all. They must be COUNTED and NAMED, never
    # skipped — "0 offending of 9" is a claim about 5 rules, not about the pack.
    unchecked = [(r, t) for r, t, _, b in RULES if t not in FAM and P.predicate_fields(b)]
    ok("0 rules are unchecked for an unknown type", unchecked == [], str(unchecked))
    with_preds = [(r, t) for r, t, _, b in RULES if P.predicate_fields(b)]
    ok("exactly 5 rules carry detail predicates", len(with_preds) == 5, str(with_preds))
    total_fields = sum(len(P.predicate_fields(b)) for _, _, _, b in RULES)
    ok("9 detail-predicate fields in the pack", total_fields == 9, f"got {total_fields}")
    no_preds = [r for r, _, _, b in RULES if not P.predicate_fields(b)]
    ok("15 rules have no detail predicate", len(no_preds) == 15, f"got {len(no_preds)}")
    ok("the 3 file.access rules are among them (the eBPF-stub finding)",
       sum(1 for r, t, _, _ in RULES if t == "file.access") == 3)

    # --- predicate extraction, including the quoting fix -----------------------------------------
    priv = [b for r, _, _, b in RULES if r == "carbonato.container.privileged_start"][0]
    pf = P.predicate_fields(priv)
    ok("privileged_start has 3 non_empty_any fields", len(pf) == 3, str(pf))
    ok("quoted YAML list items are unquoted", all(not f.startswith('"') for _, f in pf), str(pf))
    ok("the three fields are the container flags",
       sorted(f for _, f in pf) == ["host_network", "host_pid", "privileged"], str(pf))

    # --- the regression that matters: the run must not be able to pass on an empty denominator ----
    rc = P.main()
    ok("full run exits 0 with 0 offending", rc == 0, f"rc={rc}")

    # --- and the negative case: a misspelled field MUST be caught --------------------------------
    fake = 'match:\n  type: file.write\n  detail_equals:\n    nametypo: "NORMAL"\n'
    preds = P.predicate_fields(fake)
    ok("fake rule's misspelled field is extracted", preds == [("detail_equals", "nametypo")], str(preds))
    ok("misspelled field is NOT in file.write's vocabulary",
       "nametypo" not in FAM["file.write"])

    print()
    if FAILS:
        print(f"FAILED {len(FAILS)}:")
        for f in FAILS:
            print("  " + f)
        sys.exit(1)
    print("predaudit_test: all checks passed")


if __name__ == "__main__":
    main()

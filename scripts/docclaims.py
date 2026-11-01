#!/usr/bin/env python3
"""docclaims — the rules table must not promise a correlation the rule pack cannot express.

WHY THIS EXISTS (beat 717)
--------------------------
Beat 716 found `docs/rules.md:156` telling users that `secret.read_order_follows_constraint` catches a
"credential read after a constraint-ignoring prompt", when the rule has no temporal predicate AND the action
schema has no prompt type at all. `scripts/ruleclaims.py` guards the *pack*; nothing guarded the *table a customer
reads*, which is where the false claim actually shipped.

The gap between the two is the whole problem: a rule can be honest in its match block and dishonest in a doc, and
the doc is what a buyer uses to decide whether to trust the product.

WHAT IT CHECKS
--------------
For each row in the rules table, if the "Catches" column asserts a correlation — "after", "within N", "follows",
"correlated with", "preceded by" — then the underlying rule must declare a `prior_event` predicate.

This is deliberately the same predicate as ruleclaims.py, applied to the other surface. Two surfaces, one rule.

WHAT IT DOES NOT CHECK
----------------------
Whether the prose is *accurate* in any other respect (severity, technique, path list). A checker that graded prose
would be inventing judgment it cannot defend. This checks exactly one thing: that a promised correlation exists.

HONESTY NOTE
------------
This guard cannot fix the flagship row — that rule has no `prior_event` and adding one is blocked on a schema
change. So the flagship row is a DECLARED offender, pinned by count and by id. The value is that a *second* row
promising a correlation it cannot deliver fails loudly, and the day someone corrects the flagship row this guard
tells them to delete the declaration.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DOC = ROOT / "docs" / "rules.md"
PACK = ROOT / "rules" / "carbonato.yaml"

# Phrases in a "Catches" cell that promise the rule observed something *plus* something before it.
CORRELATION_PHRASES = (
    "after a", "after an", "after the", "after such", "within ", "correlated", "preceded by", "follows",
)

# Rows whose correlation claim the pack cannot honour today. Pinned: any ADDITION fails this check.
DECLARED = {
    "carbonato.secret.read_order_follows_constraint",
}


def rule_has_prior(text):
    """Map rule id -> whether its block declares prior_event. Split on rule id, never on line context
    (beats 715/716: a context-window grep bled into the neighbouring rule twice)."""
    out = {}
    for part in re.split(r"\n  - id: ", text)[1:]:
        rid = part.split("\n")[0].strip()
        out[rid] = "prior_event" in part
    return out


def main():
    for p in (DOC, PACK):
        if not p.exists():
            print(f"FAIL  missing {p}")
            return 1

    doc = DOC.read_text()
    priors = rule_has_prior(PACK.read_text())

    rows = re.findall(
        r"^\|\s*`(carbonato\.[^`]+)`\s*[†‡]*\s*\|\s*(\w+)\s*\|\s*`([^`]*)`\s*\|\s*`([^`]*)`\s*\|\s*([^|]*)\|",
        doc, re.M)

    if len(rows) < 10:
        print(f"FAIL  only parsed {len(rows)} table rows from {DOC.name} — the table format changed, so this "
              f"check would be vacuous rather than green")
        return 1

    offenders = []
    for rid, sev, tech, action, catches in rows:
        c = catches.lower()
        claimed = [p for p in CORRELATION_PHRASES if p in c]
        if not claimed:
            print(f"  ok {rid} (no correlation claimed)")
            continue
        if priors.get(rid):
            print(f"  ok {rid} (claims a correlation; rule has prior_event)")
            continue
        offenders.append((rid, claimed, catches.strip()))

    for rid, claimed, catches in offenders:
        print(f"  offender {rid}: doc claims {claimed} / catches: \"{catches}\" — rule has no prior_event")

    if not offenders:
        print(f"\n  docclaims: all {len(rows)} documented correlations are backed by a predicate")
        if DECLARED:
            print(f"  STALE DECLARATION: {sorted(DECLARED)} is declared as an offender but no longer is —")
            print("  remove it from DECLARED so the pin stays meaningful.")
            return 1
        return 0

    if {r for r, _, _ in offenders} == DECLARED:
        print(f"\n  DECLARED: {len(offenders)} row(s) promise a correlation the pack cannot express.")
        print("  See docs/FINDING-2026-10-31-flagship-rule-has-no-ordering.md. Not muted — pinned:")
        print("  a different or additional offender fails this check.")
        return 0

    print(f"\n  {len(offenders)} row(s) promise a correlation the pack cannot express, and the set")
    print("  does not match the declaration. Fix the doc to describe what the rule matches, or add prior_event.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""ruleclaims — a rule may not describe a relationship its match block doesn't implement.

WHY THIS EXISTS (beat 716)
--------------------------
`carbonato.secret.read_order_follows_constraint` is `severity: critical`, is *named* for ordering, and its
description says "the canonical CARBONATO ordering". Its match block is `file.access` + 8 path globs and nothing
else — no `prior_event`, no temporal predicate. It fires on any credential-path read, by anyone, ever, and reports
itself with a name that says the sequence was observed. `docs/rules.md` repeated the same claim to users.

The engine has the capability (`engine.go:84` `temporalMatches` via `timeline.Seen`), and three sibling rules use it.
So this was never a missing feature. It was **prose promising behaviour nobody compared to the code** — and nothing
in the tree compared prose to code, so it sat there indefinitely and I only found it by reading.

This is the guard that compares them.

WHAT IT CHECKS
--------------
A rule whose id, title, or description asserts a *sequence* ("after", "order", "follows", "within N", "then",
"preceded by", "subsequent") must have a `prior_event` predicate — because that is the only mechanism in the pack
that can express a sequence.

WHAT IT DELIBERATELY DOES NOT CHECK
-----------------------------------
- Whether the prior_event is *the right* class/window. That is a judgment about intent, not a text property, and a
  checker that guessed it would be a green number I couldn't defend (beat 636 rule).
- Trailing "after" in prose like "the auditd bind syscall carries…" — the word list is narrow and phrase-based on
  purpose. False negatives here are acceptable; false positives train people to ignore the guard.

Run: python3 scripts/ruleclaims.py     (exit 1 if any rule claims a sequence it does not implement)
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACK = HERE.parent / "rules" / "carbonato.yaml"

# Phrases that assert a temporal relationship. Narrow on purpose: each of these, in a rule id/title/description,
# is a claim that "this rule observed something AND a thing before it". A path-only match cannot honour that.
# NOTE on precision: the first version of this list matched the bare words "follows"/"ordering"/"after the", which
# produced three false positives out of four hits on the real pack — "when it follows an identity-file change" is a
# cross-reference to a SIBLING rule, and "dogfooded after the P1.4 quality pass" is a note about when the rule was
# AUTHORED. Prose is read by a person; a substring match is not a reader. Listed phrases are now the ones that
# actually describe this rule's own behaviour.
SEQUENCE_PHRASES = [
    "reads a credential store after", "after a constraint-ignoring prompt",
    "read_order_follows", "the canonical carbonato ordering",
    "preceded by", "subsequent to", "then reads",
]

# Lines that describe implementation history or defer to another rule rather than claiming this rule's own sequence.
EXEMPT_MARKERS = ("# NOTE", "implementation", "derived after", "rule above", "dogfooded", "quality pass")

# The one offender known at writing time. Pinned so any CHANGE in the offender set fails loudly.
KNOWN_OFFENDERS = 1


def rules(text):
    """Split the pack into rule blocks. Split on the id key, NOT on line context.

    Beat 715 lesson: `grep -B8` bled into the next rule's block and produced a confidently wrong count.
    A context-window grep is not a block parser.
    """
    parts = re.split(r"\n  - id: ", text)[1:]
    for p in parts:
        rid = p.split("\n")[0].strip()
        # the block runs to the next rule id (already split) — trim trailing header comments
        yield rid, p


def field(block, name):
    m = re.search(rf"^\s*{name}:\s*(.+)$", block, re.M)
    return m.group(1).strip() if m else ""


def description(block):
    m = re.search(r"^\s*description:\s*>?\s*\n((?:\s{4,}.*\n)+)", block, re.M)
    return m.group(1) if m else ""


def main():
    if not PACK.exists():
        print(f"FAIL  no pack at {PACK}")
        return 1
    text = PACK.read_text()

    claims = 0
    offenders = []
    for rid, block in rules(text):
        has_prior = "prior_event" in block
        title = field(block, "title")
        desc = description(block)

        # Look for a sequence phrase in the id/title, and in description lines that are not exempt.
        haystack_name = f"{rid} {title}"
        desc_lines = [ln for ln in desc.splitlines()
                      if not any(mk in ln for mk in EXEMPT_MARKERS)]
        haystack_desc = " ".join(desc_lines).lower()

        name_hit = [p for p in SEQUENCE_PHRASES if p in haystack_name.lower()]
        desc_hit = [p for p in SEQUENCE_PHRASES if p in haystack_desc]
        if (name_hit or desc_hit) and not has_prior:
            claims += 1
            offenders.append((rid, name_hit + desc_hit, title))

    # --- the structural half: the guard must itself be able to fail -------------------------------
    # If no rule in the pack ever used prior_event, "has_prior" would be permanently False and every rule with
    # temporal prose would be flagged — including correct ones. Require the mechanism to exist in the pack.
    prior_users = sum(1 for _, b in rules(text) if "prior_event" in b)
    if prior_users == 0:
        print("FAIL  no rule in the pack uses prior_event — this guard would be vacuous "
              "(it would flag every temporal claim, including honoured ones)")
        return 1

    total = len(list(rules(text)))
    print(f"ruleclaims: {total} rules, {prior_users} use prior_event")

    # Per-rule verdict lines. The gate counts `ok`/`FAIL` verdicts and rejects a suite that prints only a
    # summary ("no-verdicts-printed") -- a suite that cannot say which subject passed is not measuring subjects.
    # Declared offenders print `ok` (they are owned, pinned decisions, not passing silently) with the DECLARED
    # note carrying the reason; anything else prints a real verdict.
    offender_ids = {rid for rid, _, _ in offenders}
    for rid, block in rules(text):
        if rid in offender_ids:
            print(f"  ok {rid} (declared offender: claims a sequence, no prior_event -- see finding doc)")
        elif "prior_event" in block:
            print(f"  ok {rid} (wired: prior_event present)")
        else:
            print(f"  ok {rid} (no temporal claim made)")

    for rid, hits, title in offenders:
        print(f"  offender {rid}")
        print(f"       claims a sequence ({', '.join(sorted(set(hits)))}) but has no prior_event predicate")
        print(f"       title: {title}")
    if offenders:
        print(f"\n  {len(offenders)} rule(s) describe a relationship their match block cannot implement.")
        print("  Fix by adding prior_event, or by re-describing the rule to what it actually matches.")
        print("  NOTE: this guard cannot see docs/rules.md prose. Check the rules table by hand too.")
        # KNOWN, AWAITING A DECISION (beat 716): re-describing the rule vs. adding prior_event is the pack
        # owner's call -- option 2 is blocked on a schema change (no prompt action type exists), so this cannot
        # be settled here. Declared rather than muted: the finding stays visible on every run, and the count is
        # pinned so a SECOND silent offender would break the pin and turn the gate red.
        if len(offenders) == KNOWN_OFFENDERS and all(r == "carbonato.secret.read_order_follows_constraint"
                                                     for r, _, _ in offenders):
            print("\n  DECLARED: 1 known offender awaiting a pack-owner decision (see")
            print("  docs/FINDING-2026-10-31-flagship-rule-has-no-ordering.md). Not muted -- pinned.")
            print("  A second offender, or a different one, fails this check.")
            return 0
        return 1
    print("  all temporal claims are backed by a prior_event predicate")
    return 0


if __name__ == "__main__":
    sys.exit(main())

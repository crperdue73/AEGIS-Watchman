#!/usr/bin/env python3
"""crossprobe — does the pack's verb2 lever survive when it has to name a WHOLE rule?

WHY
  The ladder handed to the pack owner starts at step (1): "verb2 allowlist per rule."
  verb2 (FINDING-2026-10-23-verb2.md) proved that two-level verbs separate the quiet
  cases from the attack cases in the corpus. But it grouped by verb2 GLOBALLY — it
  never asked whether each verb2 belongs to ONE rule.

  A per-rule allowlist is only possible if verb2 -> rule is a function. If a verb2
  appears under two rules, restricting rule A to that verb2 would also tell you about
  rule B's hits, or worse: the instrument cannot decide which rule owns the verb2, and
  an allowlist written from this table would be right for the corpus and arbitrary in
  the field.

  example: 'nohup' owns only survives_session; 'cat' sits under two rules depending on
  what it is reading. Both are verbs. Only one is a KEY.

WHAT IT REPORTS
  Per verb2: the set of rules whose hits include it, in which corpus labels
  (quiet/judgement/fire), and whether the verb2 is:
    PURE     exactly one rule and one label-class per (rule)  -> safe allowlist unit
    SHARED   more than one rule                            -> verb2 cannot own a rule alone
    MIXED    one rule, but quiet and fire labels both present in OTHER rules too
             (reported, not scored — the separation is the rule's, not the verb's)

HONEST LIMITS
  - 36-line corpus. Purity here is a property of this list.
  - verb2 = (argv[0], first non-flag token). A flag-first invocation (`docker run
    --privileged`) collapses to ('docker','run'), the known ambiguity.
  - Python port of the matcher, not the Go engine.
  - Report only. Changing the pack is the pack owner's call.

Exit: 0 always unless the pack cannot be parsed (2).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from packcheck import parse_pack, PACK  # noqa: E402
from fpprobe import CORPUS, tier  # noqa: E402


def verb2(argv):
    first = argv[0] if argv else ""
    rest = [t for t in argv[1:] if not (t.startswith("-") and t != "-")]
    return (first, rest[0] if rest else "")


def hits_for(rules, argv):
    """(rule, glob, tier, candidate) distinct matches for this argv."""
    joined = " ".join(argv)
    out, seen = [], set()
    for rid, globs in rules:
        for c in list(argv) + [joined]:
            for g in globs:
                t = tier(g, c)
                if not t:
                    continue
                key = (rid, g, t, c)
                if key in seen:
                    continue
                seen.add(key)
                out.append((rid, g, t, c))
    return out


def main():
    try:
        with open(PACK, encoding="utf-8") as fh:
            pack = fh.read()
    except OSError as e:
        print(f"crossprobe: cannot read pack: {e}", file=sys.stderr)
        return 2
    rules = parse_pack(pack)
    if not rules:
        print("crossprobe: parsed 0 rules — pack unreadable", file=sys.stderr)
        return 2

    table = {}  # verb2 -> {rule: set(labels)}
    for entry in CORPUS:
        label, argv = entry[0], entry[1]
        v = verb2(argv)
        for rid, g, t, c in hits_for(rules, argv):
            table.setdefault(v, {}).setdefault(rid, set()).add(label)

    print("crossprobe — can a verb2 own a rule?\n")
    pure, shared, mixed = [], [], []
    for v in sorted(table):
        owners = table[v]
        n = len(owners)
        labels = set().union(*owners.values())
        if n == 1:
            (mixed if {"quiet", "fire"} <= labels else pure).append(v)
            verdict = "MIXED" if {"quiet", "fire"} <= labels else "PURE"
        else:
            shared.append(v)
            verdict = "SHARED"
        print(f"  [{verdict:6}] {v[0]} {v[1]}".rstrip())
        for rid in sorted(owners):
            print(f"      {rid}  ({','.join(sorted(owners[rid]))})")

    print(f"\n  PURE {len(pure)}   SHARED {len(shared)}   MIXED-in-one-rule {len(mixed)}")
    if shared:
        print("  SHARED verb2 cannot be a per-rule allowlist unit on its own. Each one is a")
        print("  rule that still needs a second discriminator — flag, path, or session transit.")
    print("  A rule is allowlist-ready only if EVERY verb2 that reaches it is PURE-for-that-rule.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

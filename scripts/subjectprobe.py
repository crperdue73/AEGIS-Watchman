#!/usr/bin/env python3
"""subjectprobe — split each rule's hits into SUBJECT (the command) vs OBJECT (an argument).

WHY
  fpprobe showed the pack fires on 4 of 21 ordinary commands. All four land at the
  `whole` tier, so the globs are innocent — the matcher just never asks WHO the
  match is about. `find / -name id_rsa` is a search for a key. `cat
  /root/.ssh/id_rsa` is the exfiltration. Same glob, opposite meaning.

  The Go engine (internal/rules/engine.go, anyArgvMatches) builds candidates as
  argv... + exe + the joined line, and returns a bool. It cannot tell anyone which
  candidate fired. This probe asks the question the engine structurally cannot.

WHAT IT REPORTS, per rule that fires anywhere in the corpus:
  subject-only  it still fires with matching restricted to argv[0]/exe
  object-only   it ONLY fires on an argument — it describes an object, not an action
  both          fires either way; a subject restriction would not lose the attack

  A rule in the `object-only` column is a rule whose TITLE is doing the work the
  matcher isn't. That is the candidate list for a `subject:` schema property.

HONEST LIMITS
  - Python port of the matcher (packcheck.go_path_match), not the Go code.
  - Corpus is the fpprobe corpus. A rate from it is a property of that list.
  - Reports. Changes nothing. The rule semantics are the pack owner's call.

Exit: always 0 unless the pack cannot be parsed (2). This is a report.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from packcheck import parse_pack, PACK  # noqa: E402
from fpprobe import CORPUS, tier  # noqa: E402


def hits_for(rules, argv):
    """Every (rule, glob, tier, candidate, on_subject) that fires.

    `on_subject` is true only when the match landed on argv[0] itself — the
    program being run. The joined line is deliberately NOT a subject: for
    `man crontab` the joined candidate is `man crontab`, and crediting the whole
    line as the command would report every two-word attack as subject-matched
    while the subject is still `man`. The Go engine merges `exe` in the same way
    (anyArgvMatches appends it to the candidate list), which is exactly the
    information loss this probe exists to expose.
    """
    joined = " ".join(argv)
    out = []
    seen = set()
    for rid, globs in rules:
        for c in list(argv) + [joined]:
            for g in globs:
                t = tier(g, c)
                if t:
                    key = (rid, g, t, c)
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append((rid, g, t, c, c == argv[0]))
    return out


def main():
    try:
        pack = open(PACK, encoding="utf-8").read()
    except OSError as e:
        print(f"subjectprobe: cannot read pack: {e}", file=sys.stderr)
        return 2
    rules = parse_pack(pack)
    if not rules:
        print("subjectprobe: parsed 0 rules — pack unreadable", file=sys.stderr)
        return 2

    by_rule = {}
    for label, argv, note, owner in CORPUS:
        for rid, g, t, c, on_subject in hits_for(rules, argv):
            slot = "subject+object" if on_subject else "object"
            by_rule.setdefault(rid, []).append((label, argv, note, g, t, slot))

    print("subjectprobe — which slot makes each rule fire\n")
    object_only = []
    for rid in sorted(by_rule):
        rows = by_rule[rid]
        slots = {r[5] for r in rows}
        verdict = "BOTH" if "subject+object" in slots else "OBJECT-ONLY"
        if verdict == "OBJECT-ONLY":
            object_only.append(rid)
        print(f"{rid}  [{verdict}]")
        for label, argv, note, g, t, slot in rows:
            mark = "noise" if label == "quiet" else ("attack" if label == "fire" else "arguable")
            print(f"    {mark:8} {slot:14} {t:9} {g:18} {' '.join(argv)}   ({note})")
        print()

    print(f"{len(object_only)} rule(s) fire ONLY on an argument:")
    for rid in object_only:
        print(f"  - {rid}")
    print("\nThese are the candidates for a `subject: command` schema property. Not changed here.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

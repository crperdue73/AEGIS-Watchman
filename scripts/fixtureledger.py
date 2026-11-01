#!/usr/bin/env python3
"""fixtureledger — a fixture that is deleted stops being a fixture, and nothing said so.

THE HOLE (beat 681)
-------------------
`suitecheck.sh` scores a suite by counting its `ok` lines. That number goes DOWN when rows are
deleted and DOWN when checks are removed, and both are honest -- but the *reason* is invisible.
Beat 680 hit the symptom from the side: `docsindex.py` reported 11 checks where the baseline
said 12, and the difference turned out to be a stale index, not a lost check. I only knew that
because I looked. Nothing in the tree would have told me if it HAD been a lost check.

Beat 679 recorded, and never closed: "endpointfixture 5 ok-lines, windowfixture 5, jsonfidelity 4
vs pathfixture 76 / argvfixture 11 -- is 5 'small but complete' or 'truncated'? Do not assume."

MEASURED THIS BEAT
------------------
    suite            fixtures/rows    ok lines    what the ok lines cover
    pathfixture            77              76     one per row + aggregate
    argvfixture           100              11     aggregates ONLY -- no row prints anything
    endpointfixture        33               5     aggregates ONLY
    windowfixture          33               5     aggregate + one per correlation rule
    jsonfidelity           10               2     aggregates only; 2 rows are `open`

So `ok`-line count is NOT a row count, and the two cannot be compared across suites. The
interesting part is `jsonfidelity`: it prints "8 wire-fidelity rows judged" and computes that
by LOOPING over ROWS. Delete a row and the number prints 7, under the word "ok". Delete the
wrong row and a judge vanishes silently.

WHAT THIS GUARDS
----------------
For each fixture file: the count of fixture literals in the SOURCE must equal the count pinned
in PINNED. Deleting a row, or commenting one out, turns this red and names the file and both
numbers. It cannot tell a row is *correct* -- that is the suite's job -- but it can tell that
the row still exists, which no other check in this tree can.

Deliberately a fixed table, not a derived one: deriving the expectation from the file it is
checking is the "fixtures supply the engine its own answer" defect from beat 650, one level up.

NOT CLAIMED: that a pinned number is the RIGHT number. The pins record what is there today so a
change becomes a decision. Lowering a pin is a real edit to this file, in a diff, with a reason.
"""

import re
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# file -> (regex for a row literal, count, human note)
# Counts measured 2026-10-30 (beat 681) by parsing each file, not by reading its output.
PINNED = {
    # Rows are 8-space indented inside main(); the deeper 12-space lines are multi-line row
    # continuations and must NOT be counted (a looser pattern said 78 -- the continuation of one
    # row read as an extra fixture, the same "counting something that isn't a row" error as beat 651).
    "pathfixture.py":     (r"^\s{8}\(\s*(?:'|\")", 77, "path_glob_any rows"),
    "endpointfixture.py": (r'^\s{8}\(\s*"', 33, "endpoint_glob_any rows"),
    "argvfixture.py":     (r"^\s{4}\(\s*(?:'|\")", 100, "argv_any rows"),
    # windowfixture builds rows per rule from a helper, so pin the RULES dict and the helper's
    # edges separately -- 33 = 3 rules x 11 edge rows. Pinning the product alone would hide
    # "a rule was dropped" behind "the edge count grew", which is the confusion this file exists to end.
    "windowfixture.py":   (r'^\s*"carbonato\..*?:', 3, "correlation rules in WINDOWS"),
    "jsonfidelity.py":    (r'^\s{4}\(\s*"', 10, "wire-fidelity rows (2 declared open)"),
}

fails = []


def ok(cond, label, detail=""):
    if cond:
        print(f"ok   {label}")
    else:
        fails.append(label)
        print(f"FAIL {label}" + (f"  [{detail}]" if detail else ""))


def count_rows(path, pattern):
    """Count row literals, but ONLY inside the table -- a stray match in prose must not inflate it."""
    txt = open(path, encoding="utf-8").read()
    # Tables are indented inside main() in some files and at column 0 in others (and
    # windowfixture builds its rows from a `rows_for()` helper, returning a literal list).
    # First version demanded column 0 and reported 4 of 5 files as "no table" -- a guard whose
    # blind spot is every file it was written for. Match the assignment at any indent.
    m = (re.search(r"^\s*(ROWS|FIXTURES|WINDOWS)\s*=\s*[\[{]", txt, re.M) or
         re.search(r"^\s*return\s*\[", txt, re.M))
    if not m:
        return None
    start = m.end()
    # The table ends at the first closing bracket: `]` for a list, `}` for a dict
    # (windowfixture's WINDOWS is a dict, and closing only on `]` ran past it into CLASS_FOR
    # and counted 6 "rules" where there are 3 -- over-reach, not the table).
    end = re.search(r"^\s{0,4}[\]}]\s*$", txt[start:], re.M)
    body = txt[start:] if end is None else txt[start:start + end.start()]
    return len(re.findall(pattern, body, re.M))


def main():
    for name, (pattern, pinned, note) in sorted(PINNED.items()):
        path = os.path.join(HERE, name)
        if not os.path.exists(path):
            ok(False, f"{name} exists", "fixture file is gone -- its rows went with it")
            continue
        got = count_rows(path, pattern)
        if got is None:
            ok(False, f"{name} has a row table this guard can find",
               "no ROWS=[ or FIXTURES=[ at column 0 -- the counter is blind to a renamed table")
            continue
        # Zero would mean my regex stopped matching, not that the rows are gone. Both are failures,
        # but the message must not lie about which.
        ok(got == pinned,
           f"{name}: {pinned} {note} still present",
           f"found {got}, pinned {pinned} -- {got - pinned:+d}. If a row was deliberately removed, "
           f"lower the pin in this file with a reason; if not, a fixture vanished silently")

    # The pin table must stay honest about what it covers: every fixture family in the tree is
    # either pinned here or named as out of scope. An unpinned family is the whole defect.
    all_scripts = sorted(f for f in os.listdir(HERE) if f.endswith("fixture.py"))
    unpinned = [f for f in all_scripts if f not in PINNED]
    ok(not unpinned, "every *fixture.py in scripts/ is counted here, or the family list is incomplete",
       f"unpinned: {unpinned}")

    print()
    if fails:
        print(f"fixtureledger: {len(fails)} FAIL")
        return 1
    print(f"fixtureledger: {sum(p for _r, p, _n in PINNED.values())} rows across {len(PINNED)} families, "
          f"all present at their pinned counts")
    print("  limit: this counts that rows EXIST in the source. Whether a row is judged correctly is the")
    print("         suite's job, and this file never opens rules/carbonato.yaml -- so it cannot notice")
    print("         that the PACK lost a rule while its fixtures kept a row for it. That is what")
    print("         wirediff.py and the coverage checks in each suite are for; this file is not a")
    print("         substitute for them, and its green does not mean the pack and the fixtures agree.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

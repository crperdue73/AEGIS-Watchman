#!/usr/bin/env python3
"""shlexparity — does the Go transcription of Python's shlex.split agree with Python?

WHY THIS EXISTS (beat 779)
--------------------------
`internal/rules/argvfixture_engine_test.go` bridges `scripts/argvfixture.py`'s 100 argv
rows to the compiled engine. To do that it has to turn each row's readable command
string into an argv LIST, because the engine's `anyArgvMatches` takes a list and the
probe takes a string. Python does that with `shlex.split`. Go has no shlex, so the
test carries `shlexSplit`, a port.

That port is now load-bearing: if it splits differently from Python, the bridge
compares the engine's answer to a DIFFERENT question than the probe asked, and a
green result would be measuring nothing. Beat 642's lesson — "my oracle is a mirror"
— applies to the splitter exactly as it does to the matcher.

So the port is measured against the real thing, not eyeballed.

WHAT THIS CHECKS
    For every row in argvfixture.ROWS: shlex.split(cmd) in Python vs shlexSplit in Go.
    Any disagreement is reported as (row, python argv, go argv).

HOW
    The Go test `TestShlexSplitParity` reads expected argv from a generated fixture
    file and asserts equality. This script writes that file and exits non-zero if the
    file is stale, so the gate catches drift on either side.

USAGE
    python3 scripts/shlexparity.py            # regenerate the parity fixture
    python3 scripts/shlexparity.py --check    # exit 1 if stale

PACK PRESENCE (beat 779, measured)
    `packpresent_test.py` caught this script passing with `rules/carbonato.yaml` deleted, because it
    reads argvfixture.py and the fixture and never touches the pack. A gate entry that stays green
    when the product is gone is not a check, it is a green light. So this script now asserts the
    pack exists and parses to a non-empty rule list before doing anything else -- the same premise
    the other fixture probes rest on.
"""
import ast
import json
import os
import shlex
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROWS = os.path.join(ROOT, "scripts", "argvfixture.py")
OUT = os.path.join(ROOT, "internal", "rules", "testdata", "shlexparity.json")


PACK = os.path.join(ROOT, "rules", "carbonato.yaml")


def pack_premise():
    """Fail loudly if the product is not there to be judged. See PACK PRESENCE above."""
    if not os.path.exists(PACK):
        print("FAIL shlexparity: the pack %s is absent -- this check has nothing to judge"
              " and must not report success" % os.path.relpath(PACK, ROOT))
        return False
    text = open(PACK, encoding="utf-8").read()
    if not text.strip():
        print("FAIL shlexparity: the pack %s is empty" % os.path.relpath(PACK, ROOT))
        return False
    n = sum(1 for line in text.splitlines() if line.lstrip().startswith("- id:"))
    if n == 0:
        print("FAIL shlexparity: the pack parses to zero rules")
        return False
    print("ok   the pack is present (%d rules) before judging argv rows" % n)
    return True


def main():
    if not pack_premise():
        return 1
    rows = None
    tree = ast.parse(open(ROWS, encoding="utf-8").read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "ROWS":
            rows = ast.literal_eval(node.value)
    if rows is None:
        raise SystemExit("shlexparity: could not find ROWS in %s" % ROWS)

    cases = []
    for glob, cmd, want, cat in rows:
        cases.append({"cmd": cmd, "argv": shlex.split(cmd)})

    rendered = json.dumps({"cases": cases}, indent=1, ensure_ascii=False) + "\n"
    check = "--check" in sys.argv
    if os.path.exists(OUT):
        current = open(OUT, encoding="utf-8").read()
    else:
        current = None

    if current == rendered:
        print("ok   shlex parity fixture matches %d argv rows from %s"
              % (len(cases), os.path.basename(ROWS)))
        return 0
    if check:
        print("FAIL shlex parity fixture is STALE — run: python3 scripts/shlexparity.py")
        return 1
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, "w", encoding="utf-8").write(rendered)
    print("shlexparity: wrote %s (%d cases)" % (OUT, len(cases)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

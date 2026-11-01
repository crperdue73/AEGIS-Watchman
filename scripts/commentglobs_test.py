#!/usr/bin/env python3
"""commentglobs_test.py — the detector must catch a planted stale claim and NOT fire on look-alikes.

This tool was wrong three times before it was right:
  1. grepped the pack FILE, so a glob quoted inside a YAML comment ("REMOVED beat 614: '*:/host*'")
     read as shipped -- a removed glob, documented as removed, made a stale claim look true.
  2. flagged its own explanatory prose as a claim.
  3. over-tightened the keyword filter until it found ZERO claims -- silencing the real defect.

Each bug was a version of the thing it hunts: a checker that only understands one spelling. So this
test plants a stale claim in a scratch tree and asserts it is found, and plants four look-alikes and
asserts they are not. If the filter is ever retuned, one of these five must break.

  M0  a planted comment claiming a glob the pack lacks -> rc 1, names it
  M1  a planted comment claiming a glob the pack HAS    -> rc 0
  M2  a YAML comment quoting a removed glob does NOT satisfy a claim  (bug 1)
  M3  a collector-style filepath.Match comment is not judged         (bug 2, dialect)
  M4  a struct-field "e.g." example is not a claim                   (bug 3, over-fire)
  M5  the real tree is CLEAN -- Defect 4 (docker.go's "*:/" claim) was fixed at beat 629. This case is
      repointed, not deleted: it now asserts the shipped tree stays clean, so the defect cannot come
      back silently. (It was originally "docker.go's *:/ is still caught" and correctly went red the
      moment the comment was fixed -- that red was the proof the fix landed.)
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable
fails = 0

PACK = """rules:
  - id: test.rule
    match:
      detail_glob_any:
        binds:
          # REMOVED: "*:/host*" fired too broadly. This removed glob must NOT count as shipped.
          - "/:/"
"""


def check(name, cond, detail=""):
    global fails
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}  {detail}")
        fails += 1


def build(tmp, go_body):
    os.makedirs(os.path.join(tmp, "scripts"))
    os.makedirs(os.path.join(tmp, "rules"))
    os.makedirs(os.path.join(tmp, "internal", "x"))
    shutil.copy(os.path.join(HERE, "commentglobs.py"), os.path.join(tmp, "scripts", "commentglobs.py"))
    open(os.path.join(tmp, "rules", "carbonato.yaml"), "w").write(PACK)
    open(os.path.join(tmp, "internal", "x", "a.go"), "w").write(go_body)


def run(tmp):
    p = subprocess.run([PY, os.path.join(tmp, "scripts", "commentglobs.py")],
                       cwd=tmp, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def case(go_body):
    tmp = tempfile.mkdtemp()
    try:
        build(tmp, go_body)
        return run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print("commentglobs_test:")

    rc, out = case('package x\n// rules glob on "*/no/such/*" to catch it.\nvar A = 1\n')
    check("M0 a planted stale claim is caught", rc == 1 and '"/no/such/*"' or rc == 1, out[-300:])
    check("M0 and it names the missing glob", '"*/no/such/*"' in out, out[-300:])

    rc, out = case('package x\n// rules glob on "/:/" for the host root.\nvar A = 1\n')
    check("M1 a real shipped glob passes", rc == 0, out[-300:])

    # Bug 1: a glob inside a YAML *comment* was read as shipped. The planted pack quotes "*:/host*"
    # in a comment. A Go comment claiming exactly that glob must still be STALE.
    rc, out = case('package x\n// rules glob on "*:/host*" to catch the escape.\nvar A = 1\n')
    check("M2 a glob quoted only in a YAML comment does not count as shipped",
          rc == 1 and '"*:/host*"' in out, out[-400:])

    # Bug 2: collector uses filepath.Match -- a different dialect, not a pack claim.
    rc, out = case('package x\n// globMatch uses filepath.Match; "skills/**/*.md" works.\nvar A = 1\n')
    check("M3 a filepath.Match dialect comment is not judged", rc == 0, out[-300:])

    # Bug 3: a struct-field doc example illustrates syntax.
    rc, out = case('package x\n// ArgvAny fires on any element (e.g. "*.ssh*", "*id_rsa*").\nvar A = 1\n')
    check("M4 an e.g. field example is not a claim", rc == 0, out[-300:])

    rc, out = run(ROOT)
    check("M5 the real shipped tree has no stale glob claims", rc == 0, out[-400:])
    check("M5 and *:/ is no longer claimed after the Defect 4 fix", '"*:/"' not in out, out[-400:])

    print("\ncommentglobs_test: " + ("discriminates in every direction" if not fails else f"{fails} FAILED"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

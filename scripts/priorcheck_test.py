#!/usr/bin/env python3
"""priorcheck_test.py — priorcheck must be able to say "nothing found", and must mark withdrawals.

The whole value of this tool is that a quiet result is trustworthy. A search that always returns hits, or
one that hides a retracted document in the middle of live ones, is worse than no search at all.

  M0  the shipped archive: a known subject finds FINDING-2026-10-23-argv-subject.md
  M1  a nonsense term returns NO hits and says so (rc 0 -- a quiet answer is a valid answer)
  M2  a retracted finding is marked [WITHDRAWN], not presented as live
  M3  --status filters (open vs retracted disagree on the same subject)
  M4  a docs dir with no findings does not crash the search
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


def check(name, cond, detail=""):
    global fails
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}  {detail}")
        fails += 1


def run(cwd, *args):
    p = subprocess.run([PY, os.path.join(cwd, "scripts", "priorcheck.py"), *args],
                       cwd=cwd, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def main():
    print("priorcheck_test:")

    rc, out = run(ROOT, "argv-subject")
    check("M0 finds the Oct 23 argv-subject finding", "FINDING-2026-10-23-argv-subject.md" in out, out[:400])
    check("M0 exits 0 on a hit", rc == 0, f"rc={rc}")

    rc, out = run(ROOT, "zzz-no-such-subject-zzz")
    check("M1 a nonsense term reports no hits", "nothing in" in out and "mention" in out, out[:300])
    check("M1 and still exits 0 (quiet is a valid answer)", rc == 0, f"rc={rc}")

    rc, out = run(ROOT, "2987f-retracted-x")  # matches nothing; use --list instead
    rc, out = run(ROOT, "--list")
    check("M2 the retracted finding is marked WITHDRAWN in --list", "[WITHDRAWN]" in out or "WITHDRAWN" in out,
          out[:300])
    check("M2 the retraction title is present", "RETRACTED" in out, "")

    # NB: the retracted finding is about the poll clock and does not contain the word "crontab" at all --
    # my first version of this case searched "crontab" and got a correct empty answer, which I nearly read
    # as a filter bug. Pick a subject that is genuinely in both populations.
    rc, out_open = run(ROOT, "--status", "open", "crontab")
    rc, out_ret = run(ROOT, "--status", "retract", "correlation")
    check("M3 --status open finds crontab findings", "mention 'crontab'" in out_open, out_open[:200])
    check("M3 --status retract finds the withdrawn one only",
          "RETRACTED" in out_ret and "argv-subject" not in out_ret, out_ret[:400])
    check("M3 and marks it WITHDRAWN in the hit list", "[WITHDRAWN]" in out_ret, out_ret[:400])
    check("M3 the two filters disagree", out_open != out_ret, "")

    tmp = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(tmp, "scripts"))
        os.makedirs(os.path.join(tmp, "docs"))
        shutil.copy(os.path.join(HERE, "priorcheck.py"), os.path.join(tmp, "scripts", "priorcheck.py"))
        rc, out = run(tmp, "crontab")
        check("M4 an empty docs/ does not crash", rc == 0 and "nothing in 0 finding" in out, f"rc={rc} {out[:200]}")

        # and the hard failure it should have: no docs/ at all
        shutil.rmtree(os.path.join(tmp, "docs"))
        rc, out = run(tmp, "crontab")
        check("M4 no docs/ at all exits 2 and says so", rc == 2 and "no docs/" in out, f"rc={rc} {out[:200]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\npriorcheck_test: " + ("all modes discriminate" if not fails else f"{fails} FAILED"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

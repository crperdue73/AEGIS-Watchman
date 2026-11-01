#!/usr/bin/env python3
"""redundant_test.sh -- mutation-test redundant_test.py itself.

A guard that cannot fail is worse than no guard. This script proves `redundant_test.py` actually
detects redundancy, by injecting a redundant glob into a COPY of the pack and asserting the suite goes
red. The real pack is never touched.

It also pins the suite's LIMIT, honestly, because that limit cost me a beat.

WHAT THIS SCRIPT PROVES, AND WHAT IT PROVES IT CANNOT DO
  M1  add a glob wholly subsumed by "/:/"            -> suite MUST fail   (redundancy is caught)
  M2  add a redundant cron.rules glob               -> suite MUST fail   (not a one-rule fluke)
  M3  reinstate the beat-614 glob "*:/host*"        -> suite MUST pass   (DOCUMENTED BLIND SPOT)

M3 is the uncomfortable one. "*:/host*" is the glob I deleted in beat 614, and this suite does NOT
flag it. That is not a bug to fix -- it is the correct behaviour of the question this suite asks.
"*:/host*" has a unique witness (`/x:/host`), so it is not redundant. It was OVER-BROAD: its unique
witnesses were all false positives. Catching that needs labelled true/false positives per rule, which
this repo does not have.

So M3 asserts the blind spot out loud. If someone later writes a precision suite and wires it in here,
M3 will fail and tell them to update this file -- which is the point. A documented blind spot that
fails when it closes is worth more than a suite that silently pretends to be complete.
"""

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PACK = os.path.join(ROOT, "rules", "carbonato.yaml")

# The binds block as it ships, and two redundant variants to inject.
BINDS_NOW = '''          - "/:/"
          - "*/var/run/docker.sock*"'''
BINDS_SUBSUMED = '''          - "/:/"
          - "*/:/"
          - "*/var/run/docker.sock*"'''
BINDS_BEAT614 = '''          - "*:/host*"
          - "/:/"
          - "*/var/run/docker.sock*"'''

fails = []


def run_suite(pack_text, label):
    """Run redundant_test.py against a temporary pack; return True if it passed."""
    tmp = tempfile.mkdtemp(prefix="redundant-mut-")
    try:
        rdir = os.path.join(tmp, "rules")
        os.makedirs(rdir)
        with open(os.path.join(rdir, "carbonato.yaml"), "w", encoding="utf-8") as fh:
            fh.write(pack_text)
        sdir = os.path.join(tmp, "scripts")
        os.makedirs(sdir)
        for name in ("redundant_test.py", "neargo.py"):
            shutil.copy(os.path.join(HERE, name), sdir)
        r = subprocess.run([sys.executable, os.path.join(sdir, "redundant_test.py")],
                           capture_output=True, text=True, timeout=120)
        return r.returncode == 0, (r.stdout + r.stderr)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check(label, cond, detail=""):
    if cond:
        print(f"ok    {label}")
    else:
        fails.append(label)
        print(f"FAIL  {label}" + (f"  [{detail}]" if detail else ""))


def main():
    original = open(PACK, encoding="utf-8").read()

    # 0. Baseline: the shipped pack must pass.
    ok, out = run_suite(original, "baseline")
    check("the shipped pack has no passenger globs", ok, out[-300:])

    # 1. M1: a glob wholly subsumed by "/:/" must be caught.
    inject = original.replace(BINDS_NOW, BINDS_SUBSUMED, 1)
    check("  ...the mutation actually changed the pack", inject != original)
    ok, out = run_suite(inject, "M1")
    check("M1 a glob subsumed by '/:/' makes the suite FAIL", not ok, out[-300:])
    check("  ...and names the redundant glob", "'*/:/'" in out, out[-300:])

    # 2. M2: redundancy in a different rule, to prove it is not a binds-only fluke.
    cron_now = '        - "*/etc/cron.d/*"'
    cron_dup = '        - "*/etc/cron.d/*"\n        - "*/etc/cron.d*"' 
    if cron_now in original:
        inject2 = original.replace(cron_now, cron_dup, 1)
        check("  ...M2 mutation changed the pack", inject2 != original)
        ok, out = run_suite(inject2, "M2")
        check("M2 a redundant cron glob also makes the suite FAIL", not ok, out[-300:])
    else:
        inject2 = original.replace(cron_now, cron_dup, 1)
        check("  ...M2 mutation changed the pack", inject2 != original)
        ok, out = run_suite(inject2, "M2")
        check("M2 a redundant cron glob also makes the suite FAIL", not ok, out[-300:])

    # 3. M3: the documented blind spot. The beat-614 glob is over-broad, NOT redundant.
    inject3 = original.replace(BINDS_NOW, BINDS_BEAT614, 1)
    check("  ...M3 mutation changed the pack", inject3 != original)
    ok, out = run_suite(inject3, "M3")
    check("M3 BLIND SPOT pinned: '*:/host*' is over-broad, so this suite passes it", ok, out[-300:])

    print()
    if fails:
        print(f"redundant_mut.py: {len(fails)} FAIL")
        for f in fails:
            print("  -", f)
        print("\nIf M3 failed, a precision suite may now exist. Update this file's BLIND SPOT note.")
        return 1
    print("redundant_mut.py: all ok -- redundancy is caught, and the blind spot is pinned")
    return 0


if __name__ == "__main__":
    sys.exit(main())

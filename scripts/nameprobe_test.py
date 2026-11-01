#!/usr/bin/env python3
"""nameprobe_test.py — proves the census guard can actually FAIL.

A census that pins a count is only worth having if it notices the count changeing. Three mutations
on temp copies (the real pack is never touched), plus a baseline:

  M0 shipped pack           -> rc 0, census 37
  M1 one more verb-glob     -> rc 1, "census grew"
  M2 a brand new rule in this class -> rc 1, names the rule
  M3 a needle with shell syntax in it -> NOT counted (a `|` needle cannot be tripped by `man X`)

M3 matters: the first version of the sweep counted 39 globs because malformed probes (`man | sh`)
inflated it. The corpus being wrong is the failure mode this suite guards against, so it is tested.

Exit 0 all three hold · 1 otherwise.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PACK = os.path.join(ROOT, "rules", "carbonato.yaml")

ANCHOR = '      argv_any:\n        - "*.ssh/id_*"\n        - "*id_rsa*"'

fails = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}  {detail}")
        fails.append(name)


def run_with(src_transform, must_change=True):
    tmp = tempfile.mkdtemp(dir=ROOT, prefix=".npn-")
    try:
        shutil.copytree(os.path.join(ROOT, "scripts"), os.path.join(tmp, "scripts"))
        shutil.copytree(os.path.join(ROOT, "rules"), os.path.join(tmp, "rules"))
        src = open(PACK, encoding="utf-8").read()
        new = src_transform(src)
        if must_change and new == src:
            raise AssertionError("mutation did not apply — anchor text is stale, fix this suite")
        open(os.path.join(tmp, "rules", "carbonato.yaml"), "w", encoding="utf-8").write(new)
        r = subprocess.run([sys.executable, "scripts/nameprobe.py"], cwd=tmp,
                           capture_output=True, text=True, timeout=180)
        return r.returncode, r.stdout + r.stderr
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print("nameprobe_test:")

    rc, out = run_with(lambda s: s, must_change=False)
    check("M0 shipped pack is green", rc == 0, out.strip().splitlines()[-1:] and out.strip().splitlines()[-1])
    check("M0 baseline reads 37", "37 globs across 7 rules" in out, out[:200])

    rc, out = run_with(lambda s: s.replace(ANCHOR, ANCHOR + '\n        - "*gpg-connect-agent*"'))
    check("M1 one more verb-glob FAILS the census", rc == 1, f"rc={rc}")
    check("M1 says the census grew", "census grew" in out, out[:200])

    rc, out = run_with(lambda s: s.replace(
        "rules:",
        "rules:\n  - id: carbonato.test.brandnew\n    title: x\n    severity: low\n"
        "    match:\n      argv_any:\n        - \"*nc -l*\"\n      type: process.exec", 1))
    check("M2 a new rule in this class FAILS", rc == 1, f"rc={rc}")
    check("M2 names the new rule", "carbonato.test.brandnew" in out, out[:200])

    rc, out = run_with(lambda s: s.replace(ANCHOR, ANCHOR + '\n        - "*a|b*"'))
    check("M3 a shell-syntax needle is NOT counted", rc == 0 and "37 globs across 7 rules" in out,
          out[:200])

    check("the real pack is untouched", open(PACK, encoding="utf-8").read().count("gpg-connect-agent") == 0)

    print(f"\nnameprobe_test: {'ok, all three mutations caught' if not fails else str(len(fails)) + ' FAILED'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""packpresent_test.py -- prove packpresent.py can actually fail.

A guard that cannot fail is worse than no guard. This drives the guard against mutated packs and
asserts each mutation is caught. The real pack is never modified: mutations are written to temp dirs.
"""

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

fails = []


def check(label, cond, detail=""):
    if cond:
        print(f"ok    {label}")
    else:
        fails.append(label)
        print(f"FAIL  {label}" + (f"  [{detail}]" if detail else ""))


def run_in(tmp):
    r = subprocess.run([sys.executable, os.path.join(tmp, "scripts", "packpresent.py")],
                       capture_output=True, text=True, timeout=180, cwd=tmp)
    return r.returncode, (r.stdout + r.stderr)


def make_tree(mutate):
    """Copy scripts/ + rules/ into a temp dir, apply mutate(pack_path), return (tmp, pack_text)."""
    tmp = tempfile.mkdtemp(prefix="packpresent-mut-")
    shutil.copytree(os.path.join(HERE, "..", "scripts"), os.path.join(tmp, "scripts"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(os.path.join(HERE, "..", "rules"), os.path.join(tmp, "rules"))
    pack = os.path.join(tmp, "rules", "carbonato.yaml")
    mutate(pack, tmp)
    return tmp


M1 = lambda p, t: os.remove(p)                                        # pack deleted
M2 = lambda p, t: open(p, "w").close()                                # pack emptied
M3 = lambda p, t: open(p, "w").write("rules: []\n")                   # parses, no rules
M4 = lambda p, t: open(p, "w").write("rules:\n  - id: x\n    match: {}\n")  # rule with no globs


def main():
    original = open(os.path.join(HERE, "..", "rules", "carbonato.yaml"), encoding="utf-8").read()

    # 0. baseline
    tmp = make_tree(lambda p, t: None)
    try:
        rc, out = run_in(tmp)
        check("the real pack passes the guard", rc == 0, out[-400:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    for label, mut, want in (
        ("M1 the pack file is DELETED", M1, "exists"),
        ("M2 the pack file is EMPTY", M2, "not empty"),
        ("M3 the pack parses to zero rules", M3, "parses to a non-empty rule list"),
        ("M4 a rule yields no globs", M4, "parses to a non-empty rule list"),
    ):
        tmp = make_tree(mut)
        try:
            rc, out = run_in(tmp)
            check(f"{label} -> guard FAILS", rc != 0, out[-300:])
            check(f"  ...and names the reason ({want!r})", want in out, out[-300:])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # The pack must be intact after all that.
    check("the real pack is untouched",
          open(os.path.join(HERE, "..", "rules", "carbonato.yaml"), encoding="utf-8").read() == original)

    print()
    if fails:
        print(f"packpresent_test: {len(fails)} FAIL")
        return 1
    print("packpresent_test: all ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())

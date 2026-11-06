#!/usr/bin/env python3
"""scanblind — publish-scan reports PASS when it cannot run at all.

THE FIND
--------
`scripts/publish-scan.sh` is the guard that is supposed to stop a credential, an operator
home path, or private infrastructure from reaching the public repo. It printed:

    publish-scan: PASS — no tracked file and no unpushed commit carries a credential, ...

I planted `docs/zz-leakcanary.txt` containing a `cpat_`-shaped token. It printed PASS again.

THE MECHANISM (from source, comments stripped)
-----------------------------------------------
    report() {
      ...
      out="$(git grep -n -I -E -- "$pat" 2>/dev/null)" || true
      [[ -z "$out" ]] && return 0
      ...

`git grep` searches the INDEX / tracked tree. This tree has no `.git` -- it was destroyed in
the 2026-10-08 migration (MEMORY.md, WORKLOG). So:

    git grep -> fatal: not a git repository -> nonzero
    `|| true`                  -> the failure is DISCARDED
    2>/dev/null                -> even the FATAL MESSAGE is discarded
    out=""                     -> empty
    [[ -z "$out" ]] && return 0 -> "no hits"

Every one of the four checks then returns 0, `fatal` stays 0, and the script prints PASS with
a sentence asserting a scan that never happened.

WHY THIS IS THE WORST INSTANCE FOUND SO FAR
-------------------------------------------
Beats 662-677 found screens that look calm when the system is broken: an unbounded alert, a
detector with no sink, a health probe that cannot say no, a store that reports accepted, a
read error that stops delivery, a monitor that cannot tell unreadable from present. Those
were all inside the product. **This one is inside the tool that decides whether the product
is safe to publish**, it has been dark since 2026-10-08, and `make publish-scan` is listed in
MEMORY.md as a green gate. A green gate that cannot fail is worse than no gate, because it
consumes the attention that would have caught the thing it was supposed to catch.

Note the comment at the top of report() -- "When non-empty, report() greps these commit
objects instead of the worktree" -- the design is correct and deliberate. The defect is not
a misunderstanding of git; it is the `|| true` / `2>/dev/null` pair that makes "the tool
could not run" indistinguishable from "the tool ran and found nothing".

CLAIM / NOT CLAIMED
-------------------
CLAIMED (measured on this host): publish-scan.sh prints PASS while a `cpat_`-shaped token
    sits in the tracked tree; `git grep` fails with "not a git repository"; `|| true` plus
    `2>/dev/null` reduce that failure to an empty string; `report()` returns 0 on empty.
CLAIMED (from source): all four checks route through report(); `fatal` is only set from
    inside report(); nothing else can set it.
NOT CLAIMED: that publish-scan was ALWAYS broken -- it was green while a `.git` existed, and
    MEMORY.md records it green pre-migration. This is a regression ON the migration, not a
    defect that was always there. **NOT CLAIMED: that a real credential is in the public
    repo today** -- I did not audit the remote and could not; what I measured is that the
    local guard cannot answer the question.
"""

import sys
import pathlib
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def strip_comments(src):
    src = re.sub(r"^\s*#.*$", "", src, flags=re.M)
    return src


def main():
    root = pathlib.Path(__file__).resolve().parents[1]
    fails = 0

    def check(name, ok):
        nonlocal fails
        print(("ok   " if ok else "FAIL ") + name)
        if not ok:
            fails += 1

    try:
        raw = (root / "scripts/publish-scan.sh").read_text()
    except Exception as e:  # noqa: BLE001
        print(f"FAIL could not read input ({e}); a missing input is not a pass")
        return 1
    code = strip_comments(raw)

    rep = code[code.index("report() {"):]
    rep = rep[:rep.index("\n}\n")]

    print("-- the mechanism: a failure and an empty result must NOT look identical --")
    # BEAT 836: CLAUSE INVERTED, ON PURPOSE.
    #
    # Every check below used to assert the DEFECT: "report() discards git grep's
    # exit status", "this tree has NO .git", "publish-scan reports PASS with a
    # credential-shaped token in the tree". That was right when the bug was open
    # -- it was a measurement of a live hole. The hole is now closed (publish-scan
    # propagates git grep's status and exits 2 UNAVAILABLE), so the file has to
    # turn around with it. Asserting the bug forever would make it a test that can
    # only ever be green on a broken product, which is the same species of mistake
    # in the opposite direction.
    #
    # Same count (9), every clause re-pointed at the FIXED behaviour.
    check("report() runs `git grep`", "git grep" in rep)
    check("report() CAPTURES git grep's exit status (no blanket `|| true`)",
          "rc=$?" in rep and rep.count("|| true") < rep.count("git grep"))
    check("report() treats >1 as NO ANSWER and records it",
          "unavailable=1" in rep and "$rc" in rep)
    check("report() still returns 0 on an EMPTY result (1 = no matches, a real answer)",
          '[[ -z "$out" ]] && return 0' in rep)
    check("`fatal` can only be set from inside report() (no independent failure path)",
          code.count("fatal=1") == 1)

    print("\n-- measured: an unreadable tree must produce NO VERDICT, not a pass --")
    has_git = (root / ".git").exists()
    print(f"   {root}/.git exists: {has_git}")
    check("scan declines to VALIDATE a tree it cannot read (exit 2, never PASS)",
          "UNAVAILABLE" in code and "exit 2" in code)

    # Ask git a question whose answer is a CANARY, not a keyword that legitimately appears in
    # the scanner's own source. The first version grepped for "cpat_" -- which is a pattern
    # STRING inside publish-scan.sh, so a healthy repo returns 0 matches found (rc 0) and the
    # check went red against a perfectly good tree. Beat 836: I had written the assertion for
    # the .git-less authored tree, then run it in a real repo and called the repo broken.
    r = subprocess.run(["git", "grep", "-n", "-I", "-E", "--",
                        "ZZ_NO_SUCH_TOKEN_IN_ANY_TREE_ZZ"],
                       cwd=str(root), capture_output=True, text=True)
    print(f"   git grep rc={r.returncode} stderr={r.stderr.strip()[:70]!r}")
    check("git grep in THIS tree distinguishes 1 (no match: a real answer) from >1 (cannot read)",
          r.returncode in (0, 1),
          )
    if r.returncode > 1:
        print(f"   note  git grep returned {r.returncode} -- the tree is unreadable, so "
              f"every scan of it is meaningless")

    print("\n-- and a planted token must not survive the scan, in a tree it CAN read --")
    # The canary goes into a throwaway git repo, because the authored tree has no
    # .git and the correct verdict there is UNAVAILABLE (pinned above). This half
    # asks the other question: when the scan CAN look, does it still catch a leak?
    work = Path(tempfile.mkdtemp())
    try:
        subprocess.run(["git", "init", "-q"], cwd=str(work), check=True)
        (work / "leak.txt").write_text("token = ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8\n")
        subprocess.run(["git", "add", "leak.txt"], cwd=str(work), check=True)
        shutil.copytree(root / "scripts", work / "scripts")
        # Run from a DIFFERENT cwd on purpose: beat 836 found the script resolved
        # its repo as $here/.. , so a copy of it living in a clone scanned the
        # authored tree instead. Pinning "the verdict follows the tree, not the
        # caller's cwd" is the check that keeps that bug from coming back.
        p = subprocess.run(["bash", str(work / "scripts/publish-scan.sh")], cwd="/tmp",
                           capture_output=True, text=True)
        print(f"   planted leak in a real repo; publish-scan rc={p.returncode}")
        check("publish-scan FAILS on a credential-shaped token it can actually see",
              p.returncode != 0 and "PASS" not in p.stdout)
        check("the verdict follows the TREE, not the caller's cwd (beat 836)",
              "leak.txt" in p.stdout)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    if fails:
        print(f"\nSCANBLIND RED: {fails}")
        return 1

    print("\n=> publish-scan.sh now separates the three states it used to merge: clean (0),")
    print("   findings (1), and COULD NOT LOOK (2). The last one is the whole point -- it was")
    print("   printing PASS for it from 2026-10-08 until beat 836.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

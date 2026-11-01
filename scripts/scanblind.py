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
import re
import subprocess


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

    print("-- the mechanism: a failure and an empty result look identical --")
    check("report() runs `git grep`", "git grep" in rep)
    check("report() discards git grep's EXIT STATUS everywhere (`|| true`)",
          rep.count("git grep") >= 1 and rep.count("|| true") >= rep.count("git grep"))
    check("report() discards git grep's STDERR (`2>/dev/null`)", "2>/dev/null" in rep)
    check("report() returns 0 on an EMPTY result (which a failed grep also produces)",
          '[[ -z "$out" ]] && return 0' in rep)
    check("`fatal` can only be set from inside report() (no independent failure path)",
          code.count("fatal=1") == 1)

    print("\n-- measured in a SCRATCH COPY with .git removed, so git grep cannot work --")
    # The 2026-10-08 migration destroyed this working tree's .git, and that is where the
    # blind spot was found. But a fresh clone always HAS a .git, so asserting its absence
    # would make this check true only on a broken box -- it would fail everywhere the
    # product is actually used. Instead, reconstruct the condition deliberately: copy the
    # scanner into a scratch dir with no .git and show it reports PASS on a question it
    # could not answer. Same mechanism, and it now holds in a clone.
    import shutil, tempfile
    scratch = pathlib.Path(tempfile.mkdtemp(prefix="scanblind-"))
    try:
        (scratch / "scripts").mkdir()
        shutil.copy(root / "scripts/publish-scan.sh", scratch / "scripts/publish-scan.sh")
        has_git = (scratch / ".git").exists()
        print(f"   {scratch}/.git exists: {has_git}")
        check("the scratch tree has NO .git (the 2026-10-08 migration condition, rebuilt)",
              not has_git)

        r = subprocess.run(["git", "grep", "-n", "-I", "-E", "--", "cpat_"],
                           cwd=str(scratch), capture_output=True, text=True)
        print(f"   git grep rc={r.returncode} stderr={r.stderr.strip()[:70]!r}")
        check("git grep exits NONZERO without a .git", r.returncode != 0)
        check("its output is EMPTY, exactly what 'no hits' looks like", r.stdout == "")

        print("\n-- and the scanner prints PASS while a token sits in the tree --")
        canary = scratch / "zz-leakcanary.txt"
        try:
            canary.write_text("token = cpat_deadbeefdeadbeefdeadbeefdeadbeef\n")
            p2 = subprocess.run(["bash", "scripts/publish-scan.sh"], cwd=str(scratch),
                                capture_output=True, text=True)
            print(f"   planted {canary.name}; publish-scan rc={p2.returncode}")
            check("publish-scan reports PASS with a credential SHAPED token in the tree",
                  "PASS" in p2.stdout and p2.returncode == 0)
        finally:
            if canary.exists():
                canary.unlink()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    if fails:
        print(f"\nSCANBLIND RED: {fails}")
        return 1

    print("\n=> publish-scan.sh cannot distinguish 'no findings' from 'could not run', and in")
    print("   a tree without .git it is the second: `git grep` fails, `|| true` and")
    print("   2>/dev/null discard the failure, and `[[ -z $out ]] && return 0` reports clean.")
    print("   It was dark on this box from 2026-10-08 until .git was restored -- and MEMORY.md")
    print("   NOT claimed: that a real credential is public today -- I did not and cannot")
    print("   audit the remote from here. Claimed: the local guard cannot answer that question.")
    print("   Fix, Dad's call -- three parts, and the FIRST is the one that matters:")
    print("   (1) distinguish CANNOT-RUN from NO-FINDINGS: capture git grep's status and")
    print("       BANNER-EXIT (rc 2, 'scanner unavailable', fatal) when it is nonzero and the")
    print("       reason is not 'no matches'; drop `2>/dev/null` or tee stderr into the");
    print("       report. A scanner must never print PASS on an unanswered question.")
    print("   (2) add `publish-scan.sh` to suitecheck.sh's SELFHARNESS -- it is a pack-exempt")
    print("       guard that currently NOTHING runs, which is how it stayed dark.")
    print("   (3) restore the .git (the reconciliation already routed to Dad via Jade, hb202)")
    print("       -- otherwise (1) will correctly banner-fail on every heartbeat, which is the")
    print("       honest state but a noisy one.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

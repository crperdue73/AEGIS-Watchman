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

    # This suite used to assert the DEFECT: that report() discarded git grep's exit
    # status and stderr, so "could not run" and "found nothing" were one output. Beat 739
    # fixed the scanner. A canary that still asserts the bug is a canary that fails when
    # the bug is fixed -- so it is inverted: it now REQUIRES the fix and goes red if
    # anyone reintroduces `|| true` / `2>/dev/null` around the grep.
    print("-- the mechanism: a failure and an empty result must NOT look identical --")
    check("report() runs `git grep`", "git grep" in rep)
    # Scope this to the grep LINE, not the whole function: `|| true` legitimately
    # appears on the allowlist filter (`printf ... | grep -vE "$allow"`), where a
    # no-matches exit is expected and harmless. The defect was `|| true` on the
    # git-grep invocation itself, so that is precisely what is asserted here.
    grep_lines = [ln for ln in rep.splitlines() if "git grep" in ln and not ln.strip().startswith("#")]
    check("report() has a git grep invocation to inspect", len(grep_lines) > 0)
    check("report() CAPTURES git grep's exit status (no `|| true` on the grep line)",
          all("|| true" not in ln for ln in grep_lines))
    check("report() does NOT discard git grep's STDERR (`2>/dev/null` gone)",
          "2>/dev/null" not in rep)
    check("report() treats exit status >1 (cannot-run) as FATAL, not as no-hits",
          "rc > 1" in rep and "could not run the scan" in rep)
    check("a FATAL cannot-run sets the failure flag",
          rep.split("rc > 1")[1].split("fi")[0].count("fatal=1") == 1)
    check("--warn refuses to print a clean verdict for a scan it could not run",
          "INCONCLUSIVE" in code)

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

        print("\n-- and the scanner REFUSES to certify a tree it could not read --")
        canary = scratch / "zz-leakcanary.txt"
        try:
            canary.write_text("token = cpat_deadbeefdeadbeefdeadbeefdeadbeef\n")
            p2 = subprocess.run(["bash", "scripts/publish-scan.sh"], cwd=str(scratch),
                                capture_output=True, text=True)
            print(f"   planted {canary.name}; publish-scan rc={p2.returncode}")
            check("publish-scan FAILS on a tree it cannot read (no false PASS)",
                  p2.returncode != 0 and "PASS — no tracked file" not in p2.stdout)
            check("it names the cannot-run reason instead of staying silent",
                  "could not run" in p2.stdout)
            p3 = subprocess.run(["bash", "scripts/publish-scan.sh", "--warn"],
                                cwd=str(scratch), capture_output=True, text=True)
            check("--warn stays inconclusive rather than claiming clean",
                  "INCONCLUSIVE" in p3.stdout and "PASS — no tracked file" not in p3.stdout)
            canary.unlink()
            p4 = subprocess.run(["bash", "scripts/publish-scan.sh"], cwd=str(scratch),
                                capture_output=True, text=True)
            check("with the canary gone it STILL refuses (the verdict tracks the scan, "
                  "not the canary)", p4.returncode != 0)
        finally:
            if canary.exists():
                canary.unlink()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    if fails:
        print(f"\nSCANBLIND RED: {fails}")
        return 1

    print("\n=> publish-scan.sh now distinguishes 'no findings' from 'could not run'.")
    print("   git grep's status is captured; >1 (not a repo, bad rev, grep failure) is FATAL")
    print("   with the reason printed, and --warn reports INCONCLUSIVE instead of PASS.")
    print("   History: the blind spot was found on 2026-10-08 (this tree lost its .git in the")
    print("   migration) and FIXED in beat 739 -- the same beat that found the tree would not")
    print("   build. Before the fix, the scanner printed PASS over a planted token on any")
    print("   tree it could not read, and the release gate that should have caught it was")
    print("   green for three weeks. NOT claimed: that a real credential was public -- I did")
    print("   not and cannot audit the remote from here. Claimed: the local guard now answers")
    print("   the question it was asked, and fails loudly when it cannot.")
    print("   Remaining work, in priority order:")
    print("   (1) add `publish-scan.sh` to suitecheck.sh's SELFHARNESS -- it is a pack-exempt")
    print("       guard, and a guard nothing runs is how it stayed dark for three weeks.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

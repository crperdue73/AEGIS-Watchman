#!/usr/bin/env python3
"""guardreach_test.py — the reach linter must name every offender, and must not use scope as a mute.

Two things this pins, both learned the hard way at beat 630:

  1. `listing()` — the offender lists were `[:8]` / `[:3]`, so the guard printed "26 without" and named
     eight. A count nobody can act on. Eighteen guards were reported defective and permanently
     unnameable. The linter now names all of them, truncating only the display and saying how many
     it held back.

  2. `is_test_file()` — 56 of 127 scored files were `*_test.py` / `*_test.sh`. Scoring a test for not
     printing a denominator is a category error, and it drowned the real 17. But a scope filter is a
     mute: exclude too much and the guard goes green by seeing less. So the test asserts the filter
     admits real guards AND that narrowing did not silence a known offender that is not a test.

  M0  is_test_file admits a guard and rejects *_test.py / *_test.sh
  M1  listing() names every item when the list fits
  M2  listing() names all when it does not -- the count and the names agree (the beat-630 bug)
  M3  a real non-test offender (blindcheck.sh) is still in scope and still flagged
  M4  the excluded test files are exactly the 56 that exist on disk (no over-broad glob)
  M5  shell output is READ -- `echo` counts as output, so a shell guard can have a denominator
  M6  and a shell guard that states no reach still reads as having none (the reader is not credulous)
  M7  is_library admits the 3 real libraries and nothing else on disk
  M8  and refuses a script that only LOOKS like one: an unimported module with no __main__ is not
      a library, it is an unrun guard and must still be scored
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import guardreach as g  # noqa: E402

fails = 0


def check(name, cond, detail=""):
    global fails
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}  {detail}")
        fails += 1


def main():
    print("guardreach_test:")

    check("M0 is_test_file admits a guard", not g.is_test_file("packcheck.py"), "guard misread as test")
    check("M0 is_test_file rejects *_test.py", g.is_test_file("packcheck_test.py"), "test read as guard")
    check("M0 is_test_file rejects *_test.sh", g.is_test_file("fpprobe_test.sh"), "test read as guard")
    check("M0 is_test_file admits .sh guard", not g.is_test_file("license-check.sh"), "guard misread")

    out = subprocess.run([sys.executable, os.path.join(HERE, "guardreach.py")],
                         capture_output=True, text=True).stdout

    # M1/M2: listing() has to keep count and names consistent. The failure mode being guarded against
    # is a header saying N while the bracket names fewer, with no marker that it was cut.
    m = re.search(r"(\d+) without: \[(.*?)\](?:\s+REST\((\d+)\): \[(.*?)\])?\n", out, re.S)
    check("M2 the R1 header is present", bool(m), out[:200])
    if m:
        n = int(m.group(1))
        head = [x.strip() for x in m.group(2).split(",") if x.strip()]
        rest_n = int(m.group(3)) if m.group(3) else 0
        rest = [x.strip() for x in (m.group(4) or "").split(",") if x.strip()]
        check("M2 count and named offenders agree exactly (no silent truncation)",
              len(head) + len(rest) == n and rest_n == len(rest),
              f"header {n}, head {len(head)}, REST marker {rest_n}, rest listed {len(rest)}")
        check("M2 the held-back count matches the held-back names", rest_n == len(rest),
              f"marker says {rest_n}, listed {len(rest)}")

    check("M3 blindcheck.sh is scored and still flagged (scope did not mute a real guard)",
          "blindcheck.sh" in out, "a non-test offender disappeared from the report")

    # M4: scope must be exactly the test files that exist -- not a ``*test*`` glob that also eats
    # e.g. scripts/typecheck.py or scripts/packpresent.py.
    on_disk = [f for f in os.listdir(HERE)
               if f.endswith((".py", ".sh")) and os.path.isfile(os.path.join(HERE, f))]
    excluded = {f for f in on_disk if g.is_test_file(f)}
    real_tests = {f for f in on_disk if f.endswith("_test.py") or f.endswith("_test.sh")}
    check("M4 the excluded set is exactly the _test files on disk", excluded == real_tests,
          f"extra={sorted(excluded - real_tests)[:5]} missing={sorted(real_tests - excluded)[:5]}")
    for tricky in ("typecheck.py", "packpresent.py", "guardtriage.py", "typecheck.py"):
        check(f"M4 {tricky} is not excluded as a test", not g.is_test_file(tricky), "false exclusion")

    # M5/M6: before beat 631 the reader only knew `print(`, so all 17 shell guards were scored blind.
    # Reading echo/printf is what let clone-check.sh be scored fairly. Both directions are pinned: a
    # shell guard WITH a reach must be credited, and one WITHOUT must still read as unreached.
    d_with, _, _, _, _ = g.judge('echo "checked $n of $total files"\n')
    check("M5 shell echo output is read (a shell guard can have a denominator)", d_with,
          "echo output is invisible to the linter -- shell guards scored blind")
    d_without, _, _, bad_without, _ = g.judge('echo "all clean, nothing leaked"\n')
    check("M6 a shell guard with no reach still reads as having none",
          (not d_without) and bool(bad_without), f"denom={d_without} bad={bad_without}")
    d_printf, _, _, _, _ = g.judge('printf "%d rules scanned\\n" "$n"\n')
    check("M5 printf is read too", d_printf, "printf output is invisible")

    # M7/M8: libraries are excluded from R1 because they return values instead of making claims. The
    # exclusion is decided by evidence (imported + no __main__ + no output + no exit), never by name,
    # because a name-based filter is the very bug this linter hunts. The pair is pinned in both
    # directions: the three real ones must be admitted, and a plausible impostor must be refused.
    libs = {f for f in on_disk if not g.is_test_file(f) and g.is_library(f)}
    check("M7 is_library admits exactly the imported, silent modules",
          libs == {"globmatch.py", "neargo.py", "tokruns.py"}, f"got {sorted(libs)}")
    for lib in ("globmatch.py", "neargo.py", "tokruns.py"):
        check(f"M7 {lib} is excluded as a library", g.is_library(lib), "library scored as a guard")
    check("M8 a test file is never treated as a library", not g.is_library("packpresent_test.py"),
          "test misclassified as library")
    check("M8 a missing file is not silently called a library", not g.is_library("no_such_file.py"),
          "missing file returned True -- would let a typo exempt a guard")

    # M8b: the checks above pass for three files that satisfy EVERY criterion at once, so they cannot
    # tell WHICH criterion is doing the work -- a mutation of any single one still passes them. (Found
    # at beat 632: dropping the __main__ test, and then the importer test, both left the suite green.)
    # Plant a scratch module in scripts/ that satisfies every condition, then break them ONE AT A TIME.
    scratch = os.path.join(HERE, "zz_scratchlib.py")
    importer = os.path.join(HERE, "zz_scratchuser.py")
    try:
        with open(scratch, "w") as fh:
            fh.write("def v():\n    return 1\n")
        with open(importer, "w") as fh:
            fh.write("import zz_scratchlib\n")
        check("M8b baseline: imported + silent + no __main__ IS a library",
              g.is_library("zz_scratchlib.py"), "baseline not recognised as library")

        # break 1: give it a __main__ -- it can now be run, so it is a guard again
        with open(scratch, "w") as fh:
            fh.write('def v():\n    return 1\n\nif __name__ == "__main__":\n    print(v())\n')
        check("M8b a __main__ block alone disqualifies library status",
              not g.is_library("zz_scratchlib.py"), "a runnable module was exempted as a library")

        # break 2: it prints, so even without a __main__ it makes a claim
        with open(scratch, "w") as fh:
            fh.write("def v():\n    print('checked 3 files')\n")
        check("M8b output alone disqualifies library status",
              not g.is_library("zz_scratchlib.py"), "a printing module was exempted as a library")

        # break 3: it can set an exit status
        with open(scratch, "w") as fh:
            fh.write("import sys\ndef v():\n    sys.exit(1)\n")
        check("M8b an exit status alone disqualifies library status",
              not g.is_library("zz_scratchlib.py"), "an exiting module was exempted as a library")

        # break 4: nothing imports it -- an unrun guard, not a library
        os.remove(importer)
        with open(scratch, "w") as fh:
            fh.write("def v():\n    return 1\n")
        check("M8b an unimported silent module is NOT a library (it is an unrun guard)",
              not g.is_library("zz_scratchlib.py"), "unimported module exempted as a library")
    finally:
        for f in (scratch, importer):
            if os.path.exists(f):
                os.remove(f)

    print()
    print(f"guardreach_test: {len(excluded)} test files excluded, scope honest"
          if not fails else f"guardreach_test: {fails} FAIL")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

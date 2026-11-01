#!/usr/bin/env python3
"""matcherblind — does any of this actually depend on the matcher, or does it pass on any answer?

THE QUESTION (beat 682)
-----------------------
Everything in this tree judges the matcher. `globmatch.match_one` is my transcription of `engine.go:251`,
and 77 path rows, 33 endpoint rows, 100 argv rows and 16 `*_test.py` suites are all certified against it.
Beat 642 named the risk: that oracle is a *mirror*, written by the same hands that wrote the rows.

What nobody had asked: **is any of it falsifiable at all?** If every check I own would pass against a
matcher that always said `True`, or always `False`, then the green means "the matcher is deterministic,"
not "the matcher is right" -- and I would never be able to tell the difference from the output.

THE METHOD
----------
Swap the matcher for a degenerate one and re-run the suite in a SUBPROCESS (never in-process: importing a
`*_test.py` executes it -- measured at 51 `ok` lines during pathglob_test's import -- so in-process
patching would fold other suites' verdicts into this one's count). Then read the exit code:
`rc == 0` means the suite was happy with a matcher that cannot be wrong in the way it exists to catch.

    BLIND_ALWAYS_TRUE   every glob matches everything
    BLIND_ALWAYS_FALSE  no glob ever matches anything

A suite that survives both is not a test of the matcher. It may still be a fine test of something else
(parsing, coverage, pins) -- so this file reports the split rather than declaring everything broken.

THE LIMIT, STATED UP FRONT
--------------------------
`rc == 0` under a blind matcher is *evidence* of vacuity, not proof: a suite could assert `want == False`
on a row whose truth value the engine also derives without matching. The reverse also holds -- a suite
that goes red under a blind matcher may be going red on its first check and never reaching the ones that
matter. This measures survival, which is the question I can answer without a rule-semantics opinion.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Which suites route their assertions through globmatch's matcher. Established by import census, not by
# filename: these are the files that import match_one / path_match / any_glob_matches / substring_needle.
SUITES = [
    "pathfixture.py",
    "endpointfixture.py",
    "argvfixture.py",
    "windowfixture.py",
    "pathglob_test.py",
    "suffixprobe_test.py",
]

# Declared survivors. A suite listed here is expected NOT to depend on the matcher's answers; the reason
# must be about the suite, not about my patience. Empty by default -- justify each one by measuring.
#
# Measured 2026-10-30 (beat 682): all six DIED under both blind matchers. Nothing to declare.
KNOWN_MATCHER_FREE = {}

BLINDS = {
    "ALWAYS_TRUE": "    return True\n",
    "ALWAYS_FALSE": "    return False\n",
}


def run_suite(name, blind_name, body):
    """Run `name` under a blind matcher. Returns (rc or None, tail of output).

    WHY THIS COPIES THE TREE INSTEAD OF SETTING PYTHONPATH (first attempt, beat 682, was a lie):
    every fixture suite does `sys.path.insert(0, HERE)` and `import globmatch` -- and HERE is the real
    scripts dir. A `PYTHONPATH` entry is *appended* to `sys.path`, not prepended, so the suites imported
    the REAL matcher and all six "survived a matcher that always says no" while never seeing it. Measured
    before believing: `PYTHONPATH=<blind> python3 -c "import globmatch"` reports the real file.
    **A mutation harness that never applies the mutation reports every subject as robust.**

    So: hardlink-mirror the scripts dir, OVERWRITE the mirror's globmatch.py in place (a real file, at the
    front of sys.path by the suite's own doing), and run the mirror copy. Same isolation rule as
    packpresent.py/packvanished.py -- never touch the real tree.
    """
    root = os.path.dirname(HERE)
    tmp = tempfile.mkdtemp(prefix=".matcherblind-mirror-", dir=root)
    try:
        mirror = os.path.join(tmp, "scripts")
        ignore = shutil.ignore_patterns("__pycache__", ".matcherblind-mirror-*", ".packpresent-mirror-*",
                                        ".packvanished-mirror-*", ".git", "out")
        shutil.copytree(HERE, mirror, copy_function=os.link, ignore=ignore)
        # Unlink the hardlink FIRST, then write -- otherwise the "copy" would be the real file and this
        # guard would corrupt the very matcher it is testing.
        real = os.path.join(mirror, "globmatch.py")
        os.unlink(real)
        with open(os.path.join(HERE, "globmatch.py"), encoding="utf-8") as f:
            src = f.read()
        start = src.index("def match_one(")
        nxt = src.index("\ndef ", start + 1)
        with open(real, "w", encoding="utf-8") as f:
            f.write(src[:start] + "def match_one(glob, s):\n" + body + src[nxt:])

        # The mutation must be observable in the mirror before any verdict is drawn from it.
        probe = subprocess.run([sys.executable, "-c",
                                "import globmatch;print(globmatch.match_one('*x*','x'))"],
                               capture_output=True, text=True, cwd=mirror)
        want = "True" if body.strip() == "return True" else "False"
        if probe.stdout.strip() != want:
            return None, f"HARNESS BROKEN -- mirror match_one said {probe.stdout.strip()!r}, wanted {want}"

        p = subprocess.run([sys.executable, os.path.join(mirror, name)],
                           capture_output=True, text=True, cwd=root, timeout=300)
        tail = (p.stdout + p.stderr).strip().splitlines()
        return p.returncode, tail[-1] if tail else "(no output)"
    except subprocess.TimeoutExpired:
        return None, "TIMEOUT -- hung under a blind matcher"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    fails = []
    print(f"{len(SUITES)} suites that judge the matcher, run against two matchers that cannot be right\n")
    print(f"    {'suite':24s} {'ALWAYS_TRUE':>16s} {'ALWAYS_FALSE':>16s}   verdict")
    print("    " + "-" * 78)

    blind_in_both = []
    for name in SUITES:
        if not os.path.exists(os.path.join(HERE, name)):
            print(f"    {name:24s} {'MISSING':>16s} {'':>16s}   FAIL -- suite is gone")
            fails.append(f"{name} missing")
            continue
        rcs = {}
        for blind, body in BLINDS.items():
            rc, _tail = run_suite(name, blind, body)
            rcs[blind] = rc
        survived = [b for b, rc in rcs.items() if rc == 0]
        cells = " ".join(f"{'rc=' + str(rcs[b]):>16s}" for b in BLINDS)
        if name in KNOWN_MATCHER_FREE:
            verdict = "declared matcher-free"
        elif len(survived) == 2:
            verdict = "VACUOUS -- passes against any answer"
            blind_in_both.append(name)
            fails.append(f"{name} survived both blind matchers")
        elif len(survived) == 1:
            verdict = f"ONE-SIDED -- only detects {survived[0]} blindness"
        else:
            verdict = "detects both"
        # A per-suite `ok`/`FAIL` line at column 0, because suitecheck scores a suite by counting these
        # and would otherwise classify a fully-green run as "died before printing a single verdict".
        # Caught by the gate, not by me: the first wired-in run reported `no-verdicts-printed` while the
        # standalone run was green -- exactly beat 646's CRASH-vs-FAIL distinction doing its job.
        if verdict.startswith(("VACUOUS", "FAIL")):
            print(f"FAIL {name} {verdict}")
        else:
            print(f"ok   {name} {verdict}")
        print(f"    {name:24s} {cells}   {verdict}")

    print()
    # A suite that dies under only ONE blind matcher is only half a test: it can catch a matcher that is
    # stuck on but not one stuck off (or vice versa). Worth naming even though it is not a failure.
    one_sided = []
    for name in SUITES:
        if name in KNOWN_MATCHER_FREE or name in blind_in_both or not os.path.exists(os.path.join(HERE, name)):
            continue
        rcs = {b: run_suite(name, b, body)[0] for b, body in BLINDS.items()}
        if len([rc for rc in rcs.values() if rc == 0]) == 1:
            one_sided.append((name, [b for b, rc in rcs.items() if rc == 0][0]))

    for name, blind in one_sided:
        print(f"note {name} only detects the {blind} blindness (half a test, recorded not fixed)")

    # The declared list must stay honest: an exemption for a suite that now DOES depend on the matcher is
    # a stale exemption, and stale exemptions are how a guard quietly stops guarding.
    stale = [n for n in KNOWN_MATCHER_FREE if n not in SUITES]
    if stale:
        fails.append(f"stale matcher-free exemptions: {stale}")
        print(f"FAIL stale matcher-free exemptions (suite not in the list): {stale}")

    print()
    if fails:
        print(f"matcherblind: {len(fails)} FAIL")
        for f in fails:
            print("  - " + f)
        return 1
    print(f"matcherblind: {len(SUITES)} suites, {len(SUITES) - len(KNOWN_MATCHER_FREE)} proven matcher-dependent, "
          f"{len(one_sided)} one-sided (each named above)")
    print("  limit: this proves each suite CAN go red when the matcher lies. It cannot prove the matcher")
    print("         is right -- that is beat 642's mirror problem, and only the Go toolchain changes it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

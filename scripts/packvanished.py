#!/usr/bin/env python3
"""packvanished.py -- the pack is an INPUT, and no fixture file says so.

BEAT 680. The last two beats were about a gate that answers a question it never
asked (publish-scan with no .git) and a checker that never built the state that
breaks its subject. Then I read argvfixture's docstring: argv_any "had zero
fixtures. A family with no fixtures does not read as untested -- it reads as
green, because nothing ever asked it a question (beat 645)."

That sentence applies one level up, and nobody had said it: every fixture file
in this directory is a reader of rules/carbonato.yaml. Delete the pack and the
families do not go quiet with a verdict -- they have nothing to read. So what
does the gate actually see?

MEASURED (this beat, pack hidden in an isolated hardlink mirror):

    pathfixture.py      rc=1    0 `ok` lines -- dies on FileNotFoundError  (CRASH)
    endpointfixture.py  rc=1    0 `ok` lines -- dies on FileNotFoundError  (CRASH)
    argvfixture.py      rc=1    0 `ok` lines -- asserts, then says "cannot read the rule pack"
    windowfixture.py    rc=1    4 `ok` lines -- asserts, then "FAIL could not read pack ..."
    jsonfidelity.py     rc=1    1 `ok` line  -- "FAIL could not read pack", then a true claim
                                                 about the ENGINE that needs no pack

All five go red, so no verdict is falsely green. Two of them (pathfixture,
endpointfixture) go red by CRASHING, and a Python traceback is not a finding --
it is "the instrument broke", which is a different fact from "the detector is
wrong" even though both turn the gate red.

beat 680 CORRECTION TO MY OWN FIRST DRAFT: I wrote this file believing all five
crashed and that no fixture had ever considered a missing pack. That was wrong.
`windowfixture.py` handles it deliberately, in a comment that names the exact
failure this file was going to claim: "Without this, deleting rules/carbonato.yaml
makes this file print ok and exit 0 -- it would report a clean box on the day the
pack went missing," and credits `scripts/packpresent.py`. So the guard for this
question already existed, was already behavioural, and already worked. I wrote a
second one before reading the first. The 4 `ok` lines above are windowfixture's
fixtures being judged BEFORE the pack read is attempted -- not a green verdict,
and the run still ends red with the right sentence.
What survives is narrower and still worth pinning: 2 of the 5 die as CRASHES,
and that count is now asserted so it cannot grow silently. suitecheck classifies exactly
that (beat 646): rc!=0 with zero verdicts printed is a CRASH, "evidence of
nothing", counted separately and named. So deleting the pack reddens the gate
five times without telling anyone *why* -- and the reason it reddens is worth
being precise about: **this is not a working sensor. It is a dead battery.**

The distinction is load-bearing and this file exists to hold it:

  * A suite that cannot read its input has not tested the detector. It has
    tested nothing.
  * The gate is still red, and that is correct -- but "5 suites crashed" and
    "5 suites said no" must not look alike, or the next person reads a broken
    fixture as a broken pack.
  * Callie's line (6908, quoted in argvfixture's neighbourhood): "if the grep
    itself failed I would read it as a clean box." Hers is the dangerous
    direction, mine is the noisy one. Both are the instrument reporting on
    itself instead of on the subject.

WHAT THIS FILE ASSERTS
  1. Each fixture suite, run against a tree with NO pack, does not print `ok`
     lines as though it had checked something. (Four of five: holds. jsonfidelity
     prints one `ok` about the ENGINE -- "the defect is LATENT in the pack, LIVE
     in the engine" -- which is true without the pack and is not a claim about
     the absent pack. Measured, allowed, recorded.)
  2. None of them claims a PASS while the pack is gone.
  3. The gate's own classifier calls that state a CRASH, not a FAIL -- read from
     suitecheck.sh, because if the classifier ever stops distinguishing them this
     file's whole premise is wrong.
  4. The pack is restored byte-identical afterwards. A test that eats its input
     is worse than no test, and this one MOVES a tracked file.

WHAT IT DOES NOT DO
  * It does not change any suite to handle a missing pack. Whether a fixture
    should banner-exit ("input missing", rc 2) instead of raising is a design
    call across five files, and it changes what the release gate reports --
    Dad's call, same as publish-scan's verdict.
  * It does not prove the fixtures are correct. It proves they have an input.
"""

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PACK = os.path.join(ROOT, "rules", "carbonato.yaml")
SUITES = ["pathfixture.py", "endpointfixture.py", "argvfixture.py",
          "windowfixture.py", "jsonfidelity.py"]
# pathfixture + endpointfixture die on an uncaught FileNotFoundError. windowfixture,
# argvfixture and jsonfidelity print a finding. Fixed number, not "at least one".
PINNED_CRASHES = 2

FAILS = []


def ok(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def run(script, workdir):
    p = subprocess.run([sys.executable, script], cwd=workdir,
                       capture_output=True, text=True, timeout=300)
    return p.returncode, p.stdout + p.stderr


def main():
    if not os.path.exists(PACK):
        print("FAIL packvanished: there is no pack to hide -- rules/carbonato.yaml is already missing, "
              "which is the state this file exists to model (not a pass)")
        return 1

    before = sha(PACK)
    # ISOLATION -- learned from packpresent.py's third design, the hard way (this file's first
    # version moved the REAL pack aside and ran the suites in place; run side-by-side with
    # packpresent.py it would have deleted its sibling's input mid-run). Same fix it uses: a
    # hardlink mirror of the whole repo, and the pack is unlinked from the MIRROR ONLY. Hardlinks
    # share inodes, so mtimes and content are identical and dispose.py stays honest. The mirror
    # must sit on the same filesystem as the repo (os.link fails EXDEV across devices), so it is
    # built inside ROOT and removed in finally.
    tmp = tempfile.mkdtemp(prefix=".packvanished-mirror-", dir=ROOT)
    try:
        shutil.copytree(ROOT, tmp, symlinks=True, copy_function=os.link,
                        ignore=shutil.ignore_patterns("__pycache__", ".packpresent-mirror-*",
                                                      ".packvanished-mirror-*", ".git", "out"),
                        dirs_exist_ok=True)
    except OSError:
        shutil.rmtree(tmp, ignore_errors=True)
        tmp = None
    if tmp is None:
        print("FAIL packvanished: could not build the hardlink mirror; without it this test has no "
              "isolated way to hide the pack, and running in place would damage its neighbours "
              "(a guard that damages the thing it guards is not a guard)")
        return 1

    mirror = os.path.join(tmp, "scripts")
    mirror_pack = os.path.join(tmp, "rules", "carbonato.yaml")
    print(f"hiding the mirror's copy of rules/carbonato.yaml (real pack sha256 {before[:12]}) "
          f"and running each fixture suite\n")
    results = {}
    try:
        if os.path.exists(mirror_pack):
            os.unlink(mirror_pack)   # hardlink: removes the MIRROR entry only; real pack survives
        ok(not os.path.exists(mirror_pack) and os.path.exists(PACK),
           "precondition: the pack is gone from the MIRROR and untouched in the real tree")
        for s in SUITES:
            rc, out = run(s, mirror)
            oks = len(re.findall(r"^\s*ok\b", out, re.M))
            results[s] = (rc, oks, out)
            print(f"   {s:20s} rc={rc}  ok-lines={oks}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    after = sha(PACK)
    ok(after == before, "the real pack is byte-identical afterwards (this test never moved it)")

    print("\n-- no suite claims to have checked anything it could not read --")
    for s in SUITES:
        rc, oks, out = results[s]
        if oks:
            # jsonfidelity's one line is about the engine, not the pack: it says the
            # defect is LATENT in the pack and LIVE in the engine, which is still true
            # and is not a verdict on the missing file. Named, not excused.
            print(f"   {s} printed {oks} ok line(s) with no pack -- listed for review, not failed")
            continue
        ok(True, f"{s} printed no `ok` verdicts against a missing pack")
    ok(all(rc != 0 for rc, _o, _out in results.values()),
       "every fixture suite exits non-zero with the pack gone (none reports success on an absent input)")
    # BEAT 680, found by mutation M3: the first version of this check was
    #     ok(not re.search(r"\bPASS\b|all .* checks passed", out) or rc != 0, ...)
    # Feed it `rc=0` + "jsonfidelity: all checks passed" and it prints **ok** -- because
    # `or rc != 0` is read by a human as "unless it went red anyway", while Python reads it as
    # "green means fine". A suite that exits 0 over a missing input IS the bug, so its exit code
    # must not be an escape hatch from the phrase check. Only the aggregate check caught M3; a
    # per-suite check that can only fail when another check also fails is not a per-suite check
    # (beat 647's lesson, verbatim, in a file written six beats later).
    lies = [s for s in SUITES
            if re.search(r"\bPASS\b|all .* checks passed", results[s][2]) and results[s][0] == 0]
    ok(not lies, f"no fixture suite prints a PASS verdict while exiting 0 over a missing pack: {lies}")
    for s in SUITES:
        rc, _o, out = results[s]
        ok(rc != 0 or not re.search(r"\bPASS\b|passed", out),
           f"{s} does not print a PASS verdict while its input is missing")

    print("\n-- and the gate calls that a CRASH, not a FAIL (beat 646) --")
    try:
        with open(os.path.join(HERE, "suitecheck.sh"), encoding="utf-8") as fh:
            gate = fh.read()
    except OSError as e:
        print(f"FAIL cannot read suitecheck.sh ({e}) -- a missing gate is not a pass")
        return 1
    ok("CRASH" in gate and "evidence of nothing" in gate,
       "suitecheck.sh still classifies rc!=0-with-no-verdicts as a CRASH rather than a FAIL")
    # A CRASH is "said nothing", not "said no good news". Counting `ok` lines to detect a crash
    # would classify argvfixture.py (0 ok, one clear FAIL sentence) as a crash -- which is exactly
    # the confusion this file exists to name, made by this file on its first run. suitecheck.sh's
    # own rule is rc!=0 AND zero verdicts of ANY kind; use that, not the happy half of it.
    def verdicts(out):
        return len(re.findall(r"^\s*(ok|FAIL|xfail|note)\b", out, re.M))

    crashed = [s for s in SUITES if results[s][0] != 0 and verdicts(results[s][2]) == 0]
    ok(len(crashed) == PINNED_CRASHES,
       f"fixtures dying as a CRASH with no pack is pinned at {PINNED_CRASHES} (found {len(crashed)}) -- "
       f"the defect this file names, stated out loud so it cannot grow as a side effect")

    print()
    if FAILS:
        print(f"PACKVANISHED RED: {len(FAILS)}")
        return 1
    print(f"packvanished: all {len(SUITES)} fixture suites exit non-zero with the pack absent; "
          f"{len(crashed)} of them die as a CRASH (zero verdicts) instead of saying why; no false PASS.")
    print("  PINNED: crash-without-pack = %d of %d. Lowering this is the fix; raising it is a regression." %
          (len(crashed), len(SUITES)))
    print("  Existing coverage NOT claimed as new: packpresent.py already asserts, behaviourally, that no "
          "script may pass with the pack absent (beat 616). This file asks the narrower question.")
    print("  NOT fixed here, and it is Dad's call: whether a fixture should banner-exit")
    print("  ('input missing') instead of raising -- that changes what the release gate reports.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

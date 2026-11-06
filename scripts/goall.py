#!/usr/bin/env python3
"""goall.py -- run `go test ./...` and report it, with declared-known-red packages named explicitly.

WHY THIS EXISTS (beat 785)
--------------------------
For many beats I ended a heartbeat with "gate green: N suites, M checks." Every one of those numbers
came from `scripts/suitecheck.sh`, which is the PYTHON gate and does not run a single Go test. When I
finally ran `go test ./...` this beat, `internal/central` had been FAILING -- not crashing, failing,
with a real assertion -- and the number I had been quoting said nothing about it.

That is the same defect this project hunts, one level up: a status line that measures one thing and
gets read as a statement about another. `suitecheck` was honest; my summary sentence was not.

THE CONTRACT
------------
* Every package must pass, UNLESS it is declared below with a reason and a clearing condition.
* A declared-red package that is now GREEN is printed as a note so the declaration cannot rot.
* A declared-red package whose failing test set CHANGES (different test names) fails: a pin that
  drifts is a pin nobody is watching.
* An undeclared failing package fails by name.

A declaration here is not an excuse to leave a defect open -- it is the honest alternative to a gate
that is permanently red (beat 780: those get ignored, then deleted). The finding that motivates each
one is named, so the defect has an address.

WHAT THIS DOES NOT CLAIM
------------------------
* `go test ./...` is not `make preflight`. No -race, no fuzz, no container, no fresh-clone build.
* A green run does not mean the assertions are worth anything -- that is what the mutation work and
  coverfloor.py are for.

Exit codes: 0 = no undeclared failures, 1 = otherwise.
"""

import datetime
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK = os.path.join(ROOT, "rules", "carbonato.yaml")

# Packages allowed to fail, with the defect they name and the condition that would clear them.
# `tests` is the EXACT set of failing test names -- if that set changes, this fails, because a
# silenced defect that grew or shrank is a defect nobody is reading.
DECLARED_RED = {
    # 2026-11-06 (beat 836): internal/central's declaration was REMOVED, not
    # muted. It held TestHTTPActionsTotalDropIsNotAccepted red against finding
    # 2026-11-02g; Dad picked option 1 + 3, the handler now answers 400 on a
    # total drop, the spool keeps the batch, and the test passes for the reason
    # it was written. The finding doc is CLOSED -- so the entry goes, and this
    # dict being empty is the correct state until something else is proven red.
    #
    # The comment stays as a tombstone because the alternative -- leaving an
    # empty `{}` with no explanation -- reads identically to "this list was never
    # maintained". goall's own "declared red but now PASSES" note is what caught
    # it; that note is the mechanism, and it worked.
}


def go_env():
    env = dict(os.environ)
    env["PATH"] = "/usr/local/go/bin:" + env.get("PATH", "")
    env.setdefault("CGO_ENABLED", "1")
    return env


def parse(output):
    """Return {package: [failing test names]} for every package that failed.

    ORDER MATTERS AND I GOT IT WRONG FIRST. `go test` prints `--- FAIL: TestX` per test and only
    then `FAIL <import/path>`. My first version attached `--- FAIL` lines to a package it had
    already seen, so it recorded an empty test list and then declared the pin "moved". Measured,
    not reasoned: the first run said `failing set is now []` for a package whose test plainly
    failed. Buffer the names and attribute them when the FAIL line arrives.
    """
    failing, pending = {}, []
    for line in output.splitlines():
        m = re.match(r"^--- FAIL: (\S+)", line)
        if m:
            pending.append(m.group(1))
            continue
        m = re.match(r"^FAIL\s+(\S+)\s+(.*)$", line)
        if m and not m.group(1).startswith("["):
            # A BUILD FAILURE IS NOT A TEST FAILURE and must never be parsed as one.
            # `go test` prints `FAIL <pkg> [build failed]` -- my first regex demanded a duration
            # token and swallowed "[build" as if it were one, so a package that does not COMPILE
            # arrived here as "0 tests failed". For a declared-red package that is the exact
            # string used for "the pin drifted", i.e. a broken tree and a moved pin looked alike.
            if "[build failed]" not in m.group(2) and "[setup failed]" not in m.group(2):
                failing[m.group(1)] = sorted(pending)
            pending = []
    return failing


def build_failures(output):
    """Packages that did not compile. Kept separate from test failures on purpose."""
    return sorted({m.group(1) for m in
                   re.finditer(r"^FAIL\s+(\S+)\s+\[build failed\]", output, re.M)})


HISTORY = os.path.join(ROOT, ".goall-history.jsonl")


def record(failing, broken, bad, out):
    """Append this run's verdict to a history file. Callie's 7593: a pass after a failure erases
    the record of the failure if nothing writes the failure down. A green run today is green; it
    is NOT evidence that yesterday's red never happened. This file is that evidence, and it is
    append-only so the account cannot be quietly tidied."""
    entry = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
             "failing": failing, "build_broken": broken, "undeclared": sorted(bad),
             # EVERY package seen this run, so `prior_flaps` can tell "ran and passed" from
             # "was not in this run at all". Without this the first flap probe reported that
             # `central` -- which has only ever failed -- was flapping, because it counted the
             # package's ABSENCE from a run as a pass. An unasked package is not a passing package;
             # that is the same defect as counting "desk empty" from "I only opened the inbox"."""
             "ran": sorted({l.split()[1] for l in out.splitlines()
                            if l.startswith("ok  \t") or l.startswith("ok\t")})}
    try:
        with open(HISTORY, "a") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError as exc:
        print("note  goall: could not append to %s (%s) -- this run is not recorded, so a later green "
              "would erase it" % (HISTORY, exc))


def prior_flaps():
    """Runs where the same package both failed and passed recently. Printed as a note, never a
    pass/fail: a flap is information about the instrument, not about the product."""
    if not os.path.exists(HISTORY):
        return []
    states = {}
    try:
        with open(HISTORY) as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                for pkg in e.get("ran", []):
                    states.setdefault(pkg, set()).add("pass")
                for pkg, tests in e.get("failing", {}).items():
                    states.setdefault(pkg, set()).add("fail:%s" % ",".join(tests or ["<none>"]))
    except OSError:
        return []
    return sorted(p for p, s in states.items() if "pass" in s and any(x.startswith("fail") for x in s))



def main():
    check = "--check" in sys.argv[1:]

    # PREMISE: go test with the product deleted is a green light, not a check.
    if not os.path.isdir(os.path.join(ROOT, "internal")) or not os.path.exists(PACK):
        print("FAIL goall: internal/ or rules/carbonato.yaml is absent -- nothing to test")
        return 1

    proc = subprocess.run(["go", "test", "./...", "-count=1"],
                          cwd=ROOT, env=go_env(), capture_output=True, text=True)
    out = proc.stdout + proc.stderr

    failing = parse(out)
    broken = build_failures(out)
    bad = []

    # A tree that does not compile has no test verdict at all. Checked FIRST and by package name,
    # because the alternative is that an uncompilable package arrives as "declared red, 0 tests
    # failed" -- the exact string this script uses for "the pin drifted". Measured at beat 786: the
    # old phrase-match (`"no Go files" in out and "undefined:" in out`) only caught one flavour of
    # compile error and missed every other.
    for pkg in broken:
        print("FAIL goall: %s DID NOT COMPILE -- there is no test verdict for a package that will not "
              "build, and this is not the same thing as a test failing" % pkg)
        bad.append(pkg)

    for pkg in sorted(failing):
        tests = failing[pkg]
        decl = DECLARED_RED.get(pkg)
        if decl is None:
            print("FAIL goall: %s failed (%d test(s): %s) and is NOT declared"
                  % (pkg, len(tests), ", ".join(tests) or "package-level"))
            bad.append(pkg)
            continue
        if tests != sorted(decl["tests"]):
            print("FAIL goall: %s is declared red for %s, but the failing set is now %s -- a pin that "
                  "moved is a pin nobody is watching"
                  % (pkg, sorted(decl["tests"]), tests))
            bad.append(pkg)
            continue
        print("declared-red %s -- %s\n             finding: %s\n             next: %s"
              % (pkg.split("/")[-1], decl["why"], decl["finding"], decl["next"]))

    for pkg in DECLARED_RED:
        if pkg not in failing:
            print("note  goall: %s is declared red but now PASSES -- if the defect is fixed, delete "
                  "its declaration and the pin can go" % pkg.split("/")[-1])

    record(failing, broken, bad, out)

    total = len(re.findall(r"^(?:ok|---)\s", out, re.M))
    for pkg in prior_flaps():
        print("note  goall: %s has BOTH failed and passed across recorded runs -- a flapping package "
              "is an instrument question before it is a product question" % pkg.split("/")[-1])

    print("ok   goall: `go test ./...` exited %d, %d package(s) failing, %d declared, %d undeclared"
          % (proc.returncode, len(failing), len(failing) - len(bad), len(bad)))

    if check and (bad or (proc.returncode != 0 and not failing)):
        # returncode != 0 with nothing parsed means a build/compile failure we did not understand.
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""coverfloor.py -- statement coverage with the one question percentage cannot answer.

WHY THIS EXISTS (beat 784)
--------------------------
`go test ./internal/rules -cover` reports 93.7%. For four beats I had been saying "engine tested"
and never once run the number behind it. When I did, the number was fine and the number was lying:

    normalizePathGlobs   0.0%      <- claimed "used by tests"; no caller anywhere
    NewEngineRules       0.0%      <- claimed "used by tests"; no caller anywhere

Total percentage is an AVERAGE, and an average cannot distinguish "everything lightly exercised"
from "almost everything exercised, and two functions never run at all." The second one is the shape
that ships. So this prints both, and fails on the second.

THE RULE
--------
* No function may sit at 0.0% unless it is declared below with a reason.
* The total must stay at or above FLOOR.

A declared-zero entry is a decision with a name attached, printed on every run. An undeclared zero
is a failure. That is the same contract as staleclaim.py's `pending=` -- the honesty is that the
line appears every time, not that the gate is red forever.

WHAT THIS DOES NOT CLAIM
------------------------
* Statement coverage is not behaviour coverage. A line can execute and assert nothing. This cannot
  tell those apart, and no coverage tool can.
* A high number is not a good test suite. It only rules out the specific failure of code that has
  never run.
* internal/rules is the only package with a FLOOR. Six other packages are measured per-function
  (beat 787) but not gated by a percentage, because none of them has been regression-pinned yet --
  a floor invented today is a floor with no history behind it.

Exit codes: 0 = floor met and every zero declared, 1 = otherwise (or a premise failed).
"""

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK = os.path.join(ROOT, "rules", "carbonato.yaml")
COVER = os.path.join(ROOT, ".coverfloor.out")

# Packages measured. internal/rules was the only one until beat 787; the rest were added after a
# cross-package run showed that `internal/schema` reads 0.0% ON ITS OWN and 100% when its callers are
# counted. That is the trap this list exists to document: a package grading only its own tests can
# report zero for code four other packages exercise on every run.
#
# `floor`: a regression tripwire set at today's measured number. `None` means "measure it, print it,
# do not fail on it" -- used where there is no history to justify a specific floor yet.
PACKAGES = {
    "./internal/rules/":       {"floor": 93.0},
    "./internal/central/":     {"floor": None},
    "./internal/collector/":   {"floor": None},
    "./internal/provenance/":  {"floor": None},
    "./internal/schema/":      {"floor": None},
    "./internal/telemetry/":   {"floor": None},
    "./internal/mtls/":        {"floor": None},
    "./internal/buildinfo/":   {"floor": None},
}

# Kept for the rules-package floor message and for backward-compatible reading.
FLOOR = PACKAGES["./internal/rules/"]["floor"]

# Functions allowed to sit at 0.0%, each with the reason it is allowed and the condition that would
# clear it. Adding a line here is a decision; adding one without a `why=` is a malformed entry.
DECLARED_ZERO = {
    # (name -> reason). Names are global across the measured packages; none collide today, and if
    # two packages ever declare the same helper this dict must become per-package.
    "NewEngineRules": "why=exported API with no in-tree caller; removal is the pack owner's call "
                      "next=cross-package-use-or-removal",
    "normalizePathGlobs": "why=unexported helper with no caller at all; removal is the pack owner's "
                          "call next=caller-or-removal",

    # --- found at beat 787, when this script was widened to eight packages with cross-package
    # measurement. Every one of these has a real caller; none is dead code. They are declared, not
    # fixed, because writing the test is the work and inventing a declaration is not.
    # RETIRED beat 794: NewDockerSource, NewDockerSourceFromCommand and Inspect all said
    # "next=inject-a-command-runner" and that is now done -- `newDockerCmd` is a package var,
    # `dockercmd_test.go` substitutes a recorder and asserts the ARGV each function chooses
    # (default events argv, configured Bin/EventsArgs, disabled builds nothing, inspect carries the
    # container id). Telemetry 84.8% -> 88.1%. The subprocess still does not run in CI and is not
    # claimed to: what is measured is the decision, which is the part this package owns.
    # NOTE (beat 788): the entry that used to live here said "Alert: ...no in-tree caller...".
    # That name was ambiguous -- go tool cover prints receiver-less names, so SIX methods called
    # Alert shared it, and the one actually at 0.0% is alert.go:149 = LogAlerter.Alert, which HAS a
    # caller (config.go:254). The description was simply untrue of the code it named. Keys are now
    # file:line:name so they cannot collide. Nothing declared here is dead code.
    # logalerter_test.go now covers LogAlerter.Alert, so it no longer needs a declaration at all.
    # RETIRED beat 789: this declaration said "no in-tree caller ... nothing asserts it". Both
    # halves are now false -- buildFleetView calls it (finding 2026-11-01n option 4) and
    # fleetorder_test.go asserts it from unordered input. The 0.0% is gone; no entry needed.
    "config.go:185:TLSEnabled": "why=config predicate; every test drives TLS-on or implicit TLS instead "
                  "next=assert-the-false-branch",
    "config.go:154:UidResolverFn": "why=type declaration with no runtime statement to cover -- go reports 0.0% for "
                     "an uninstantiated func type next=none-expected",
    "email.go:236:hostOf": "why=small helper reached only from an error path that tests do not provoke "
              "next=provoke-the-error",
    "eventstore.go:435:mkdirAll": "why=thin os.MkdirAll wrapper, error branch only next=inject-a-failing-path",
    "email.go:176:sendImplicitTLS": "why=TLS handshake against a real listener next=in-process-tls-fixture",
    "email.go:155:uidStr": "why=nil-pointer formatting helper on an unattributed actor next=assert-the-nil-case",
    "eventstore.go:431:unixToTime": "why=epoch conversion, no assertion anywhere next=assert-both-directions",
    "provenance.go:186:String": "why=(provenance.Trust) renders the verdict for logs; no caller in-tree yet "
              "next=use-in-event-detail-or-remove",
}


def go():
    env = dict(os.environ)
    env["PATH"] = "/usr/local/go/bin:" + env.get("PATH", "")
    env.setdefault("CGO_ENABLED", "1")
    return env


def measure(pkg):
    """Run one package's coverage. Returns (per_func_zero_names, total, err) -- err set means this
    package produced NO measurement and must not be read as a pass."""
    out = os.path.join(ROOT, ".coverfloor%s.out" % pkg.strip("./").replace("/", "-"))
    # CROSS-PACKAGE, MEASURED (beat 787). This used to be `go test <pkg>` -- that measures the
    # package against ONLY its own tests, so `internal/schema` reported 0.0% while four other
    # packages exercised it to 100%, and `provenance.SHA256Hex` (the hash the manifest compares
    # file integrity against) read 0.0% while `cmd/manifest` calls it on every run.
    # A zero from `go test <pkg>` is a statement about the package's own tests, not about the code.
    # With `-coverpkg` restricted to the package under measurement and `./...` as the runners, the
    # number is about the CODE. For internal/rules the two agree (93.7%), which is why the first
    # version looked right.
    proc = subprocess.run(["go", "test", "./...", "-coverpkg=" + pkg,
                           "-coverprofile=" + out, "-count=1"],
                          cwd=ROOT, env=go(), capture_output=True, text=True)
    if proc.returncode != 0 and not os.path.exists(out):
        # No profile at all = the runner could not run. That is not a coverage result.
        return [], None, (proc.stdout + proc.stderr).strip()[-300:]
    # NOTE: `go test ./...` exits non-zero because internal/central has a DECLARED-RED pin (see
    # goall.py). go still writes the profile for every package that did run, so coverage is
    # measurable even on a red tree -- and reporting "not measured" here would have made the whole
    # audit hostage to a known, declared failure. Ownership: goall.py judges test outcomes,
    # coverfloor judges coverage. They must not both claim the same verdict.

    func = subprocess.run(["go", "tool", "cover", "-func=" + out],
                          cwd=ROOT, env=go(), capture_output=True, text=True)
    if func.returncode != 0:
        return [], None, "go tool cover: " + func.stderr.strip()[-300:]

    zeros, total = [], None
    for line in func.stdout.splitlines():
        m = re.match(r"^(\S+?):(\d+):\s+(\S+)\s+([\d.]+)%", line)
        if m:
            if float(m.group(4)) == 0.0:
                # QUALIFY THE NAME (beat 788). `go tool cover -func` prints the receiver-less name,
                # so nine different 0.0% functions in internal/central all printed as their bare
                # identifier -- and a declaration keyed on that identifier silently covered SIX
                # different methods named `Alert`. I then wrote a `why=` describing "central's
                # alert-path entry point with no in-tree caller" for what was actually
                # `LogAlerter.Alert`, which HAS a caller (config.go:254) and five siblings.
                # A label that cannot be quoted is not a label. file:line makes it unique and
                # points at the code.
                zeros.append(("%s:%s:%s" % (os.path.basename(m.group(1)), m.group(2), m.group(3)),
                              m.group(3)))
            continue
        t = re.match(r"^total:\s+\(statements\)\s+([\d.]+)%", line)
        if t:
            total = float(t.group(1))
    if total is None:
        return [], None, "no `total:` line -- the parser is broken, so this measurement says nothing"
    return sorted(zeros), total, None


def main():
    check = "--check" in sys.argv[1:]

    # PREMISE: green with the product deleted is a green light, not a check.
    if not os.path.isdir(os.path.join(ROOT, "internal", "rules")) or not os.path.exists(PACK):
        print("FAIL coverfloor: internal/rules/ or rules/carbonato.yaml is absent -- nothing to measure")
        return 1

    bad, unmeasured, seen_zero = [], [], set()
    summary = []

    for pkg, cfg in PACKAGES.items():
        short = pkg.strip("./").rstrip("/").replace("internal/", "")
        zeros, total, err = measure(pkg)
        if err:
            unmeasured.append(short)
            print("FAIL coverfloor: %s produced no coverage measurement (not a floor breach): %s"
                  % (short, err))
            continue
        for key, name in zeros:
            seen_zero.add(key)
            decl = DECLARED_ZERO.get(key) or DECLARED_ZERO.get(name)
            name = key
            if decl is None:
                print("FAIL coverfloor: %s: %s is at 0.0%% and is NOT declared -- code that has never "
                      "run" % (short, name))
                bad.append((short, name))
                continue
            if "why=" not in decl or "next=" not in decl:
                print("FAIL coverfloor: %s: %s is declared zero but the declaration is malformed "
                      "(needs why= and next=) -- an entry with no reason is an excuse, not a decision"
                      % (short, name))
                bad.append((short, name))
                continue
            print("declared-zero %s: %s -- %s" % (short, name, decl))

        floor = cfg.get("floor")
        if floor is None:
            summary.append("%s %.1f%%" % (short, total))
            continue
        if total < floor:
            print("FAIL coverfloor: %s total %.1f%% is BELOW the %.1f%% floor" % (short, total, floor))
            bad.append((short, "floor"))
        else:
            print("ok   coverfloor: %s total %.1f%% (floor %.1f%%)" % (short, total, floor))

    for name in DECLARED_ZERO:
        if name not in seen_zero:
            print("note  coverfloor: %s is declared zero but is no longer at 0.0%% -- if it is now "
                  "exercised, delete its declaration so the list stays honest" % name)

    print("ok   coverfloor: measured %d package(s); ungated: %s; %d failure(s)"
          % (len(PACKAGES) - len(unmeasured), ", ".join(summary) or "none", len(bad)))

    if check and (bad or unmeasured):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

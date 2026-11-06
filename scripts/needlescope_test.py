#!/usr/bin/env python3
"""needlescope_test -- pins the instrument's own numbers, so needlescope cannot quietly change.

The rule this follows (beat 636): a number I print is a claim, so something must fail when the
claim stops being true. `needlescope.py` is an instrument -- it exits 0 always -- and an
instrument with no pin is just output.

Each pin below is the number it was when it was measured, WITH the reason it should move:
  - path/endpoint glob reachability going up means a new glob arrived in the pack
    -> add fixtures for it in the family's fixture file, then move the pin.
  - fixture-flip counts changing means a fixture row was added, removed, or the matcher changed.
    Both are decisions; the pin exists to force the decision to be made out loud.

Exit 1 on any pin mismatch, printing what it was and what it is.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

PINS = {
    # measured 2026-11-06, beat 831
    "path_glob_any:globs": 30,
    "path_glob_any:reachable": 28,      # 2 of 30 have no slash-free needle (they end in /*)
    "endpoint_glob_any:globs": 32,
    "endpoint_glob_any:reachable": 32,  # every endpoint glob is a bare *domain* needle
    "fixture_flips:path": 8,            # 7 engine-wrong + 1 where the fixture argues for contains
    "fixture_flips:endpoint": 0,        # every endpoint fixture matches SELF without the fallback
}


def run():
    out = subprocess.run(
        [sys.executable, os.path.join(HERE, "needlescope.py")],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        print("needlescope could not run -- that is the failure, not a pin:")
        print(out.stderr.strip()[:2000])
        sys.exit(1)
    return out.stdout


def parse(text):
    got = {}
    for line in text.splitlines():
        s = line.strip()
        for fam in ("path_glob_any", "endpoint_glob_any"):
            if s.startswith(fam + ":"):
                got[f"{fam}:globs"] = int(s.split("of", 1)[1].split()[0].strip().rstrip(","))
                got[f"{fam}:reachable"] = int(s.split(":", 1)[1].split()[0])
    counts = {"path": 0, "endpoint": 0}
    in_flips = False
    for line in text.splitlines():
        if "verdict flips when the fallback is removed" in line:
            in_flips = True
            continue
        if in_flips:
            if line.strip().startswith("over-match probe"):
                in_flips = False
                continue
            if "pathfixture.py" in line:
                counts["path"] += 1
            elif "endpointfixture.py" in line:
                counts["endpoint"] += 1
    got["fixture_flips:path"] = counts["path"]
    got["fixture_flips:endpoint"] = counts["endpoint"]
    return got


def main():
    text = run()
    got = parse(text)
    # Verdicts are printed per pin, `ok ` or `FAIL`, because a suite that prints one summary line
    # with rc=0 is scored `no-verdicts-printed` by suitecheck.sh (beat 646) -- and it would be right:
    # six silent checks are not six checks. Measured live, this file failed the gate that way first.
    fails = 0
    for key, want in PINS.items():
        if key not in got:
            print(f"FAIL {key}: needlescope printed no number for it (want {want})")
            fails += 1
        elif got[key] != want:
            print(f"FAIL {key}: pinned {want}, measured {got[key]}")
            fails += 1
        else:
            print(f"ok   {key} == {want}")
    if fails:
        print(f"{fails} pin(s) moved. If the change is intended, update PINS in needlescope_test.py "
              f"and say why in the comment.")
        return 1
    print(f"ok   needlescope pins hold ({len(PINS)} pins)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

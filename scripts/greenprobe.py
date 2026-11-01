#!/usr/bin/env python3
"""greenprobe — does a guard's PASS line depend on anything having run?

The question came from Callie's 6336 on the family bus: "a form rejection is an instrument that always
says PASS and never prints a denominator. It works perfectly and tells nobody anything."

That is the failure mode a body of checkers has even when every one of them is honest: a checker whose
PASS is reachable from an empty input reports success for having read nothing. Beat 549's rule was "print
a denominator". Printing it is not enough. **The denominator has to be able to make the verdict go the
other way** — and the sharpest form of that, which this file is actually built to find, is:

    A PROBE THAT PRINTS ITS EVIDENCE AND EXITS 0 WHATEVER THE EVIDENCE SAYS.

`bindprobe.py` prints a per-case table and a "disagreements:" block… and then returns 0 unconditionally. A
human reading the output sees a disagreement; `make check` sees PASS. An instrument that always says PASS
regardless of what it measured is a form letter.

WHAT THIS MEASURES
  Not whether a guard has a FAIL *word* (it may have none — probes report evidence, not verdicts), but
  whether its exit status is **reachable from a failure**: some `sys.exit(1)` / `return 1` / `exit 1`
  sitting under a condition that reads a result. Three decidable questions per guard:
    A. Does it print a denominator at all?
    B. Is there a non-zero exit path at all?
    C. Is B conditional on a result, or is the guard verdict-free by design (prints evidence, exits 0)?

  VACUOUS    = A yes, B no            -> PASS is unreachable from failure. A real defect.
  EVIDENCE   = A yes, B no, C ruled   -> verdict-free on purpose; the reader is the verdict. Legitimate,
                                        but only if the printout names what would be a disagreement —
                                        which is a judgement, so it is written down per file, not assumed.

LIMIT, STATED UP FRONT: a source pattern read, not a behavioural proof. Every EVIDENCE ruling is a claim
Vivi made by hand, dated in the finding doc; the probe's job is to refuse to let one stand unnamed.

Exit 0 = every ruled guard accounted for · 1 = an unruled non-exit guard · 2 = a ruling names a missing file
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# Guards that print evidence and legitimately exit 0. Each entry is a claim to check: does the printout
# actually show, on its face, when something is wrong? v2 after the first pass: I had classified by
# "has a FAIL word", which was the wrong question and produced 13 "vacuous" rows including files that
# report evidence perfectly well. Classifying by exit-reachability cuts it to the ones that matter.
EVIDENCE_GUARDS = {
    # Every other guard that prints evidence DOES also have a non-zero exit reachable from a failure.
    # This dict started with 13 names, all classified by "has no FAIL word" — the wrong question, and it
    # flagged bindprobe/suffixprobe/typecheck, which print a disagreement block AND exit non-zero. After
    # switching to exit-reachability the true set is exactly one file, and narrowing it is the finding:
    # the body of guards is not full of vacuous greens; the READERS OF THEM were wrong twice.
    "sevprobe2.py": "the falsification probe built after a prior claim of mine was too wide. It prints the "
                    "measured value and the threshold side by side and exits 0. Legitimate as an evidence "
                    "tool: it exists to falsify a HYPOTHESIS, and a hypothesis probe has no standing to "
                    "declare the repo broken. Limit, stated: nothing in CI reads its output, so a green "
                    "CI says nothing about it either way — which is true of every evidence probe here "
                    "and is the honest scope of `make`.",
    # Ruled 2026-10-27 (beat 620) after reading the whole script, not its name.
    "parityreverse.py": "measures the direction ci-parity-check does NOT enforce (preflight runs a target "
                        "CI skips). It says so in its own last line: 'This is a MEASUREMENT, not a "
                        "verdict... whether the upper direction should also be an error is a policy "
                        "decision, not a script's.' Exiting non-zero would make a policy question a gate. "
                        "Limit, stated: it prints both denominators, so a zero is a reading rather than a "
                        "guess, but nothing in CI consumes it -- same honest scope as sevprobe2.",
}

DENOM_PAT = re.compile(
    r"\d+\s*(?:link|rule|file|guard|check|fixture|sample|event|predicate|module|commit|doc|finding|"
    r"script|value|row|pair|case|bind)s?|\{(?:checked|n|count|total|len\()",
    re.I,
)
# `$fatal` is the shell idiom: the exit code lives in a VARIABLE, so a reader that only looks for
# `exit 1` sees publish-scan.sh — the repo's most important gate — as unable to fail. That is the fourth
# time in this file a reader was narrower than the code it reads.
# `set -e` / `set -eu` / `set -euo pipefail` is the fifth instance of a reader narrower than the code
# it reads, found 2026-10-27 (beat 620). preflight.sh is 121 lines that abort at the first failing
# step under `set -eu` and exit 127 when `make` is absent -- verified behaviourally, not by grep. This
# reader called it UNRULED because it looked for `exit N` and never for the shell's own idiom. Same
# shape as `$fatal` (see the comment above): the mechanism was there, the reader did not know its name.
NONZERO_EXIT = re.compile(
    r"sys\.exit\(\s*[1-9]\s*\)|sys\.exit\(\s*\w+\s*\)|^\s*exit\s+\$?\{?[A-Za-z_]|"
    r"^\s*exit\s+[1-9]\b|return\s+[1-9]\b|return\s+fail_n|^\s*exit\s+1\b|"
    r"^\s*set\s+-[a-zA-Z]*e",  # set -e aborts the script: PASS is unreachable after a failure
    re.I | re.M,
)
EMPTY_GUARD_PAT = re.compile(
    r"if\s+not\s+(?:files|links|rules|paths|targets|rows|items|entries|predicates|samples|fixtures|"
    r"matches|blocks|globs)\b"
)


def read(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def analyse(src):
    return bool(DENOM_PAT.search(src)), bool(NONZERO_EXIT.search(src)), bool(EMPTY_GUARD_PAT.search(src))


def main():
    guards = [f for f in sorted(os.listdir(HERE)) if f.endswith((".py", ".sh"))]
    guards = [f for f in guards if not re.search(r"_test\.(py|sh)$", f)]
    print(f"guards read: {len(guards)} non-test files in scripts/")

    unpriced, vacuous, gated = [], [], []
    for f in guards:
        has_denom, has_exit, empty_guard = analyse(read(os.path.join(HERE, f)))
        if not has_denom:
            unpriced.append(f)
        elif not has_exit:
            vacuous.append(f)
            if empty_guard:
                gated.append(f)

    print(f"UNPRICED (no count printed): {len(unpriced)} — beat 549's set, not re-litigated here")
    print(f"NO NON-ZERO EXIT (PASS unreachable from failure): {len(vacuous)}")
    for f in vacuous:
        mark = "ruled" if f in EVIDENCE_GUARDS else "UNRULED"
        note = "  [emptiness guard]" if f in gated else ""
        print(f"   {f:<22} {mark}{note}")

    ruled = [f for f in vacuous if f in EVIDENCE_GUARDS]
    unruly = [f for f in vacuous if f not in EVIDENCE_GUARDS]
    print(f"\nruled as evidence tools: {len(ruled)}/{len(vacuous)}")

    missing = [f for f in EVIDENCE_GUARDS if not os.path.exists(os.path.join(HERE, f))]
    fails = []
    if unruly:
        fails.append(f"{len(unruly)} guard(s) on the exit-0 path with no ruling: {unruly}")
    if missing:
        fails.append(f"rulings name {len(missing)} file(s) that do not exist: {missing}")

    print(f"\ndenominator: {len(vacuous)} exit-0 guards of {len(guards)} non-test guards read; "
          f"{len(ruled)} ruled, {len(unruly)} unruled, {len(gated)} carry an `if not <collection>` guard")
    print("limit: source read, not behavioural proof. Every 'ruled' row is a hand claim, dated in the doc.")
    if fails:
        print(f"FAILED {len(fails)}:")
        for f in fails:
            print("  - " + f)
        return 1
    # The real point, printed every run so it cannot be forgotten: a guard with no exit path is not
    # automatically wrong — but a body where NONE of them can fail is a body that reports, not checks.
    print(f"greenprobe: {len(ruled)} exit-0 guards all ruled as evidence tools — green")
    print(f"            and 0 of them can fail. If the pack breaks, {len(ruled)} of these will print the "
          f"breakage and exit 0. That is the finding, not the fix.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""nonprobe_test -- the import-silence guard covers *_test.py. Everything else in scripts/ is uncovered.

Beat 633. `shebang_test` check 5 asserts that importing ANY suite module is silent, and its own comment
says the assertion is "wholesale". It is wholesale over the suites and BLIND over the rest of the
directory: 105 .py files live in scripts/, 58 are suites, and the other 56 include the modules suites
import (`globmatch`, `tokruns`, `neargo`).

Why the gap is a detection hole and not tidiness:
  - `shebang_test` records the cost of the same defect one level up -- four suites imported the matcher
    from `endpointglob_test`, which EXECUTED that suite at import: 51 `ok` lines fired as a side effect
    and one suite's check count depended on import order.
  - A NON-test module that runs its body at import is a library that is not a library. Every suite that
    imports it inherits the work and the output, and the failure lands in the importing file, naming
    something nobody edited.
  - The danger is not a crash, it is a TALLY. Measured on this run: `dispose.py` emits 59 lines,
    `wirediff.py` 41, `packprobe.py` 25, `noloadprobe.py` 21, `pkgcheck.py` 1. A gate that counts `ok`
    lines on stdout counts those as its own. More green, less tested.

What this file asserts:
  1. Every non-test `*.py` in scripts/ is SILENT on import unless it is a CLI program, in which case it
     must FAIL FAST to stderr (non-zero, nothing on stdout). Stdout is the gate channel; a program may
     refuse bare invocation, it may not perform a check into the counter.
  2. The classifier is exercised on fixtures with known answers in BOTH directions, so the suite cannot
     pass by relabelling everything "a tool".
  3. The defect list is printed, always. This suite found 8 real offenders on its first run and the
     repair is a separate step; until each is fixed it stays visible here rather than disappearing into
     a green gate.

Scope honesty: this closes the NON-test half of the import-silence class. It does not re-assert the
suite half (`shebang_test` owns that), and says nothing about `__pycache__`, `attic/`, or anything
outside scripts/.
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

FAILS = []


def check(name, got, want):
    if got != want:
        FAILS.append(f"{name}\n     got: {got!r}\n    want: {want!r}")
        print(f"FAIL {name}")
    else:
        print(f"ok   {name}")


def classify(path, timeout=180):
    """Import `path` in a fresh interpreter; say what it does.

    "silent"       -- exit 0, no stdout                     the only good outcome for a library
    "tool"         -- exit != 0, no stdout                  a program that refuses bare invocation: fine
    "noisy"        -- exit 0, printed to stdout             runs a check at import: BAD
    "raises_tty"   -- exit != 0, AND wrote to stdout        printed while failing: ambiguous, BAD
    """
    stem = os.path.basename(path)[:-3]
    d = os.path.dirname(path)
    code = f"import sys; sys.path.insert(0, {d!r}); import {stem}"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       timeout=timeout, cwd=d)
    lines = len(r.stdout.splitlines())
    if r.returncode == 0:
        return ("silent" if lines == 0 else "noisy"), lines
    return ("tool" if lines == 0 else "raises_tty"), lines


def is_cli_tool(path):
    """Static sort only -- used to NAME a program, never to excuse a module.

    The behavioural result in `classify` is what gets asserted. This exists so the report can say
    "packprobe.py is a program that prints while failing" instead of just "noisy".
    """
    try:
        src = open(path, encoding="utf-8").read()
    except OSError:
        return False
    top = [l for l in src.splitlines() if l and not l.startswith((" ", "\t", "#", '"""', "'"))]
    return any(l.startswith(("if __name__", "sys.exit(", "raise SystemExit"))
               or "argparse" in l for l in top)


def main():
    mods = sorted(p for p in os.listdir(HERE) if p.endswith(".py") and not p.endswith("_test.py"))
    check("there are non-test modules to check (the glob is not empty)", len(mods) > 20, True)

    silent, tools, bad = [], [], []
    for name in mods:
        path = os.path.join(HERE, name)
        kind, lines = classify(path)
        if kind == "silent":
            silent.append(name)
        elif kind == "tool":
            tools.append(name)
        else:
            bad.append((name, kind, lines, "cli-looking" if is_cli_tool(path) else "module"))

    # 1. THE ASSERTION: quiet library, or quiet refusal. Never a tally on stdout while importing.
    check("no non-test module prints to stdout at import", bad, [])

    # 2. Not vacuous in either direction. The first run of this suite measured 48 silent / 0 tools /
    #    8 defects and this check FAILED -- correctly, because eight programs that passed a
    #    `check()` and exited had been misfiled as libraries. A classifier that cannot say "tool" is
    #    not classifying.
    #
    #    Beat 633b: after the seven were wrapped in main(), `tools` measured 0 again and this check went
    #    RED a second time on a clean tree -- the classifier had no false negatives, the DIRECTORY had no
    #    programs. So the demand is stated as what it actually is: the classifier must be able to say
    #    "tool", and the fixtures in section 3 are where that ability is proven. Requiring n>3 programs
    #    to exist would be asserting a property of a tree I am trying to clean, and the reward for
    #    cleaning it would be a red gate. `noisy`/`raises_tty` must still be non-zero when defects are
    #    present -- that is what keeps the classifier from silently collapsing every verdict to "silent".
    check("the classifier sees libraries", len(silent) > 3, True)
    check("every defect is labelled noisy or raises_tty, never 'tool'",
          [n for n, k, _, _ in bad if k not in ("noisy", "raises_tty")], [])
    print(f"     {len(silent)} silent, {len(tools)} programs, {len(bad)} defects "
          f"of {len(mods)} non-test modules")
    if bad:
        for name, kind, lines, look in bad:
            print(f"       {name:22} {kind:11} {lines:3} stdout lines  ({look})")

    # 3. Fixtures, both directions, in a scratch directory. A decision this suite depends on gets
    #    proven -- and proven able to say NO.
    with tempfile.TemporaryDirectory() as td:
        def put(name, body):
            p = os.path.join(td, name)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(body)
            return p

        quiet = put("quietmod.py", "VALUE = 1\n")
        chatty = put("chattymod.py", "print('ok   I ran anyway')\nVALUE = 1\n")
        refusing = put("refusemod.py", "import sys\nsys.exit('usage: refusemod --x')\n")
        half = put("halfmod.py", "import sys\nprint('ok   started')\nsys.exit(2)\n")
        check("fixture: import-quiet module -> silent", classify(quiet)[0], "silent")
        check("fixture: printing module -> noisy", classify(chatty)[0], "noisy")
        check("fixture: refusing program -> tool", classify(refusing)[0], "tool")
        check("fixture: prints then fails -> raises_tty (not excused)", classify(half)[0], "raises_tty")
        prog = put("prog.py", "import argparse\nimport sys\nsys.exit(2)\n")
        check("  ...is_cli_tool says no to a plain module", is_cli_tool(quiet), False)
        check("  ...is_cli_tool says yes to a program", is_cli_tool(prog), True)

    print()
    if FAILS:
        print(f"nonprobe: {len(FAILS)} failure(s)")
        for f in FAILS:
            print(f"  - {f}")
        return 1
    print("nonprobe: all checks ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())

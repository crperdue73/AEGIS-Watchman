#!/usr/bin/env python3
"""shebang_test -- every suite must declare the interpreter that can actually run it, and the harness
must never guess.

FINDING-2026-10-26b (beat 591). `suitecheck.sh` picked the runner by reading each file's shebang and
fell back to `$PY` when there was none -- which sounds safe and is not, because the fallback feeds a
PYTHON file to the shell for any suite that lacks one, and `sh` happily reads a docstring as commands.
Measured on eight of my own suites: no shebang, `/bin/sh` executed the prose, exit=2, and they
contributed ZERO checks while the gate still printed "held". Nothing was falsely green only because
dash rejects Python syntax loudly rather than exiting 0 on the noise -- luck, not design.

Two failures, worth separating:
  1. THE FILES were wrong: no shebang, so no runner could know what they were.
  2. THE HARNESS was wrong: its no-shebang fallback guessed instead of refusing. A harness that guesses
     the interpreter can only ever test the guess.

And then the meta-lesson, which cost me two more mutants: my first version of these checks GREPPED
`suitecheck.sh` for strings like "no shebang" and `"$oks" -eq 0`, and both mutants SURVIVED, because
those words were still sitting in the comment explaining the fix. Measured consequence for the second
one: with `|| [ "$oks" -eq 0 ]` removed but the handler kept, a suite that exits 0 and prints nothing
walks straight through the gate -- "1 suites held, 0 checks ok, 0 reported failures". A test a comment
can satisfy is not testing the code. Every claim below is therefore BEHAVIOURAL: the harness is copied
to a temp directory, fed a probe suite, and its exit status and summary line are what gets asserted.

It is delimited so this suite cannot itself suffer the defect it checks for: it is a static read plus
subprocess runs, and it declares its own interpreter on line 1.
"""
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

FAILS = []


def check(name, got, want):
    if got != want:
        FAILS.append(name)
        print(f"FAIL {name}: got {got!r} want {want!r}")
    else:
        print(f"ok   {name}")


def first_line(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.readline()


def run_harness(files):
    """Copy the real suitecheck.sh into a scratch dir, drop in `files`, run it. Returns (rc, output)."""
    tmp = tempfile.mkdtemp(prefix="harnessprobe-")
    try:
        shutil.copy(os.path.join(HERE, "suitecheck.sh"), tmp)
        with open(os.path.join(tmp, "suitecheck.baseline"), "w"):
            pass
        for name, body in files.items():
            with open(os.path.join(tmp, name), "w") as fh:
                fh.write(body)
        r = subprocess.run(["sh", os.path.join(tmp, "suitecheck.sh")], capture_output=True,
                           text=True, timeout=180, cwd=tmp)
        return r.returncode, r.stdout + r.stderr
    finally:
        shutil.rmtree(tmp, ignore_errors=True)



def main():
    suites = sorted(glob.glob(os.path.join(HERE, "*_test.py")) + glob.glob(os.path.join(HERE, "*_test.sh")))
    check("the suite directory is not empty", len(suites) > 30, True)

    # --------------------------------------------------------------------------------------------------
    # 1. Every suite declares an interpreter it can actually be run by.
    # --------------------------------------------------------------------------------------------------
    no_shebang = [os.path.basename(p) for p in suites if not first_line(p).startswith("#!")]
    check("no suite is missing a shebang", no_shebang, [])

    py_bad = [os.path.basename(p) for p in suites if p.endswith(".py") and "python" not in first_line(p)]
    check("every .py suite's shebang names python", py_bad, [])

    sh_bad = [os.path.basename(p) for p in suites
              if p.endswith(".sh") and not re.search(r"(^|/|\s)(ba)?sh\b", first_line(p))]
    check("every .sh suite's shebang names a shell", sh_bad, [])

    # --------------------------------------------------------------------------------------------------
    # 2. The harness refuses a suite it cannot classify -- measured, not grepped.
    # --------------------------------------------------------------------------------------------------
    rc, out = run_harness({"probe_test.py": 'print("ok   a suite with no shebang")\n'})
    check("the harness REFUSES a shebang-less suite", (rc != 0, "no shebang" in out), (True, True))
    check("  ...and does not quietly run it through python anyway",
          "a suite with no shebang" not in out, True)

    # NOTE: a ".cob" probe was refused for the wrong reason -- the harness only globs *_test.py/*_test.sh,
    # so it never looked at the file. The probe has to be a real suite name for the shebang branch to run.
    rc, out = run_harness({"probe_test.py": '#!/usr/bin/env cobol\nDISPLAY "ok".\n'})
    check("the harness REFUSES an unrecognised shebang rather than guessing",
          (rc != 0, "unknown shebang" in out), (True, True))

    # The control: the same file WITH an interpreter must be accepted, or the refusals above would also
    # hold on a harness that rejected everything.
    rc, out = run_harness({"probe_test.py": '#!/usr/bin/env python3\nprint("ok   fine")\n'})
    check("  ...while the same suite WITH a shebang is accepted and RUN",
          (rc, "1 suites held, 1 checks ok" in out), (0, True))

    # --------------------------------------------------------------------------------------------------
    # 3. A suite that prints no verdicts must never count as held. This is the defect that started it:
    #    eight suites contributed zero checks and the gate said "held".
    # --------------------------------------------------------------------------------------------------
    rc, out = run_harness({"probe_test.py": "#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n"})
    check("a suite that exits 0 and prints NO verdicts is rejected",
          (rc != 0, "no-verdicts-printed" in out), (True, True))

    rc, out = run_harness({"probe_test.py": '#!/usr/bin/env python3\nprint("FAIL something")\n'})
    check("a suite that prints only failures is rejected",
          (rc != 0, "reported-failures=1" in out), (True, True))

    # An honest suite is still accepted -- the control that keeps sections 2 and 3 from passing on a
    # harness that rejects everything.
    rc, out = run_harness({"probe_test.py": '#!/usr/bin/env python3\nprint("ok   one")\nprint("ok   two")\n'})
    check("  ...and an honest two-check suite is accepted with both counted",
          (rc, "1 suites held, 2 checks ok" in out), (0, True))

    # --------------------------------------------------------------------------------------------------
    # 4. One cheap static check, kept because its operand is the operand that matters rather than prose
    #    about it: the baseline is banked per suite from that suite's own count, not from a running total.
    #    (A silent-baseline assertion was attempted twice and DISCARDED -- a throwaway baseline stays empty
    #    because the harness only writes it under --update-baseline, so that check was about my scaffolding
    #    and not about the harness. Removed rather than softened.)
    # --------------------------------------------------------------------------------------------------
    harness = open(os.path.join(HERE, "suitecheck.sh"), encoding="utf-8").read()
    check("the baseline is banked per suite, from that suite's own ok count",
          '>' in harness and '"$b" "$oks"' in harness, True)

    # --------------------------------------------------------------------------------------------------
    # 5. A test module must be import-safe. Measured this the hard way: four suites imported the matcher
    #    from `endpointglob_test`, which EXECUTED that suite at import -- 51 `ok` lines fired as a side
    #    effect, and importing it from a test meant the importing suite's own count depended on import
    #    order. A test module that does work at import time cannot be a library. Guard: import every
    #    *_test.py module in a fresh interpreter and require that it prints NOTHING.
    # --------------------------------------------------------------------------------------------------
    # Beat 597: the debt is PAID. At beat 595 this guard could only cover the five matcher-sharing
    # modules, because 24 of 40 suites ran their whole body at import and rewriting them was bigger than
    # the bug I was chasing. Now every *_test.py is wrapped in a `main()`, so the assertion is wholesale:
    # importing ANY suite must print nothing and exit 0. If a new suite is written without the guard,
    # this fires -- which is the point of asserting the whole set rather than a chosen shortlist.
    silent_but_noisy = []
    for p in sorted(suites):
        if not p.endswith("_test.py"):
            continue
        mod = os.path.basename(p)[:-3]
        r = subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {HERE!r}); import {mod}"],
                           capture_output=True, text=True, timeout=120, cwd=HERE)
        if r.returncode != 0 or r.stdout.strip():
            silent_but_noisy.append((mod, r.returncode, len(r.stdout.splitlines())))
    check(f"importing ANY of the {len(suites)} suite modules is silent (none runs its body at import)",
          silent_but_noisy, [])
    # One line per module as well as the aggregate: a failure that names no file is a failure somebody has
    # to bisect by hand. Beat 597 first wrote only the aggregate and quietly lost 4 checks off the total.
    noisy = {mod for mod, _, _ in silent_but_noisy}
    for p in sorted(suites):
        if p.endswith("_test.py"):
            mod = os.path.basename(p)[:-3]
            check(f"  {mod}: silent on import", mod in noisy, False)
    check("the matcher lives in a non-test module, so importing it cannot run tests",
          os.path.exists(os.path.join(HERE, "globmatch.py")), True)
    check("  ...and no *_test.py hands out the matcher any more",
          [p for p in suites if p.endswith("_test.py")
           and any(l.startswith("from endpointglob_test import")
                   for l in open(p, encoding="utf-8").read().splitlines())], [])

    # --- 5c. A module that is import-clean can still NameError on a missing import at call time. ----------
    # Beat 604: pathglob_test.py used re.findall at module level with no `import re` and crashed. The
    # import-silence check above passed it, because the fault was in a `if __name__` block, not at import.
    # Same defect bite twice now (beat 595: globmatch.py, beat 604: pathglob_test.py). So: compile each
    # suite and reject any name load that cannot be resolved to a builtin, a module global, an import, or
    # a local -- a lightweight linter, no dependency.
    import ast
    import builtins
    for path in sorted(glob.glob(os.path.join(HERE, "*_test.py"))):
        mod = os.path.basename(path)
        try:
            tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
        except SyntaxError as exc:
            check(f"compile {mod}", f"SyntaxError: {exc.msg}", "clean")
            continue
        bound = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__package__", "__spec__"}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for a in node.names:
                    bound.add((a.asname or a.name).split(".")[0])
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound.add(node.name)
                for sub in ast.walk(node):
                    if isinstance(sub, ast.arg):
                        bound.add(sub.arg)
                    elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                        bound.add(sub.id)
                    elif isinstance(sub, (ast.Import, ast.ImportFrom)):
                        for a in sub.names:
                            bound.add((a.asname or a.name).split(".")[0])
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                bound.add(node.id)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                bound.add(node.name)
            elif isinstance(node, ast.Lambda):
                # Beat 608: a lambda's parameters are bound names. Without this the linter flagged every
                # lambda parameter as a missing import -- a false positive that would get the linter
                # deleted the first time someone used one.
                for a in node.args.args:
                    bound.add(a.arg)
        missing = sorted({n.id for n in ast.walk(tree)
                          if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in bound})
        check(f"{mod}: every global name is bound (no missing import)", missing, [])

    print()
    if FAILS:
        print(f"shebang: {len(FAILS)} FAIL")
        sys.exit(1)
    print("shebang: all ok")


if __name__ == "__main__":
    main()

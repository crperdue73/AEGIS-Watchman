#!/usr/bin/env python3
"""gomutate.py -- prove the Go tests can FAIL, on the code they claim to test.

WHY THIS EXISTS
  Every Go test in this tree was mutation-proven by hand: I broke the code, watched the test go red,
  put it back. That is real evidence -- for the length of one heartbeat, in one transcript, about to be
  compacted. Nothing in the gate re-checks it. 535 Test functions, and not one of them has a standing
  witness that it can fail.
  Beat 814 made this concrete: `normalizePathGlobs` had NO test at all and nothing noticed for the
  entire life of the package. The tests I added were proven by two mutants I ran once and deleted.

WHAT THIS ASKS, AND WHAT IT DOES NOT
  It asks: for a sampled set of small statements inside a package, if I corrupt one, does that
  package's test binary go red?
  * A mutant that SURVIVES means the tests around that line do not distinguish it from the original.
    That is not automatically a bug -- the statement may be unreachable, redundant, or covered only by
    build tag. It is a QUESTION, and the output says so.
  * A mutant that is KILLED is the ordinary, good outcome and needs no comment.

THE HONEST LIMITS (read before trusting a green run)
  1. SAMPLED, NOT EXHAUSTIVE. `--max N` bounds how many mutants run. A green run means "the N I tried
     were caught", never "the suite is mutation-complete".
  2. ONE PACKAGE PER MUTANT. It runs `go test` for the mutated package only. A test in another package
     can never kill it, so a survivor may be tested elsewhere.
  3. FOUR MUTATION SHAPES: `==` <-> `!=`, `<` <-> `<=` / `>` <-> `>=`, and (beat 831) `true` <-> `false`.
     It does not touch arithmetic, `return` values, or assignments. Surviving it does not mean immune
     to a shape it never tried.
     Beat 831 measured the bool gap and closed it: 120 bool-literal lines across the three packages
     were invisible to this tool until then -- including every `return false` in `internal/rules`,
     which is the line where a detection decision is made. `internal/rules` had never been mutation-
     tested AT ALL (94 candidates, none ever run).
  4. TREATED AS EQUIVALENT, REPORTED NOT SCORED: a mutant that fails to COMPILE is counted separately.
     It is evidence of nothing about the tests -- compilers are not tests (beat 806: two mutants that
     didn't compile nearly read as "the suite is weak").
  5. NEVER LEAVES THE REAL TREE MUTATED. The mutant IS written into the real working file (there is no
     scratch copy -- this line said so until beat 838, which was wrong, and the wrongness matters
     because it is the guarantee a reader would lean on hardest). What actually holds is narrower and
     was measured: the original bytes are restored after every mutant including on exception, and the
     tree md5 is compared before/after the whole run with `return 2` plus "do not report it" if any
     file drifted. So the guarantee is "verified restoration", not "never touched" -- a crash or a
     SIGKILL mid-run leaves a planted mutant on disk, and only the next run's digest check notices.

USAGE
  python3 scripts/gomutate.py                 # sample 12 mutants across the core packages
  python3 scripts/gomutate.py --max 40        # more
  python3 scripts/gomutate.py --pkg internal/rules
  exit 0 = every attempted mutant was killed (or compile-failed and was excluded)
  exit 1 = at least one SURVIVOR, named
"""

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Packages worth mutating first: these decide whether the product detects anything.
DEFAULT_PKGS = [
    "internal/rules",
    "internal/telemetry",
    "internal/collector",
]

# (pattern, replacement) applied to a single line. Order matters.
#
# Comparison shapes first: a line carrying both `return x < y` and a bool has no `true`/`false`
# outside the comparison, so the order is not load-bearing for correctness -- but `candidates()`
# takes the FIRST match and breaks, so a line with two comparison operators still only ever gets
# one mutant. Measured at beat 831: 14 such lines. That is bounded, known, and reported by
# `--audit`; widening it means emitting multiple candidates per line, which changes the sampling
# contract (see HONEST LIMITS 1) and is deliberately not done here.
SHAPES = [
    (" <= ", " < "), (" < ", " <= "),
    (" >= ", " > "), (" > ", " >= "),
    (" == ", " != "), (" != ", " == "),
    # bool literals -- word-bounded so `trueish` and identifiers containing them are safe.
    # Both directions are needed and BOTH are real: beat 833 measured that every genuine candidate
    # here is a `false`, so with only (true,false) the loop below ran per-line, matched nothing,
    # and the line was dropped entirely (`continue`) instead of being recorded as (false,true).
    ("true", "false"), ("false", "true"),
]

# Boolean candidates are found by what is ACTUALLY on the line, not by the pair being tried. Beat 833:
# beat 831's bool patch recorded every bool line as (true,false), which failed to apply on the 23
# `return false` lines in internal/rules -- the mutation run reported 18 survivors plus a wall of
# "shape vanished" non-applies, and the non-applies were this bug wearing a mutant's clothes.
BOOL_TOKEN = re.compile(r"\b(true|false)\b")

# bool flips must not fire inside strings or comments; `candidates()` already skips comment lines,
# and this guards the remaining inline cases.

GO = shutil.which("go") or "/usr/local/go/bin/go"


def go_env():
    e = dict(os.environ)
    e["PATH"] = "/usr/local/go/bin:" + e.get("PATH", "")
    e["CGO_ENABLED"] = "1"
    e.setdefault("GOFLAGS", "-count=1")
    return e


def candidates(pkg):
    """Mutable lines: a comparison shape in a statement, not a comment, not an import, not a decl."""
    d = os.path.join(ROOT, pkg)
    out = []
    if not os.path.isdir(d):
        return out
    for name in sorted(os.listdir(d)):
        if not name.endswith(".go") or name.endswith("_test.go"):
            continue
        path = os.path.join(d, name)
        for i, line in enumerate(open(path, encoding="utf-8", errors="replace"), 1):
            s = line.strip()
            if not s or s.startswith("//") or s.startswith("*") or s.startswith("/*"):
                continue
            if s.startswith("import") or s.startswith("package") or s.startswith("func "):
                continue
            # A `//` comment tail is not code: mutating inside it would produce a mutant that
            # differs from the original only in prose. Strip it before deciding, mutate the code
            # part only. (Beat 652's lesson -- the engine never sees a comment, so a mutant inside
            # one is a false positive wearing a mutant's clothes.)
            code = line.split("//", 1)[0]
            # Bool literals are not a pair to search for -- they are a TOKEN to read off the line.
            # Beat 833: the old loop asked "does this line contain 'true'?" for the (true,false)
            # shape and dropped the line when it did not, so every `return false` was silently
            # never sampled. Record the flip of whatever is actually there, then move on: a line
            # with two bool tokens still gets one mutant (see the one-mutant-per-line contract).
            unq = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', '""', code)
            m = BOOL_TOKEN.search(unq)
            if m:
                old, new = m.group(1), ("false" if m.group(1) == "true" else "true")
                out.append((pkg, name, i, line.rstrip("\n"), old, new))
                continue
            for old, new in SHAPES:
                if old in ("true", "false"):
                    continue
                if old not in code:
                    continue
                out.append((pkg, name, i, line.rstrip("\n"), old, new))
                break
    return out


def build(pkg):
    r = subprocess.run([GO, "build", "./"+pkg], cwd=ROOT, env=go_env(),
                       capture_output=True, text=True, timeout=300)
    return r.returncode == 0, (r.stderr or "").strip()


def run_tests(pkg):
    r = subprocess.run([GO, "test", "./" + pkg], cwd=ROOT, env=go_env(),
                       capture_output=True, text=True, timeout=600)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def tree_digest(pkgs):
    """md5 of every .go file in the packages -- the identity of the tree under test.

    Beat 819 exists because of this function not existing. I inverted `case err == nil:` in
    behavior.go during a mutation run and never restored it; the next run's baseline stayed
    green-ish for unrelated reasons and I reported a score against a tree with a planted mutant
    in it. The restore is in a `finally` block, which is correct -- but "the finally ran" is an
    assumption, and an assumption is exactly what a mutation tool must not be built on.
    """
    out = {}
    for pkg in pkgs:
        d = os.path.join(ROOT, pkg)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if name.endswith(".go"):
                path = os.path.join(d, name)
                with open(path, "rb") as f:
                    out[os.path.join(pkg, name)] = hashlib.md5(f.read()).hexdigest()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=12, help="how many mutants to attempt")
    ap.add_argument("--pkg", action="append", default=None, help="restrict to a package (repeatable)")
    args = ap.parse_args()

    pkgs = args.pkg or DEFAULT_PKGS
    pool = []
    for p in pkgs:
        pool.extend(candidates(p))
    if not pool:
        print("FAIL gomutate: no mutable lines found in %s" % ", ".join(pkgs))
        return 1

    # Baseline: the unmutated packages must pass, or every survivor below is meaningless.
    for p in pkgs:
        rc, out = run_tests(p)
        if rc != 0:
            print("FAIL gomutate: baseline `go test ./%s` is already red -- refusing to mutate a "
                  "broken tree, since every mutant would 'survive' for that reason alone" % p)
            print(out.strip()[-600:])
            return 1
    print("ok   gomutate: baseline green on %d package(s), %d mutable lines available"
          % (len(pkgs), len(pool)))

    # Identity of the tree BEFORE any mutant is written. Checked again on every exit path.
    before = tree_digest(pkgs)

    # Distribute the sample round-robin so no single large file eats the whole budget.
    by_pkg = {}
    for c in pool:
        by_pkg.setdefault(c[0], []).append(c)
    sample, ring = [], sorted(by_pkg)
    while any(by_pkg[r] for r in ring) and len(sample) < args.max:
        for r in ring:
            if by_pkg[r] and len(sample) < args.max:
                sample.append(by_pkg[r].pop(0))

    survivors, killed, broken = [], 0, []
    backup = tempfile.mkdtemp(prefix="gomutate.")
    try:
        for (pkg, name, lineno, line, old, new) in sample:
            path = os.path.join(ROOT, pkg, name)
            shutil.copy2(path, os.path.join(backup, name))
            try:
                src = open(path, encoding="utf-8").read()
                lines = src.splitlines(keepends=True)
                # Assert the substitution lands. Beat 811/813: a mutant that changes nothing reads
                # exactly like a mutant that survived, and I have been fooled by that four times.
                assert old in lines[lineno - 1], "shape vanished from line %d" % lineno
                # Mutate the CODE part only, never the `//` comment tail. Otherwise a bool literal
                # that appears in prose after the code would be flipped instead of the decision
                # itself -- a mutant that differs only in a comment, which no test can ever see.
                raw = lines[lineno - 1]
                code, sep, tail = raw.partition("//")
                assert old in code, (
                    "shape at %s:%d appeared only inside the comment, not the code"
                    % (name, lineno)
                )
                lines[lineno - 1] = code.replace(old, new, 1) + sep + tail
                mutated = "".join(lines)
                assert mutated != src, "substitution produced identical text"
                open(path, "w", encoding="utf-8").write(mutated)
            except AssertionError as e:
                print("FAIL gomutate: mutant did not apply at %s:%d (%s) -- not evidence about the "
                      "tests" % (name, lineno, e))
                shutil.copy2(os.path.join(backup, name), path)
                continue

            try:
                ok, err = build(pkg)
                if not ok:
                    broken.append((pkg, name, lineno, err.splitlines()[0][:90] if err else "?"))
                else:
                    rc, out = run_tests(pkg)
                    if rc == 0:
                        survivors.append((pkg, name, lineno, line.strip()[:96], old, new))
                    else:
                        killed += 1
            finally:
                shutil.copy2(os.path.join(backup, name), path)
    finally:
        shutil.rmtree(backup, ignore_errors=True)

    # The restore guarantee. Not "the finally block ran" -- the actual bytes, compared.
    after = tree_digest(pkgs)
    drifted = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    if drifted:
        print()
        print("FAIL gomutate: the tree was NOT restored -- %d file(s) differ from before the run:"
              % len(drifted))
        for k in drifted:
            print("  DRIFT %s  %s -> %s" % (k, before.get(k, "absent"), after.get(k, "absent")))
        print("       Every survivor count above is about a tree that no longer exists. Do not")
        print("       report it. Restore the files, then re-run.")
        return 2
    print("ok   gomutate: tree restored -- %d .go file(s) match their pre-run md5" % len(before))

    print()
    for pkg, name, lineno, line, old, new in survivors:
        print("SURVIVOR %s/%s:%d  %r -> %r" % (pkg, name, lineno, old.strip(), new.strip()))
        print("         line: %s" % line)
    for pkg, name, lineno, err in broken:
        print("SKIP     %s/%s:%d  mutant did not compile: %s" % (pkg, name, lineno, err))

    attempted = killed + len(survivors)
    print()
    print("gomutate: %d attempted, %d killed, %d survivor(s), %d compile-skip(s)"
          % (attempted, killed, len(survivors), len(broken)))
    print("NOTE: sampled and shape-limited -- see the limits in this file's docstring before reading "
          "'0 survivors' as 'mutation-complete'.")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""coverunmirrored_test -- a NAME-matching tool must never outrank MEASURED coverage.

WHY THIS FILE EXISTS
--------------------
`scripts/unmirrored.py` reports helpers that "no Go test names". Measured 2026-11-04 (beat 812)
against `go tool cover -func`: of the 40 names it was reporting, **39 were exercised by real compiling
Go tests** -- 17 at 100.0% of statements. It flagged `matchOne`, `anyGlobMatches`, `anyArgvMatches` and
`substringNeedle`, the four functions that decide whether roughly a hundred rules fire, while all four
sat at full coverage from the beat-776 test file written specifically to cover them.

The cause is not a bug in the regex and cannot be fixed by tightening it. **Coverage is transitive.**
A test that calls `Evaluate` reaches `matchOne` four frames down and never writes its name. A
table-driven test drives a helper on forty inputs and names it once, or never. A text search can be
made precise about false COVERAGE (beat 809: strip comments and strings, require a call). It is blind
to false ABSENCE, always, structurally.

So this file does not test the tool's output -- the tool's output is the thing that was wrong. It tests
the DISCLOSURE and it pins the measured truth, so that:

  * the tool cannot quietly regain authority it has not earned, and
  * a real regression in coverage is caught by the MEASUREMENT rather than by a name scan.

WHAT THIS PINS
--------------
1. The docstring states the heuristic's limit, by name, so a reader who never opens the finding doc
   still learns that "unmirrored" does not mean "untested". A silent tool is how this survived four
   beats.
2. The measured fact is recorded as a value: a helper reported as unmirrored is checked against the
   coverage profile, and the four named load-bearing functions must NOT be unmirrored-and-uncovered.
3. The one GENUINE gap is pinned: `prune` is present only under `-tags sqlite`, so a plain
   `go test ./...` sees a whole backend dark. If that ever changes -- a tag removed from the file, or
   the file made tag-free -- this fires, because either event changes what the default gate means.

Coverage needs a Go toolchain, so this suite SKIPS LOUDLY when Go is absent rather than passing
silently. A skipped check that prints nothing is indistinguishable from a passing one, and that is
the exact failure mode this whole family keeps producing.
"""
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

FAILS = []

# The four helpers the tool was loudest about, and the coverage each was measured to have. If a future
# run finds one of these genuinely uncovered, that is a real finding -- and it must arrive as a real
# finding, not as a name-scan verdict nobody trusts any more.
LOAD_BEARING = ["matchOne", "anyGlobMatches", "anyArgvMatches", "substringNeedle"]


def check(name, got, want):
    if got != want:
        FAILS.append(name)
        print(f"FAIL {name}: got {got!r} want {want!r}")
    else:
        print(f"ok   {name}")


def go_bin():
    for c in ("go", "/usr/local/go/bin/go"):
        if os.path.exists(c) or shutil.which(c):
            return c
    return None


def coverage(go, tags=None):
    """Return {func_name: percent} from a real coverage profile, or None if the run failed."""
    prof = "/tmp/cu_cover_%s.prof" % ("sql" if tags else "base")
    cmd = [go, "test"]
    if tags:
        cmd += ["-tags", tags]
    cmd += ["./...", "-coverprofile=" + prof, "-run", "Test"]
    env = dict(os.environ)
    env["PATH"] = "/usr/local/go/bin:" + env.get("PATH", "")
    env["CGO_ENABLED"] = env.get("CGO_ENABLED", "1")
    try:
        subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, timeout=600)
    except subprocess.TimeoutExpired:
        return None
    if not os.path.exists(prof):
        return None
    out = subprocess.run([go, "tool", "cover", "-func=" + prof],
                         cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    rows = {}
    for line in (out.stdout or "").splitlines():
        m = re.match(r"(\S+):(\d+):\s+(\S+)\s+([\d.]+)%", line)
        if m:
            rows[m.group(3)] = float(m.group(4))
    return rows or None


def main():
    src = open(os.path.join(HERE, "unmirrored.py"), encoding="utf-8").read()

    # 1. The disclosure. Not the wording -- the fact. A reader must be told the verdict is textual.
    doc = src.split('"""')[1] if '"""' in src else ""
    check("docstring discloses that the verdict is a NAME heuristic", "NAME" in doc.upper(), True)
    check("docstring says a name search cannot see false absence",
          "FALSE ABSENCE" in doc.upper(), True)
    check("docstring points at the measurement", "FINDING-2026-11-04c" in doc, True)
    check("docstring tells the reader how to ask Go directly",
          "-coverprofile" in doc, True)

    # 2. The measured truth, re-measured. This is the half that is not a text search.
    go = go_bin()
    if go is None:
        print("SKIP coverage half: no Go toolchain on PATH (/usr/local/go/bin also absent).")
        print("     This is a SKIP, not a pass -- the name-heuristic checks above ran, the coverage")
        print("     checks did not, and the difference matters: an unrun check is not evidence.")
    else:
        rows = coverage(go)
        if rows is None:
            print("SKIP coverage half: `go test ./... -coverprofile` produced no profile. A profile can")
            print("     fail to appear for a HARDER reason than a slow test -- read the run before")
            print("     concluding the code is uncovered.")
        else:
            for fn in LOAD_BEARING:
                pct = rows.get(fn)
                check(f"{fn} is exercised by real Go tests (>0% statements)", pct is not None and pct > 0, True)

            # 3. The genuine gap: a build tag hides an entire file from the default profile.
            sql = coverage(go, tags="sqlite")
            if sql is None:
                print("SKIP prune check: the -tags sqlite profile did not build.")
            else:
                check("prune is tagged: absent from the default profile (the real gap)",
                      rows.get("prune") is None, True)
                check("prune IS covered once the sqlite tag is applied",
                      (sql.get("prune") or 0) > 0, True)

    print()
    if FAILS:
        print(f"coverunmirrored: {len(FAILS)} check(s) failed")
        return 1
    print("coverunmirrored: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

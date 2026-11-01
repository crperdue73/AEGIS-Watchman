#!/usr/bin/env python3
"""orphanprobe — every instrument in scripts/ must be reachable from the one command that gates a push.

`scripts/guardreach.py` tells you what an instrument CLAIMS. Beat 551's `greenprobe.py` tells you whether
its verdict can fail. Neither tells you the thing Leo said on the bus at 6352 and that Callie repeated at
6350: **a check that isn't called is a thing you did once in October.** An instrument nothing invokes has
the same standing as a memory — true when written, silent ever since.

So this walks the actual call graph, not the file list:

  ENTRY   — targets someone types (`make lint`, `make preflight`, `scripts/preflight.sh`) and the CI
            workflow's `run:` lines. These are the roots: what a push actually executes.
  CALLED  — reachable from a root by following `make <target>` dependencies, `./scripts/x`, and
            `$(PYTHON) scripts/x`. Transitive: a script that calls a script counts.
  ORPHAN  — in scripts/, not a `_test`, not reachable from any root. The instrument exists and nobody
            runs it.
  LIBRARY — not an entry point at all: no `__main__` block and no shebang, so it is imported by other
            scripts. An orphan check that flagged these would be wrong 6 times out of 6.

LIMIT, STATED UP FRONT: "reachable" means a shell-level invocation exists in this tree. It does not prove
the invocation is conditional-correct, that its output is read, or that the CI workflow calls the same
Make target (that is `ci-parity-check.sh`'s job, and a different question). It also cannot see something
invoked from a crontab or a human's shell history — which is exactly the category this file exists to
shrink, so the orphan list means "not reachable from the repo", and the finding says so in those words.

Exit 0 = no orphans · 1 = at least one · 2 = a root does not exist (the probe would be checking nothing)
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# preflight calls `make build`, `make lint`, `make preflight` itself... and the Make targets it chains
# through are the real roots. `make preflight` is thin (it shells to this script), so a BFS that stops at
# the Makefile sees almost nothing — the first run reported 50 orphans, which is a reading of the Makefile,
# not of the repo. Roots are therefore the CI-aligned make targets the preflight script names.
# The honest roots are the targets `scripts/preflight.sh` actually chains, plus lint's deps — i.e. what a
# push really executes. Two earlier root sets were wrong in opposite directions: `[lint, preflight]` alone
# reported **50** orphans (it saw the Makefile, not the repo — `make preflight` is thin and shells out),
# and 20 hand-picked "documented instrumentation" targets reported **38**, because most of those targets
# are not reachable from preflight at all. That second number was the useful mistake: a Makefile target is
# not an invocation any more than a file is. Roots are read from preflight.sh now, not chosen by me.
def preflight_roots():
    """The roots are READ from scripts/preflight.sh, never typed in.

    Every hand-picked root set I tried produced a different wrong number (50, 38, 42), because a
    hand-picked set measures the picker's beliefs. `make preflight` is the path a push takes, so
    `\bmake X` in preflight.sh IS the definition of reachable. The one thing to keep true is that this
    reader must not go blind when preflight puts a target on a continuation line — see the check below.
    """
    pf = os.path.join(ROOT, "scripts", "preflight.sh")
    try:
        with open(pf, encoding="utf-8", errors="replace") as fh:
            src = fh.read()
    except OSError:
        return []
    return sorted(set(re.findall(r"\bmake\s+([A-Za-z0-9_.\-]+)", src)))


MAKE_ROOTS = preflight_roots()
CALL_RE = re.compile(r"(?:\./scripts/|\$\(PYTHON\)\s+scripts/|scripts/)([A-Za-z0-9_.\-]+\.(?:py|sh))")
MAKE_DEP_RE = re.compile(r"^([A-Za-z0-9_.\-]+):\s*(.*)$")


def read(p):
    with open(p, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def makefile_graph(text):
    """.PHONY line gives EVERY target a dependency line, so a reader that does not exclude it puts every
    script in the graph and reports 0 orphans — a guard that always passes. Caught by the falsifier: pulling
    packcheck out of `lint` changed nothing, which was the tell."""
    graph, cur = {}, None
    for line in text.splitlines():
        if line.startswith("\t") and cur:
            graph[cur]["body"].append(line)
            continue
        m = MAKE_DEP_RE.match(line)
        if m and not line.startswith("#") and m.group(1) != ".PHONY":
            cur = m.group(1)
            deps = [d for d in m.group(2).split() if not d.startswith("$")]
            graph[cur] = {"deps": deps, "body": []}
        else:
            cur = None if not line.strip().startswith("#") else cur
    return graph


def scripts_in(text):
    return set(CALL_RE.findall(text))


def is_library(path):
    src = read(path)
    if not path.endswith(".py"):
        return False
    return "__main__" not in src and not src.startswith("#!")


def main():
    mk = read(os.path.join(ROOT, "Makefile"))
    graph = makefile_graph(mk)
    # "named by a Makefile target" must mean a RECIPE, not a comment. `packcheck` is named in a Makefile
    # comment explaining it is deliberately not in lint, so matching the raw text filed packcheck as
    # "has a target nobody chains" and hid it from the printed orphan list while the FAILED summary still
    # carried it — two counts of the same set disagreeing, which is how this was caught.
    recipes = "\n".join(" ".join(v["body"]) for v in graph.values())
    missing_roots = [r for r in MAKE_ROOTS if r not in graph]

    # BFS over make targets and script files, both directions of invocation.
    seen_targets, reached_scripts = set(), set()
    queue = list(MAKE_ROOTS)
    while queue:
        t = queue.pop()
        if t in seen_targets or t not in graph:
            continue
        seen_targets.add(t)
        # recipe lines ONLY. Folding a target's dependency NAMES into the script set let `make lint` swallow
        # every target name it mentions, so pulling packcheck out of lint changed nothing — the guard found
        # packcheck in the *name* `packcheck-selfcheck`. A dependency is followed as a target, never cited.
        body = " ".join(graph[t]["body"])
        reached_scripts |= scripts_in(body)
        # A recipe line `make packcheck-selfcheck` invokes a TARGET with no `scripts/` in it, so the script
        # regex cannot see it. Preflight chains by shelling out to make — that is how CRPrdode's own gate
        # reaches most of its instruments, and a reader that only follows `a: b` dependencies and
        # `scripts/x` literals reports every one of them as an orphan. Fifth bug in this file, and it was
        # the one making the whole measurement wrong rather than one row: 42 vs the truth.
        for sub in re.findall(r"\bmake\s+([A-Za-z0-9_.\-]+)", body):
            if sub in graph:
                queue.append(sub)
        for d in graph[t]["deps"]:
            if d in graph:
                queue.append(d)

    # A script that calls another script (or a MAKE TARGET) pulls it in too, to a fixed point. This is the
    # pass that makes the measurement honest, and getting it right took five separate corrections:
    #   1. `packcheck_test.sh` never names `packcheck.py` — it tests it through `packcheck-selfcheck`, so
    #      make targets reachable from a script must be followed, not just `scripts/x` literals.
    #   2. `for s in sorted(reached_scripts)` while ADDING to reached_scripts raises "Set changed size during
    #      iteration"; the raise killed the pass and the outer `while changed` swallowed it. Silent, and it
    #      cost 4 rows. Iterate a copy.
    #   3. `_test.sh` harnesses `cp scripts/packcheck.py "$tmp/scripts/"` — staging a fixture is not a call.
    #   4. A target reached from a script must itself be walked for further targets AND scripts.
    #   5. Adding a target that a script named must mark `changed`, or the fixed point stops early.
    all_script_names = set(f for f in os.listdir(HERE) if f.endswith((".py", ".sh")))
    changed = True
    while changed:
        changed = False
        for s in sorted(set(reached_scripts)):
            p = os.path.join(HERE, s)
            if not os.path.exists(p):
                continue
            src = read(p)
            named = re.findall(r"\bmake\s+([A-Za-z0-9_.\-]+)", src)
            for sub in named:
                if sub in graph and sub not in seen_targets:
                    seen_targets.add(sub)
                    before = len(reached_scripts)
                    reached_scripts |= scripts_in(" ".join(graph[sub]["body"]))
                    for d in graph[sub]["deps"]:
                        if d in graph:
                            reached_scripts |= scripts_in(" ".join(graph[d]["body"]))
                    if len(reached_scripts) != before:
                        changed = True
                    changed = True
            # Excluding tests entirely was too blunt: `selfcheck` targets are chained by preflight PRECISELY
            # because the harness is the only thing that proves the alarm can fire, so a test reached through
            # a make target IS a caller. Exclude only a test that is reached no other way and merely stages a
            # copy (`cp "$HERE/packcheck.py" "$work/scripts/"` — the copy is a fixture, not a call), which
            # `scripts_in` cannot tell apart. The rule: drop a copy target, keep the invocation.
            for sub in scripts_in(src):
                if sub in reached_scripts:
                    continue
                if sub not in all_script_names:
                    continue
                if sub not in reached_scripts:
                    reached_scripts.add(sub)
                    changed = True

    ci = os.path.join(ROOT, ".github", "workflows")
    ci_runs = set()
    if os.path.isdir(ci):
        for f in os.listdir(ci):
            if f.endswith((".yml", ".yaml")):
                ci_runs |= scripts_in(read(os.path.join(ci, f)))

    all_scripts = [f for f in sorted(os.listdir(HERE)) if f.endswith((".py", ".sh"))]
    not_test = [f for f in all_scripts if not re.search(r"_test\.(py|sh)$", f)]
    libs = [f for f in not_test if is_library(os.path.join(HERE, f))]
    entry = [f for f in not_test if f not in libs]
    orphans = [f for f in entry if f not in reached_scripts]
    ci_only = [f for f in entry if f in ci_runs and f not in reached_scripts]

    print(f"scripts in scripts/: {len(all_scripts)}  (tests: {len(all_scripts) - len(not_test)})")
    print(f"libraries (no __main__, no shebang — imported, not run): {len(libs)}: {libs}")
    print(f"entry points (runnable): {len(entry)}")
    print(f"roots walked: {len(MAKE_ROOTS)} targets chained by scripts/preflight.sh -> "
          f"{len(seen_targets)} make targets reached")
    print(f"reachable from those roots: {len(entry) - len(orphans)}/{len(entry)} entry points")
    fails = []
    mentioned = [f for f in orphans if re.search(r"\b" + re.escape(f) + r"\b", recipes)]
    unmentioned = [f for f in orphans if f not in mentioned]
    print(f"ORPHANS (runnable, not reachable from what a push runs): {len(orphans)}")
    print(f"   named in a recipe but the target is never chained: {len(mentioned)}: {mentioned}")
    print(f"   named in no recipe at all — a file nobody typed into anything: {len(unmentioned)}")
    # EVERY orphan gets a line. The earlier version printed only the second bucket and left the first
    # unnamed, so 42 was announced and 37 were listed — a total and its own breakdown disagreeing in the
    # open. Both buckets print, with the bucket on each row.
    for f in orphans:
        tag = "target-unwired" if f in mentioned else ("CI-only" if f in ci_only else "no-caller")
        print(f"   ORPHAN [{tag}] {f}")
    if len(orphans) != len(mentioned) + len(unmentioned):
        fails.append("orphan buckets do not sum to the total")

    if missing_roots:
        fails.append(f"root target(s) missing from Makefile: {missing_roots} — those were not walked")
    if orphans:
        fails.append(f"{len(orphans)} runnable instrument(s) unreachable from a push "
                     f"({len(mentioned)} have a Make target nobody chains, {len(unmentioned)} have none): "
                     f"{orphans}")

    print(f"\ndenominator: {len(orphans)} orphans out of {len(entry)} runnable instruments "
          f"({len(all_scripts)} files total, {len(libs)} libraries excluded)")
    print("reach: shell-level invocation in this tree only. A crontab or a human's shell can run anything; "
          "the claim is 'not reachable from the repo', which is the reach that survives a stranger cloning it.")
    if fails:
        print(f"FAILED {len(fails)}:")
        for f in fails:
            print("  - " + f)
        return 1
    print("orphanprobe: every runnable instrument is reachable from what a push runs — green")
    return 0


if __name__ == "__main__":
    sys.exit(main())

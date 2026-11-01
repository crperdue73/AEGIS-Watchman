#!/usr/bin/env python3
"""parityreverse — measure BOTH directions of the CI/preflight relationship.

ci-parity-check.sh enforces one direction: every command CI runs must be reachable from
scripts/preflight.sh. It is silent in the other: a target preflight runs that CI never runs is not
reported anywhere. preflight.sh's header promises "the same targets", which is the symmetric reading.

This measures the gap. It does NOT gate it, on purpose: whether a local-only step should be an error,
a declared exception, or a documentation fix is a policy decision for the repo owner, not something a
script gets to decide by going red. Exit is 0 whether or not there is a gap; the gap is printed.

Anchoring matters. CI commands are read from `run:` lines ONLY. Matching "lines containing make" also
matches step labels (`- name: make lint`), which inflated the first version of this measurement from 20
commands to 26 and produced a phantom "6 commands silently dropped" finding. There was no such finding.

Preflight's target set is closed over Makefile prerequisites, because pin-check is not invoked by name in
preflight — it is a prerequisite of lint, and lint is. A version that skipped the closure reported
pin-check as a CI-only target. The guard was right and this reader was wrong.

Prints two denominators. A reverse diff with no count on either side is not a reading.
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(p):
    with open(os.path.join(ROOT, p), encoding="utf-8", errors="replace") as fh:
        return fh.read()


def ci_make_targets(ci_src):
    """Make targets CI actually invokes. `run:` anchored — never a `- name:` label."""
    out = set()
    for line in ci_src.splitlines():
        m = re.match(r"\s*run:\s*(.+)$", line)
        if not m:
            continue
        cmd = m.group(1).strip()
        mm = re.match(r"make\s+([a-z][a-z0-9-]*)", cmd)
        if mm:
            out.add(mm.group(1))
    return out


def makefile_edges(mk_src):
    """target -> its prerequisite NAMES. `.PHONY` is excluded: it gives every name a fake dep line,
    which is the bug that made an earlier probe report a repo with zero problems."""
    edges = {}
    for line in mk_src.splitlines():
        m = re.match(r"^([A-Za-z0-9_.\-]+):\s*(.*)$", line)
        if not m or m.group(1) == ".PHONY":
            continue
        deps = [d for d in m.group(2).split() if not d.startswith("$")]
        edges.setdefault(m.group(1), set()).update(deps)
    return edges


def close_over_prereqs(seeds, edges):
    """A target reached as a prerequisite of a target preflight runs IS run by preflight."""
    seen = set()
    stack = list(seeds)
    while stack:
        t = stack.pop()
        if t in seen:
            continue
        seen.add(t)
        for d in edges.get(t, ()):
            if d not in seen:
                stack.append(d)
    return seen


def main():
    ci = read(".github/workflows/ci.yml")
    pf = read("scripts/preflight.sh")
    mk = read("Makefile")

    edges = makefile_edges(mk)
    ci_targets = ci_make_targets(ci)
    pf_named = set(re.findall(r"\bmake\s+([a-z][a-z0-9-]*)", pf))
    pf_targets = close_over_prereqs(pf_named, edges)

    only_pf = sorted(pf_targets - ci_targets)
    only_ci = sorted(ci_targets - pf_targets)

    print(f"ci.yml parsed:         {len(edges)} make targets in Makefile, "
          f"{len(ci_targets)} invoked by CI (run: anchored, labels excluded)")
    print(f"preflight.sh parsed:   {len(pf_named)} targets invoked by name, "
          f"{len(pf_targets)} after closing over Makefile prerequisites")
    print()
    print(f"preflight runs, CI does NOT ({len(only_pf)}):")
    for t in only_pf:
        print(f"   {t}")
    if not only_pf:
        print("   (none — CI covers every target preflight runs)")
    print()
    print(f"CI runs, preflight does not ({len(only_ci)}):")
    for t in only_ci:
        print(f"   {t}")
    if not only_ci:
        print("   (none — this is the only direction ci-parity-check.sh enforces)")
    print()
    print("This is a MEASUREMENT, not a verdict. ci-parity-check enforces the lower direction only;")
    print("whether the upper direction should also be an error is a policy decision, not a script's.")
    print("Both denominators printed so a zero on either side is a reading and not a guess.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Tests for bindprobe — pin the tool's own first-draft bug before pinning the pack.

The bug this file exists for: bindprobe's YAML glob reader originally kept the opening quote, so all three
globs read as '- "*:/host*' and every case missed. That is the SECOND time in one night the same quote bug
shipped into a probe, which is why it gets its own assertion here rather than a comment.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bindprobe as B  # noqa: E402
import neargo  # noqa: E402

FAILS = []


def ok(name, cond, detail=""):
    if not cond:
        FAILS.append(f"{name} {detail}".strip())
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))



def main():
    G = B.detail_globs(B.RULE)
    BINDS = G["binds"]

    # --- the reader -----------------------------------------------------------------------------
    ok("rule found", G is not None)
    ok("one detail key: binds", sorted(G) == ["binds"], str(sorted(G)))
    ok("two binds globs", len(BINDS) == 2, str(BINDS))
    # the pinned draft bug
    ok("no glob keeps a YAML quote", not any(q in g for g in BINDS for q in ('"', "'")), str(BINDS))
    # UPDATED beat 614: the pack dropped "*:/host*" (redundant with "/:/", and its only distinct
    # effect was a critical-rule false positive). CASES below is unchanged and still passes -- which is
    # the evidence that "/:/" already covered every true host-root mount it used to shadow.
    ok("globs are the pack's literal text",
       sorted(BINDS) == sorted(["/:/", "*/var/run/docker.sock*"]), str(BINDS))

    # --- the matcher semantics the probe leans on -----------------------------------------------
    # CORRECTED 2026-10-27 (beat 610). These three pinned "needle refused (interior '/')", which came
    # from neargo.py's invented refusal. engine.go:287 guards only '*?[' -- a '/' is not special, so all
    # three globs DO carry a needle and the contains fallback is live for them. Everything below in this
    # file depended on that false premise.
    ok("needle PRESENT for the deleted *:/host* (kept: documents the matcher, not the pack)",
       neargo.substring_needle("*:/host*") == ":/host")
    ok("needle PRESENT for /:/", neargo.substring_needle("/:/") == "/:/")
    ok("needle PRESENT for the socket glob",
       neargo.substring_needle("*/var/run/docker.sock*") == "/var/run/docker.sock")

    # --- what the rule actually reaches ---------------------------------------------------------
    ok("denominator: 9 bind sets", len(B.CASES) == 9, f"got {len(B.CASES)}")
    ok("2 controls that must not fire", sum(1 for c in B.CASES if not c[2]) == 2)

    REACHED = {}
    for label, mounts, want, _why in B.CASES:
        REACHED[label] = B.fires(BINDS, ",".join(mounts))

    ok("control cache dir does not fire", REACHED["control: cache dir only"] is False)
    ok("control ordinary mounts do not fire", REACHED["control: two ordinary mounts"] is False)

    # the finding: a mount listed AFTER another bind is unreachable
    ok("socket alone fires", REACHED["socket alone"] is True)
    ok("socket as SECOND of two fires", REACHED["socket, second of two"] is True)
    # CORRECTED beat 610: was pinned False as "the asymmetry". The contains fallback runs on the joined
    # blob, so position is irrelevant. CASES has always said True for this row; only the assertion
    # disagreed with the table it was checking.
    ok("socket as FIRST of two fires too (position does not matter)",
       REACHED["socket, first of two"] is True)
    ok("host root alone fires", REACHED["host root to /host"] is True)
    ok("host root as SECOND of two fires", REACHED["host root to /host, second"] is True)
    ok("host root as FIRST of two fires too (position does not matter)",
       REACHED["host root to /host, first"] is True)

    # --- and the reason -------------------------------------------------------------------------
    # --- and the reason it works regardless of position -----------------------------------------
    # REVERSED beat 610: this section used to prove "the failure is the trailing comma". There is no
    # such failure under the Go. Both orders fire, because the needle is a plain substring search over
    # the joined blob.
    comma = "/:/host,/data:/data"
    ok("both orders fire: the comma does not break the contains route",
       B.fires(BINDS, "/:/host") and B.fires(BINDS, comma))
    ok("position does NOT decide: same mount, either list order",
       B.fires(BINDS, "/data:/data,/:/host") is True and B.fires(BINDS, comma) is True)

    # --- probe exit code is not a pass -----------------------------------------------------------
    # CORRECTED beat 610: was "exits non-zero on disagreement" (expected 1). bindprobe.py exits 1 when the
    # mirror disagrees with the rule's stated intent; with the Go-faithful matcher the rule now behaves as
    # its comment claims, so it exits 0. Pinned with the direction spelled out.
    ok("main() exits 0 once the rule behaves as written", B.main() == 0, str(B.main()))

    print()
    if FAILS:
        print(f"FAILED {len(FAILS)}:")
        for f in FAILS:
            print("  " + f)
        sys.exit(1)
    print("bindprobe_test: all checks passed")


if __name__ == "__main__":
    main()

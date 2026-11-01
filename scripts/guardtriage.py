#!/usr/bin/env python3
"""guardtriage — classify the guards that beat 549 flagged, instead of just counting them.

Beat 549 printed "26 guards without a denominator" and stopped. Twenty-six names is not a work list: one
of them shells out to `make build && go test ./...` and has nothing a count could describe, another prints
"37 findings" and was only missed by a pattern. The number was a count of the PATTERN, reported as a count
of DEFECTS — the exact error this whole campaign is about.

This file does not fix anything. It sorts the 26 into four tiers, prints the classification, and exits 1
if any guard that carries a denominate-able job is UNCLASSIFIED, so no guard quietly leaves the work list.

Tiers (see docs/FINDING-2026-10-24-guardtriage.md):
  T1  a count exists and is not printed  -> a real R1 defect, fixable today
  T2  nothing to count (orchestration)   -> needs one sentence of coverage, not a number
  T3  test harness; the count is the caller's -> print len(FAILS)-adjacent total
  T4  gated off this host (no Go/Docker/clang) -> an honest refusal, not a defect

Exit 0 every flagged guard classified · 1 at least one unclassified · 2 a guard vanished
"""

import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def load_guardreach():
    spec = importlib.util.spec_from_file_location("guardreach", os.path.join(HERE, "guardreach.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


T1 = {
    "docsindex.py": "walks finding docs; prints a count the linter's pattern missed",
    "dispose.py": "walks notes lacking a disposition",
    "check-links.py": "walks relative links",
    "fieldcheck.py": "walks pack fields",
    "eventcheck.py": "walks events",
    "exeprobe.py": "walks exe_any values",
    "crossprobe.py": "walks cross-rule pairs",
    "driftprobe.py": "walks drift samples",
    "flagprobe.py": "walks flags",
    "fpprobe.py": "walks fixtures",
    "bindprobe.py": "walks bind actions",
}
# T1b — real R1 gaps the first draft of this table MISSED. Found by the UNCLASSIFIED check going red on
# 8 guards, which is the check doing exactly its job. Each of these walks a set and reports a verdict
# only, so each is a genuine "count exists, not printed".
T1.update({
    "blindcheck.sh": "runs fixtures; prints '(5 defects asserted)' but says nothing about how many fixtures "
                     "it ran — a count in the wrong place reads as a count",
    "deps-report.sh": "walks the dependency list; prints a verdict, never the number of modules",
    "pkgcheck.py": "walks go.mod + identifiers; prints per-finding lines but no denominator",
    "publish-scan.sh": "walks tracked files and unpushed commits; prints PASS with no counts",
    "tokruns.py": "walks token runs",
})
T2 = {
    "clone-check.sh": "runs make build / go test / demo; reach is the whole tree, stated in its PASS line",
    "cut-release.sh": "acts on one version tag",
    "cut-release-check.sh": "acts on one version tag",
    "preflight.sh": "orchestration wrapper",
    "fuzz.sh": "orchestration wrapper",
    "quickstart-check.sh": "orchestration wrapper",
}
# T2b — nothing to count: a checker whose subject IS one file, or a scan that reports via exit code.
T2.update({
    "license-check.sh": "checks one file (LICENSE) for one property; a count of 1 is theatre",
    "publish-scan-check.sh": "test harness for publish-scan; its coverage is the parent's",
})

T3 = {
    "batchprobe_test.py": "test harness; count belongs to the parent runner",
    "bindprobe_test.py": "test harness",
    "clockprobe_test.py": "test harness",
    "crossprobe_test.sh": "test harness",
    "driftprobe_test.py": "test harness",
    "eventcheck_test.py": "test harness",
    "exeprobe_test.py": "test harness",
    "fieldcheck_test.py": "test harness",
    "flagprobe_test.sh": "test harness",
    "fpprobe_test.sh": "test harness",
    "neargo_test.py": "test harness",
    "needleprobe_test.sh": "test harness",
    "packcheck_test.sh": "test harness",
    "packcover_test.sh": "test harness",
    "pathdiff_test.py": "test harness",
    "pkgcheck_test.sh": "test harness",
    "predaudit_test.py": "test harness",
    "renametrace_test.py": "test harness",
    "segmentaudit_test.py": "test harness",
    "severityprobe2_test.py": "test harness",
    "starveprobe_test.py": "test harness",
    "subjectprobe_test.sh": "test harness",
    "suffixprobe_test.py": "test harness",
    "tokruns_test.py": "test harness",
    "unlinkprobe_test.py": "test harness",
    "wirecheck_test.py": "test harness",
}
# T4 append (neargo.py has no printed output at all — it is a library, judged by its test file)
T4 = {
    "ci-parity-check.sh": "needs Go + make; absent on this host since 2026-10-08",
    "ci-parity-selfcheck.sh": "needs Go + make; absent on this host",
    "ebpf-preflight.sh": "needs clang + libbpf-dev; absent on this host",
    "container-check.sh": "needs Docker; absent on this host",
}


LIBRARY = {"neargo.py": "imported by neargo_test.py; prints nothing by design — its reach is the test"}


def main():
    gr = load_guardreach()
    guards = [f for f in sorted(os.listdir(HERE)) if f.endswith((".py", ".sh"))]
    print(f"guards read: {len(guards)} files in scripts/")

    flagged = []
    for f in guards:
        d, m, s, bad, n = gr.judge(gr.read(os.path.join(HERE, f)))
        if not d:
            flagged.append(f)

    print(f"flagged by guardreach (no denominator): {len(flagged)}")

    # Tiers are computed for EVERY guard, not only the flagged ones. The first version tiered only the
    # flagged set and then printed "T1 holds {len(tiers['T1'])}" — which gave 5 when the table held 16,
    # because 11 T1 guards were never flagged by guardreach in the first place. Two different questions
    # ("who is red" and "who belongs in T1") were sharing one list. Both are reported; neither is bent.
    tiers = {"T1": [], "T2": [], "T3": [], "T4": [], "T5": [], "OK": [], "UNCLASSIFIED": []}
    for f in guards:
        if f == "guardreach.py" or f == "guardtriage.py":
            continue
        for name, table in (("T1", T1), ("T2", T2), ("T3", T3), ("T4", T4), ("T5", LIBRARY)):
            if f in table:
                tiers[name].append(f)
                break
        else:
            # a guard with no tier that DOES print a denominator is not unclassified — it is done.
            tiers["OK" if f not in flagged else "UNCLASSIFIED"].append(f)

    for name in ("T1", "T2", "T3", "T4", "T5", "UNCLASSIFIED"):
        print(f"\n{name} — {len(tiers[name])} guard(s)")
        for f in tiers[name]:
            why = (T1 | T2 | T3 | T4 | LIBRARY).get(f, "no tier carries this guard")
            print(f"   {f:<28} {why}")

    print(f"\nOK — {len(tiers['OK'])} guard(s) print a denominator and carry no tier "
          f"(not work; counted so the four tiers plus OK equal every file)")

    fails = []
    seen = {}
    for name, table in (("T1", T1), ("T2", T2), ("T3", T3), ("T4", T4), ("T5", LIBRARY)):
        for f in table:
            if f in seen:
                fails.append(f"{f} is in both {seen[f]} and {name} — first match wins silently, so a "
                             f"duplicate moves work between tiers without changing any total")
            seen[f] = name
    if tiers["UNCLASSIFIED"]:
        fails.append(f"{len(tiers['UNCLASSIFIED'])} guard(s) classified as neither defect nor refusal: "
                     f"{tiers['UNCLASSIFIED']}")
    # every tier table entry must name a guard that still exists, else the triage is describing files
    # that were renamed or deleted and the counts above are fiction.
    missing = [f for f in set(T1) | set(T2) | set(T3) | set(T4) | set(LIBRARY) if f not in guards]
    if missing:
        fails.append(f"triage names {len(missing)} guard(s) that no longer exist: {missing}")

    print()
    covered = sum(len(tiers[k]) for k in ("T1", "T2", "T3", "T4", "T5", "OK", "UNCLASSIFIED"))
    if covered + 2 != len(guards):   # +2 = guardreach.py and guardtriage.py, skipped as instruments
        fails.append(f"tiers cover {covered} guards but {len(guards)} files exist (-2 instruments): "
                     f"every guard must have a home")
    print(f"denominator: {len(flagged)} flagged guards judged against "
          f"{len(T1)}+{len(T2)}+{len(T3)}+{len(T4)}+{len(LIBRARY)} tiered names ({len(guards)} files walked) "
          f"— tier tables may name a guard that is not flagged, which is how a guard is retiered without "
          f"leaving the list")
    if fails:
        print(f"FAILED {len(fails)}:")
        for f in fails:
            print("  - " + f)
        sys.exit(1)
    in_t1 = len(tiers["T1"])  # every guard in T1, flagged or not

    # BUG FIXED HERE (beat 557). This line used to read
    #     "{len(flagged)} flagged = {T1} + {T2} + {T3} + {T4} + {T5} — green"
    # and those numbers cannot be equal for a reason no amount of care would fix: `flagged` is a subset
    # of the walk, while every tier is computed over the WHOLE walk (that widening is the fix from beat
    # 550, a few lines up). So it printed "26 flagged = 16 + 8 + 26 + 4 + 1", which sums to 55, beside a
    # claim of 26 — a total disagreeing with its own breakdown, in one sentence, and exiting 0. The tell
    # is that T3 alone equalled the total: 26 = 26 is the arithmetic of two populations sharing a label.
    #
    # The fix is not to change the tiers. It is to stop writing an equation between two different
    # questions. Each tier now reports how many of its members guardreach flagged, and the check that
    # can go red is that those per-tier flagged counts sum to `flagged`.
    per_tier_flagged = {name: len(set(members) & set(flagged)) for name, members in tiers.items()}
    n_tier_flagged = sum(per_tier_flagged.values())
    if n_tier_flagged != len(flagged):
        print(f"FAILED: per-tier flagged counts sum to {n_tier_flagged}, but guardreach flagged "
              f"{len(flagged)}. A guard is missing from the tier tables, or is in two of them.")
        sys.exit(1)
    print(f"guard triage: {len(flagged)} flagged, all accounted for across the tier tables — green")
    for _name in ("T1", "T2", "T3", "T4", "T5", "OK"):
        _members = len(tiers[_name])
        _fl = per_tier_flagged[_name]
        if _name == "OK":
            print(f"     {_name:<3} {_members:2d} guard(s), {_fl} flagged")
        else:
            print(f"     {_name:<3} {_members:2d} guard(s) ({_fl} of them flagged)")
    tiered_total = (in_t1 + len(tiers['T2']) + len(tiers['T3']) + len(tiers['T4']) + len(tiers['T5'])
                    + len(tiers['OK']))
    print(f"     tier totals: {in_t1}+{len(tiers['T2'])}+{len(tiers['T3'])}+{len(tiers['T4'])}+"
          f"{len(tiers['T5'])}+{len(tiers['OK'])} = {tiered_total}"
          f" tiered guards (walk is {len(guards)}); flagged {sum(per_tier_flagged.values())} of them")
    # The two instruments that do the judging are in the walk but deliberately not tiered (line 132 skips
    # them), so they have to be added back or the sum can never match. My first version of this check
    # forgot them and went red at 84-vs-86 — which is the check working, on me, within a minute of being
    # written. Name them explicitly rather than subtracting a magic 2.
    UNTIERED_BY_DESIGN = {"guardreach.py", "guardtriage.py"}
    missing = sorted(set(guards) - UNTIERED_BY_DESIGN - {g for m in tiers.values() for g in m})
    if missing:
        print(f"FAILED: {len(missing)} guard(s) in the walk are in no tier: {missing}")
        print(f"        tiered {tiered_total} + untiered-by-design {len(UNTIERED_BY_DESIGN & set(guards))}"
              f" does not cover the walk of {len(guards)}.")
        sys.exit(1)
    print(f"     accounted for: {tiered_total} tiered + {len(UNTIERED_BY_DESIGN & set(guards))} "
          f"untiered-by-design = {tiered_total + len(UNTIERED_BY_DESIGN & set(guards))} of {len(guards)} walked")
    # Two numbers, and the difference named. T1 holds 16 guards; only 5 of them were flagged by
    # guardreach. The other 11 are T1 for a DIFFERENT reason: their denominator exists and the linter
    # reads a message where it is phrased as code (`{checked} link(s)`) or is printed below a triple
    # quote. Collapsing those into "defects" would overstate the work; dropping them would lose it.
    unflagged_t1 = sorted(set(tiers["T1"]) - set(flagged))
    print(f"     T1 holds {in_t1} guards; {in_t1 - len(unflagged_t1)} were flagged by guardreach and "
          f"{len(unflagged_t1)} were not (linter phrasing miss, denominator already printed): "
          f"{unflagged_t1[:6]}{'…' if len(unflagged_t1) > 6 else ''}")
    print(f"     beat 549's '26' was a count of the PATTERN measured by one rule. Priced here: "
          f"{len(tiers['T1'])} need a printed count ({len(tiers['T1']) - len(unflagged_t1)} of them red "
          f"today), {len(tiers['T2'])}+{len(tiers['T3'])}+{len(tiers['T4'])}+{len(tiers['T5'])} do not, "
          f"and {len(tiers['UNCLASSIFIED'])} are unaccounted for.")
    sys.exit(0)


if __name__ == "__main__":
    main()

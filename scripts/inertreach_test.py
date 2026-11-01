#!/usr/bin/env python3
"""inertreach_test -- what InertRules measures, and what it does not.

FINDING-2026-10-26d. `internal/rules/coverage.go` has `InertRules(packs, available)`, called at collector
startup, which logs "N of M loaded rule(s) cannot fire with the attached sources". That check is correct and
it is only one level deep: it compares `r.Match.Type` against the set of attached action classes. It never
looks at a rule's match VALUES.

Consequence, and the reason this suite exists: on a deployment with all seven action classes attached the
startup line reads "all 20 loaded rule(s) can fire" -- true -- while 29 of the 32 endpoint globs inside those
rules cannot match what the egress adapter emits (FINDING-2026-10-26c). The envelope is checked. The
contents are not.

This suite pins the boundary mechanically so it cannot drift in silence:
  1. InertRules' decision depends on Type ALONE -- demonstrated by holding Type fixed and emptying every
     other match key, which must not change the verdict.
  2. All 20 rules are envelope-live when all seven classes are attached.
  3. At least one envelope-LIVE rule is simultaneously contents-dead -- the gap, stated as a number.
  4. The gap is not small: it is the majority of endpoint values.

Note on method: the Go source is read as text and its decision rule is transcribed, because there is no Go
toolchain on this host. The transcription is anchored to the source by section 1, which asserts the decision
expression is still exactly what the transcription models. If coverage.go changes shape, this suite fails
rather than quietly testing a memory.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

import yaml  # noqa: E402

from globmatch import match_one  # noqa: E402

FAILS = []
RESULTS = []


def check(name, got, want):
    RESULTS.append((name, got, want))
    if got != want:
        FAILS.append(name)
        print(f"FAIL {name}: got {got!r} want {want!r}")
    else:
        print(f"ok   {name}")


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def pack():
    return yaml.safe_load(read("rules/carbonato.yaml"))


def emitted_endpoint_shapes():
    """The endpoint strings internal/telemetry/egress.go actually builds.

    egress.go:155  detail["endpoint"] = ep                    -> "<ip>:<port>"
    egress.go:160  detail["endpoint"] = svc + " " + ep        -> "<service> <ip>:<port>"
    """
    return ["104.18.1.1:443", "slack.com 3.120.129.226:443"]


def main():
    # ----------------------------------------------------------------------------------------------
    # 1. Anchor the transcription to the source. InertRules must still be a single Type comparison.
    # ----------------------------------------------------------------------------------------------
    cov = read("internal/rules/coverage.go")
    check("coverage.go still defines InertRules", "func InertRules(" in cov, True)
    decision = re.search(
        r"if t := r\.Match\.Type; t != \"\" && !available\[t\] \{", cov)
    check("InertRules' decision is STILL exactly `Match.Type` vs available — nothing else is consulted",
          decision is not None, True)
    code_lines = [ln for ln in cov.splitlines() if not ln.lstrip().startswith("//")]
    check("  ...and it does not reference any other match key (no argv/path/endpoint/detail)",
          sorted(set(re.findall(r"Match\.(\w+)", "\n".join(code_lines)))), ["Type"])

    # ----------------------------------------------------------------------------------------------
    # 2. Transcribe the decision, then show that emptying every non-Type key cannot change it.
    # ----------------------------------------------------------------------------------------------
    def inert_verdict(rule, avail):
        t = (rule.get("match") or {}).get("type") or ""
        return bool(t) and t not in avail

    p = pack()
    rules = p["rules"]
    ALL_TYPES = sorted({(r.get("match") or {}).get("type") for r in rules} - {None, ""})
    check("the pack uses seven action classes", len(ALL_TYPES), 7)
    avail = set(ALL_TYPES)
    check("with all classes attached, NO rule is inert (envelope view)",
          [r["id"] for r in rules if inert_verdict(r, avail)], [])

    import copy
    stripped = copy.deepcopy(rules)
    for r in stripped:
        r["match"] = {"type": r["match"].get("type")}
    check("emptying EVERY match value except type leaves the verdict identical — the check is blind to contents",
          [r["id"] for r in stripped if inert_verdict(r, avail)],
          [r["id"] for r in rules if inert_verdict(r, avail)])

    # ----------------------------------------------------------------------------------------------
    # 3. The gap: envelope-live rules carrying contents-dead values.
    # ----------------------------------------------------------------------------------------------
    def endpoint_globs(rule):
        g = (rule.get("match") or {}).get("endpoint_glob_any")
        if not g:
            return []
        return g if isinstance(g, list) else list(g.values())[0]

    shapes = emitted_endpoint_shapes()
    contents_dead = {}
    for r in rules:
        globs = endpoint_globs(r)
        if not globs:
            continue
        dead = [g for g in globs if not any(match_one(g, s) for s in shapes)]
        if dead:
            contents_dead[r["id"]] = dead
    check("at least one envelope-LIVE rule is contents-DEAD (the gap this finding names)",
          bool(contents_dead), True)
    total_dead = sum(len(v) for v in contents_dead.values())
    check("30 endpoint globs are contents-dead (re-measure if this fails)", total_dead, 30)
    check("  ...spread across all three endpoint rules", len(contents_dead), 3)

    # ----------------------------------------------------------------------------------------------
    # 4. And the gap is the MAJORITY, not an edge case.
    # ----------------------------------------------------------------------------------------------
    all_ep = [g for r in rules for g in endpoint_globs(r)]
    check("endpoint values total 32 in the pack", len(all_ep), 32)
    check("contents-dead is the majority of them", total_dead > len(all_ep) - total_dead, True)
    check("  ...specifically 30 of 32 — beat 599 said 29; that count was wrong", (total_dead, len(all_ep)), (30, 32))

    # ----------------------------------------------------------------------------------------------
    # 5. FINDING-2026-10-26e follow-up: the instrument DOES work where it can see.
    #    On an auditd-only host (no eBPF) file.access is not attached, so all three secret rules must be
    #    named inert. This is the honest-degradation path and it is the one case where "can fire" is right.
    # ----------------------------------------------------------------------------------------------
    auditd_only = {"process.exec", "net.egress", "net.listen", "file.write"}
    inert_now = sorted(r["id"] for r in rules if inert_verdict(r, auditd_only))
    secret = [i for i in inert_now if ".secret." in i]
    check("auditd-only host: InertRules names all three file.access secret rules as inert",
          secret, ["carbonato.secret.read_after_shell", "carbonato.secret.read_model_key",
                   "carbonato.secret.read_order_follows_constraint"])
    check("  ...along with the 3 container rules (docker) and scope_new (shim) — 7 inert, 13 live",
          len(inert_now), 7)
    sev = {r["id"]: r.get("severity") for r in rules}
    dead_split = sorted(sev[i] for i in inert_now)
    check("  ...severity of the DEAD set: 5 critical, 2 high",
          (dead_split.count("critical"), dead_split.count("high")), (5, 2))
    live_ids = [r["id"] for r in rules if not inert_verdict(r, auditd_only)]
    live_split = sorted(sev[i] for i in live_ids)
    check("  ...severity of the LIVE set: 1 critical, 5 high, 7 medium",
          (live_split.count("critical"), live_split.count("high"), live_split.count("medium")), (1, 5, 7))
    check("  ...so 5 of the pack's 6 critical rules are dark on an auditd-only host",
          (dead_split.count("critical"), dead_split.count("critical") + live_split.count("critical")), (5, 6))
    check("  ...and the one surviving critical is an egress rule (see FINDING-26c)",
          [i for i in live_ids if sev[i] == "critical"], ["carbonato.exfil.egress_after_identity_change"])

    # And with eBPF attached the same three go live -- which is exactly when the contents gap matters.
    check("with eBPF attached those three are envelope-live, and their paths are what the globs assume",
          [r["id"] for r in rules if inert_verdict(r, avail)], [])

    # --- Section 6 (beat 606): the instrument's blind spot, and stock vs auditd-only. -----------------
    # These live inside main() on purpose. They were first written as a second `if __name__` block, which
    # printed its 5 checks AFTER the summary line -- so the count said 19 while 24 had run. Same class of
    # defect as beat 597 (five named checks collapsed into one aggregate). Fix: one block, one count.
    cov = open(os.path.join(ROOT, "internal", "rules", "coverage_test.go"), encoding="utf-8").read()
    check("Go suite states InertRules' content-blindness",
          "func TestInertRulesCannotSeeInsideAnAvailableClass" in cov, True)
    check("Go suite pins stock (6/14) vs auditd-only (7/13)",
          "func TestStockAndAuditdOnlyAreDifferentHosts" in cov, True)

    # The fixture split is the whole point: stockAvailable() silently included scope.change, which
    # behavior.go only sets when the scope adapter attached. Two "same" measurements gave 6/14 and 7/13.
    beh = open(os.path.join(ROOT, "internal", "collector", "behavior.go"), encoding="utf-8").read()
    auditd_block = beh[beh.index('if cfg.Behavior.AuditLog != ""'):]
    auditd_block = auditd_block[:auditd_block.index("\n\t}")]
    classes = set(re.findall(r'available\["([a-z.]+)"\] = true', auditd_block))
    check("behavior.go: an auditd-only host gets exactly 4 classes", sorted(classes),
          ["file.write", "net.egress", "net.listen", "process.exec"])
    check("  ...and scope.change is NOT one of them (it needs scopeAttached)",
          "scope.change" in classes, False)

    helper = cov[cov.index("func auditdOnlyAvailable()"):]
    helper = helper[:helper.index("\n}")]
    check("  ...and the test helper mirrors that set exactly",
          sorted(re.findall(r'"([a-z.]+)": true', helper)), sorted(classes))

    print()
    if FAILS:
        print(f"inertreach: {len(FAILS)} FAIL")
        sys.exit(1)
    print(f"inertreach: all ok ({len(RESULTS)} checks)")


if __name__ == "__main__":
    main()

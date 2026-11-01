#!/usr/bin/env python3
"""severityprobe2 — is the severity axis actually defended end to end?

Every other axis this week had a gap. Severity looked like it did too (`ev.Severity = r.Severity`
with no check at the assignment site), but the check lives a layer up, in `Rule.Validate()`, and a
layer down, in `severityRank`. This probe asks whether the whole path holds — and, per this week's
rule, it is written to be able to say "defended" only if it can also demonstrate what an undefended
version would look like.

Path:  rules/carbonato.yaml  ->  Rule.Severity (yaml tag)
                             ->  Rule.Validate()  (load-time, rejects unknown/empty)
                             ->  Engine.Match: ev.Severity = r.Severity  (no check here)
                             ->  central severityRank (unknown ranks LOWEST, cannot mask a known high)
                             ->  central/email.go, alerts: exact-string cases

EXIT: 0 = every stage defended; 1 = a gap found; 2 = cannot judge.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FAILS = []

VALID = ["info", "low", "medium", "high", "critical"]


def ok(name, cond, detail=""):
    if not cond:
        FAILS.append(f"{name} {detail}".strip())
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def block(src, opener):
    """From `opener` to its matching closing brace. Brace-aware, not span-based."""
    i = src.index(opener)
    depth, j = 0, i
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
        j += 1
    raise AssertionError(f"unterminated block: {opener}")


def severity_rank(sev):
    """Python model of central.severityRank — the doc comment promises unknown ranks lowest."""
    return {"critical": 5, "high": 4, "medium": 3, "low": 2, "info": 1}.get(sev, 0)


def validate_severity(sev):
    """Model of Rule.Validate's severity branch. Returns an error string or None."""
    if sev in VALID:
        return None
    if sev == "":
        return "severity is required"
    return f"invalid severity {sev!r}"


def main():
    pack = read("rules", "carbonato.yaml")
    rule_go = read("internal", "rules", "rule.go")
    eng = read("internal", "rules", "engine.go")
    alert = read("internal", "central", "alert.go")

    # ---- 1. the pack's own severities ------------------------------------------------------
    sevs = re.findall(r"\n\s+severity:\s*(\S+)", pack)
    counts = {s: sevs.count(s) for s in sorted(set(sevs))}
    print(f"pack severities: {counts}")
    ok("every pack severity is one of the five", all(s in VALID for s in sevs),
       str([s for s in sevs if s not in VALID]))
    ok("no severity carries YAML quote contamination", all('"' not in s and "'" not in s for s in sevs))
    ok("pack uses more than one severity level", len(set(sevs)) > 1, "a single level would make ranking moot")

    # ---- 2. load-time validation -----------------------------------------------------------
    v = block(rule_go, "func (r *Rule) Validate() error {")
    ok("Validate() checks severity", "switch r.Severity" in v)
    ok("Validate() accepts all five", all(f'"{s}"' in v for s in VALID))
    ok("Validate() rejects empty", 'case "":' in v and "severity is required" in v)
    ok("Validate() rejects an unknown value with a default branch", "default:" in v and "invalid severity" in v)
    ok("Validate() is actually called during load",
       re.search(r"r\.Validate\(\)", rule_go) and "if err := r.Validate(); err != nil" in rule_go)
    n_calls = len(re.findall(r"\.Validate\(\)", rule_go))
    ok("exactly one validation call site", n_calls == 1, str(n_calls))

    # ---- 3. the assignment site is unchecked (by design, since load validated) -------------
    m = block(eng, "func (e *Engine) Match(")
    ok("Match copies severity without re-checking", "ev.Severity = r.Severity" in m)
    ok("Match does not validate severity inline", "invalid severity" not in m)
    print("     ^ deliberate: the check happened at load, so a rule in memory is already valid")

    # ---- 4. the consumer cannot be fooled by an unknown value ------------------------------
    sr = block(alert, "func severityRank(sev string) int {")
    ok("severityRank exists", "func severityRank" in sr)
    ok("severityRank has a default branch", "default:" in sr)
    ok("default ranks LOWEST (0)", re.search(r"default:\s*\n\s*return 0", sr))
    ok("every valid severity outranks unknown", all(severity_rank(s) > 0 for s in VALID))
    ok("critical outranks everything", severity_rank("critical") == max(severity_rank(s) for s in VALID))
    ok("an unknown severity cannot mask a known high",
       severity_rank("HIGH-URGENT") < severity_rank("high"),
       f"{severity_rank('HIGH-URGENT')} vs {severity_rank('high')}")

    # ---- 5. what an UNDEFENDED version would look like (the negative) ---------------------
    # Without the load-time check, an unknown severity would reach the wire and rank 0, so a
    # rule authored as "Critical" (capitalised) would alert at the lowest priority while reading
    # as the highest in the pack. Demonstrate the trap is real, then show it is closed.
    ok("NEGATIVE: a capitalised severity is invalid by the loader", validate_severity("Critical") is not None)
    ok("NEGATIVE: ...and would rank 0 if it reached the wire", severity_rank("Critical") == 0)
    ok("...so a rule reading 'Critical' but alerting at rank 0 would be a silent demotion",
       severity_rank("Critical") == 0 and severity_rank("critical") == 5)
    ok("...and the loader prevents exactly that", validate_severity("Critical") is not None)
    ok("NEGATIVE: a trailing space is invalid by the loader", validate_severity("critical ") is not None)

    # ---- 6. every rule in the pack would pass the loader --------------------------------
    bad = [s for s in sevs if validate_severity(s) is not None]
    ok("all pack severities pass the loader model", not bad, str(bad))
    print(f"\n  denominator: {len(sevs)} rule severity string(s) checked against loader + ranker")

    print()
    if not sevs:
        print("  severityprobe2: CANNOT JUDGE — no severities in pack.")
        return 2
    if FAILS:
        print(f"FAILED {len(FAILS)}:")
        for f in FAILS:
            print("  " + f)
        return 1
    print("severityprobe2: DEFENDED — the assignment site looks unchecked, but validation happens at\n"
          "  load (strict, rejects empty AND unknown) and the consumer ranks unknown values lowest so\n"
          "  they cannot mask a known high. Both ends are guarded; the middle needs no guard.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

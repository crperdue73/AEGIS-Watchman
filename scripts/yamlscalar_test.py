#!/usr/bin/env python3
"""yamlscalar_test -- a comment inside a quoted YAML list item must not become part of the glob.

FINDING-2026-10-26. Beat 590 added a comment after each shadowed endpoint glob:

    - "*slack.com*"  ->  - "*hooks.slack.com*"      # shadowed by *slack.com*

Valid YAML. `yaml.safe_load` returned all eight clean values. But `fieldcheck.keys_by_rule` read the
item as '*hooks.slack.com*"      # shadowed by *slack.com*' -- the closing quote stripped, the comment
kept -- so every glob silently stopped matching. Nothing crashed and every count still added up (8
values, 8 blocks, same denominators), which is precisely why it is worth a test: a reader that is
wrong about CONTENT while right about COUNT is invisible to every parity check I own.

This is the SECOND time a comment has eaten pack data (cf. FINDING-2026-10-25m, seven endpoints). The
general form is worth stating once: this parser is not a YAML parser, it is a regex over YAML's
neighbourhood, and any YAML feature it does not model fails silently rather than loudly.

The fix is `_yaml_scalar`. These assertions pin it, then prove it end-to-end against the live pack.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fieldcheck  # noqa: E402

FAILS = []


def check(name, got, want):
    if got != want:
        FAILS.append(name)
        print(f"FAIL {name}: got {got!r} want {want!r}")
    else:
        print(f"ok   {name}")



def main():
    S = fieldcheck._yaml_scalar

    # --- the matrix of shapes a list item can take ------------------------------------------------
    check("quoted value + spaced comment", S('"*slack.com*"      # shadowed by *slack.com*'), "*slack.com*")
    check("single-quoted value + comment", S("'*x*' #c"), "*x*")
    check("bare value + comment", S("*x* #c"), "*x*")
    check("bare value with NO comment is untouched", S("plain"), "plain")
    check("double-quoted value is unquoted", S('"x"'), "x")
    check("quoted empty string is empty, not a quote char", S('""'), "")

    # --- the cases where a naive split would be WRONG ---------------------------------------------
    check("a '#' INSIDE quotes is not a comment", S('"a #b"'), "a #b")
    check("  ...nor is one that follows a bare hash-free value", S("*a#b*"), "*a#b*")
    check("a comment-only line yields empty (callers skip it)", S("#comment"), "")

    # --- padding and the shapes the old code already handled --------------------------------------
    check("surrounding whitespace is stripped", S('   "*x*"   '), "*x*")
    check("a dash-prefixed glob keeps its star", S("*-v /*:/"), "*-v /*:/")

    # --- END TO END against the live pack ---------------------------------------------------------
    # The pack is the real assertion: these are the exact lines beat 590 added comments to, and the
    # failure mode was that they parsed as globs with prose in them.
    text = open(fieldcheck.PACK, encoding="utf-8").read()
    globs = []
    for rid, key, vals in fieldcheck.keys_by_rule(text):
        if key == "endpoint_glob_any":
            globs.extend(vals)

    check("all 32 endpoint globs still parse", len(globs), 32)
    check("no parsed glob contains a '#'", [g for g in globs if "#" in g], [])
    check("no parsed glob contains whitespace", [g for g in globs if any(c.isspace() for c in g)], [])
    check("every parsed glob still starts and ends with '*'",
          [g for g in globs if not (g.startswith("*") and g.endswith("*"))], [])

    # And prove the reader agrees with YAML itself, which is the only authority that matters here.
    try:
        import yaml
    except ImportError:
        yaml = None

    if yaml is not None:
        doc = yaml.safe_load(text)
        # Only rules that CARRY the key. Comparing every rule id compares 22 empty lists against a dict
        # that has no entry for them -- my first version did that and "failed" on identical values.
        by_rule = {r["id"]: list(r["match"]["endpoint_glob_any"])
                   for r in doc["rules"] if "endpoint_glob_any" in r.get("match", {})}
        mine = {}
        for rid, key, vals in fieldcheck.keys_by_rule(text):
            if key == "endpoint_glob_any":
                mine[rid] = list(vals)
        check("the reader's values equal YAML's values, rule for rule", mine, by_rule)
        check("  ...including the three rules that carry comments",
              sorted(r for r in by_rule if len(by_rule[r]) >= 8),
              ["carbonato.c2.blockchain_rpc", "carbonato.exfil.egress_after_change",
               "carbonato.exfil.egress_after_identity_change"])
    else:
        print("skip YAML cross-check: PyYAML not importable")

    print()
    if FAILS:
        print(f"yamlscalar: {len(FAILS)} FAIL")
        sys.exit(1)
    print("yamlscalar: all ok")


if __name__ == "__main__":
    main()

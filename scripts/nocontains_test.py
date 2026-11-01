#!/usr/bin/env python3
"""nocontains_test.py — sweep the rule pack for globs that cannot use the contains route.

WHY (beat 612, 2026-10-27): Leo named the class in family chat -- "the fixture shape and the matcher
shape disagreed." substringNeedle only fires for `*<core>*` with no `*?[` in the core. Every other
glob is at the mercy of path.Match, where `*` never crosses '/'. Two shapes lose the fallback:
  (a) an interior star              e.g. '*-v /*:/'          -> DEAD (beat 611)
  (b) two wildcard runs in a segment e.g. '*/openai*key*'     -> NARROW (beat 612)

This sweep exists so a NEW glob of either shape is noticed the day it is authored, instead of being
discovered a year later by someone reading the engine. It reads the pack -- it does not hardcode the
glob list -- and it fails if the known set grows.

It also prints each glob's REACH over a fixed path ladder. "Fires somewhere" and "fires where it
matters" are different claims, and only the ladder tells them apart.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import neargo  # noqa: E402

PACK = os.path.join(HERE, os.pardir, "rules", "carbonato.yaml")
FIELDS = ("path_glob_any", "argv_any")

# The no-needle globs that exist today, with their adjudicated status. Adding a glob of this shape to
# the pack MUST fail this test until someone writes down here which kind it is and why.
# RETIRED beat 613: ("carbonato.infra.privileged_container", "*-v /*:/") was DEAD; fixed to "*-v /:*",
# which carries a needle. Removed here -- the sweep's "silently disappeared" check is what forced this
# edit, which is the whole point of that check.
KNOWN = {
    ("carbonato.secret.read_model_key", "*/openai*key*"): "NARROW - flat filenames only",
    ("carbonato.secret.read_model_key", "*anthropic*key*"): "NARROW - flat filenames only",
}

# A ladder of layouts a credential rule plausibly needs to cover. Must stay BALANCED across vendors:
# my first version listed only openai flat names, which made '*anthropic*key*' look dead (0/6) when it
# simply had no rung it could reach. A skewed ladder manufactures a finding -- same failure as a
# fixture in the wrong shape, which is the class this file exists to catch.
LADDER = [
    "/etc/openai_key",
    "/srv/openai_api_key",
    "/home/u/.openai-key",
    "/etc/openai/api_key",
    "/opt/openai/keys/prod.key",
    "/etc/anthropic_key",
    "/srv/anthropic_api_key",
    "/root/.anthropic/key",
    "/etc/anthropic/api_key",
]

FAILS = []


def check(name, got, want):
    if got == want:
        print(f"ok   {name}")
    else:
        print(f"FAIL {name}: got {got!r} want {want!r}")
        FAILS.append(name)


def main():
    import yaml
    pack = yaml.safe_load(open(PACK))
    total, noneedle = 0, {}
    for r in pack["rules"]:
        m = r.get("match", {})
        for field in FIELDS:
            for g in (m.get(field) or []):
                total += 1
                if neargo.substring_needle(g) is None:
                    noneedle[(r["id"], g)] = field

    print(f"--- 1. population: {total} globs across {len(FIELDS)} fields")
    check("the pack still has globs to sweep", total > 50, True)

    print()
    print(f"--- 2. globs with NO contains needle: {len(noneedle)} (known: {len(KNOWN)})")
    new = sorted(set(noneedle) - set(KNOWN))
    gone = sorted(set(KNOWN) - set(noneedle))
    check("no NEW no-needle glob has appeared in the pack", new, [])
    for rid, g in new:
        print(f"       NEW: {rid} {g!r} -- adjudicate it and add it to KNOWN")
    for rid, g in gone:
        print(f"       GONE: {rid} {g!r} -- was fixed? drop it from KNOWN and from its finding")
    check("no KNOWN no-needle glob silently disappeared", gone, [])

    print()
    print("--- 3. adjudicated status, re-derived (not trusted from the comment)")
    check("the fixed bind glob '*-v /:*' DOES carry a needle",
          neargo.substring_needle("*-v /:*"), "-v /:")
    check("'*/openai*key*' still has no needle",
          neargo.substring_needle("*/openai*key*"), None)

    print()
    print("--- 4. reach over the path ladder (does it fire where it MATTERS?)")
    for g in ("*/openai*key*", "*anthropic*key*"):
        hits = [p for p in LADDER if neargo.match_one(g, p)]
        print(f"       {g:18s} reaches {len(hits)}/{len(LADDER)}: {hits}")
    # Pin the measured narrowness so the gap is visible in the suite, not just in a doc.
    check("'*/openai*key*' reaches only the flat-name layouts",
          [p for p in LADDER if neargo.match_one("*/openai*key*", p)],
          ["/etc/openai_key", "/srv/openai_api_key"])
    check("'*anthropic*key*' reaches only the flat-name layouts",
          [p for p in LADDER if neargo.match_one("*anthropic*key*", p)],
          ["/etc/anthropic_key", "/srv/anthropic_api_key"])
    check("  ...and MISSES every directory-then-file layout",
          [p for p in LADDER if "openai/" in p and neargo.match_one("*/openai*key*", p)], [])

    print()
    if FAILS:
        print(f"nocontains_test: {len(FAILS)} failure(s).")
        return 1
    print("nocontains_test: all ok -- no new no-needle globs, and the known three are still adjudicated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

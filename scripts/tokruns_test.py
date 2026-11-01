#!/usr/bin/env python3
"""tokruns_test — the boundary rule must be able to be WRONG about itself.

What is being tested is a CLAIM: that treating '.' as a name boundary rescues globs like
`*/etc/cron*` without turning every glob into a wildcard.

  A. The rescue itself. `*/etc/cron*` must fire on some run of 'etc/cron.d/evil'.
  B. The counter-case. The same glob must stay silent on an unrelated descent, or "runs" is
     just the contains-fallback wearing a new coat.
  C. '.' ends a name: 'cron.d' yields 'cron' and 'd'.
  D. '/' is still NOT crossed: no run lets a '*' eat a slash. If this fails, the rationale
     evaporates and every glob becomes a wildcard.
  E. The whole string is excluded (the caller already tests it).

Exit 0 only if all five hold.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tokruns import runs  # noqa: E402
from packcheck import match_one, go_path_match  # noqa: E402


def real_match(glob, s):
    """A run candidate only counts if path.Match itself likes it.

    This is deliberately NOT match_one: match_one adds suffix/basename/contains
    fallbacks, and using it here would let the fallback (not the boundary rule) do the
    rescuing — the test would pass for the wrong reason. Runs are proposed; path.Match
    is the judge.
    """
    return go_path_match(glob, s)


pass_n = fail_n = 0


def ok(name):
    global pass_n
    pass_n += 1
    print(f"  ok    {name}")


def bad(name, detail=""):
    global fail_n
    fail_n += 1
    print(f"  FAIL  {name}{(': ' + detail) if detail else ''}")


def main():
    # A. the rescue — the point of the module
    # the corpus carries this attack as a rooted path (/etc/cron.d/evil); runs keep the root
    cands = runs("/etc/cron.d/evil")
    hit = [r for r in cands if real_match("*/etc/cron*", r)]
    if hit:
        ok(f"*/etc/cron* fires on a run ({hit[0]!r})")
    else:
        bad("*/etc/cron* on runs", f"none of {len(cands)} runs matched via path.Match: {cands}")

    # B. and stays silent on a non-cron descent
    noise = [r for r in runs("/etc/nginx/sites-enabled/default") if real_match("*/etc/cron*", r)]
    if not noise:
        ok("*/etc/cron* silent on an unrelated run")
    else:
        bad("fires on unrelated run", f"{noise[0]!r} — 'runs' is the contains-fallback in disguise")

    # C. '.' ends a name
    r = runs("cron.d")
    if "cron" in r and "d" in r:
        ok("'.' ends a name (cron.d -> cron, d)")
    else:
        bad("dot boundary", f"{r}")

    # D. '/' is still not crossed by '*'
    crossed = [x for x in runs("etc/cron/d/evil") if real_match("*cron*evil", x)]
    if not crossed:
        ok("'*' still cannot cross '/'")
    else:
        bad("'*' crossed '/'", f"{crossed[0]!r} — rationale invalid")

    # E. whole string excluded
    if runs("etc") == []:
        ok("single-component string yields no runs")
    else:
        bad("whole string offered as a run", f"{runs('etc')}")

    print(f"\ntokruns_test: {pass_n} ok, {fail_n} fail")
    return 1 if fail_n else 0


if __name__ == "__main__":
    sys.exit(main())

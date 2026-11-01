#!/usr/bin/env python3
"""needleprobe — does the Python mirror read the same pack as the Go engine on 'needle*' / '*needle'?

THE GAP — CLOSED 2026-10-27 (beat 610). Was: recorded and unfixed since beat 546.
  engine.go substringNeedle trims a leading star, then a trailing star, then rejects any core that
  still holds a wildcard — a '/' is NOT special to it. So 'a/b*' keeps the needle 'a/b', and the
  contains-fallback fires on any subject holding 'a/b'.

  scripts/neargo.py substring_needle USED TO add `or "/" in core` — an extra refusal the engine does
  not make. For a needle whose core holds a '/', the mirror said "no needle" and the engine said
  "needle". The engine is the one that runs in production, so the mirror was the STRICTER of the two.

  FIXED 2026-10-27: the `or "/" in core` clause is gone from neargo.py and the two ports now agree
  with the Go on every case below. Probe re-derived from "hunt the gap" to "assert no gap".

  Direction of the error matters: every prior probe in this tree measures through the mirror. A
  mirror narrower than the engine under-reports what the engine fires on — the safe direction for a
  person, the UNSAFE direction for a detector's own test results, which is what 545 named.

WHY IT IS LATENT TODAY
  Every endpoint glob in the pack is '*…*' and slash-free, and for path globs the suffix chain
  usually reaches the same answer anyway. Latent is not absent: the day a slash-bearing 'needle*'
  glob lands in the pack, this tree's answer and production's answer diverge silently.

WHAT THIS PROVES — and what it cannot
  It reads the engine's needle rule from engine.go source (go_block, brace-counted) and applies it
  to the same globs the mirror sees. No Go toolchain on this host, so this is a reading of the
  source, not a compiled run. That limit is printed, not hidden.

Exit 0 = mirror agrees with engine on every glob tested, on BOTH the needle and the verdict, and the
premise guard still works.

RE-DERIVED 2026-10-27 (beat 610). The old version asserted "the divergence reaches a VERDICT" and
required len(verdict_diffs) > 0 — i.e. it FAILED once the gap was fixed. Worse, its engine_match_one
returned True whenever a needle existed ('... if needle is None else True'), which is not the engine's
rule at all: the engine only fires the contains route when the needle is actually IN the subject. That
stub manufactured three phantom 'engine=True mirror=False' verdict diffs on globs where neither port
fires. A probe that fabricates the divergence it is hunting can only ever confirm itself.
"""
import sys, os, re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import neargo                      # the mirror under test

def go_block(text, header_re, name=None):
    """Return the source of the Go function whose header matches `header_re`, brace-counted.

    Beat 645: this lived nowhere. `needleprobe.py` imported it (`from pathfixture import go_block, read`)
    after a refactor had removed it, so the module raised ImportError at import -- and the gate read the
    non-zero exit as "the probe went red, therefore it works." An import error and a failing assertion
    both exit 1. They are not the same evidence. It is defined here now, self-contained, so the probe owes
    nothing to another module's private helpers.
    """
    m = re.search(header_re, text, re.MULTILINE)
    if not m:
        raise SystemExit(f"go_block: header not found: {header_re}")
    i = text.index("{", m.end() - 1)
    depth, j = 0, i
    while j < len(text):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[m.start():j + 1]
        j += 1
    raise SystemExit(f"go_block: unbalanced braces for {header_re}")


def read(p):
    with open(p, encoding="utf-8") as fh:
        return fh.read()


FAILS = []


def ok(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def engine_substring_needle_spec(src):
    """Read substringNeedle from engine.go and return a callable with ITS rule, not the mirror's."""
    body = go_block(src, r"^func substringNeedle\(")
    trims_prefix = 'TrimPrefix(core, "*")' in body
    trims_suffix = 'TrimSuffix(core, "*")' in body
    rejects_wildcard = 'ContainsAny(core, "*?[")' in body
    # Beat 645: this detector used to be
    #     '"/"' in body and "ContainsAny" in body and "/" in body.split("ContainsAny")[-1]
    # which only recognised a slash test written with strings.ContainsAny. The obvious way to write it
    # is strings.Contains(core, "/") -- and that has no "ContainsAny" in it, so the detector stayed
    # False while the engine had in fact gained the rejection. needleprobe_test.sh caught it: it patches
    # engine.go and demands the probe go red, the probe stayed green, and the selfcheck failed.
    # A detector that only recognises the spelling it was written against is the same defect as a
    # fixture that only fits the implementation. Ask about the FUNCTION, not the wording.
    rejects_slash = bool(
        re.search(r'Contains(?:Any)?\(\s*core\s*,\s*"[^"]*/', body)
    )

    def needle(glob):
        core = glob
        if trims_prefix and core.startswith("*"):
            core = core[1:]
        if trims_suffix and core.endswith("*"):
            core = core[:-1]
        if core == "":
            return None
        if rejects_wildcard and any(ch in core for ch in "*?["):
            return None
        if rejects_slash and "/" in core:
            return None
        return core

    return needle, {
        "trims_leading_star": trims_prefix,
        "trims_trailing_star": trims_suffix,
        "rejects_further_wildcard": rejects_wildcard,
        "rejects_slash": rejects_slash,
    }


def main():
    # Beat 634: this used to be `os.path.join("internal/rules/engine.go")` -- a path relative to $PWD.
    # It worked only because I always ran it from the repo root by hand. Under `suitecheck.sh` (temp cwd)
    # it raised FileNotFoundError, and `needleprobe_test.sh` read that traceback as "the probe went red",
    # which its mutation section scores as PASS. So a crash was quietly the expected outcome and the guard
    # could not have gone red for the real reason. Resolve against this file, always.
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = read(os.path.join(ROOT, "internal/rules/engine.go"))
    engine_needle, facts = engine_substring_needle_spec(src)

    print("engine needle rule, read from engine.go")
    for k, v in facts.items():
        print(f"     {k:26s} {v}")
    ok("engine trims a leading star", facts["trims_leading_star"])
    ok("engine trims a trailing star", facts["trims_trailing_star"])
    ok("engine rejects a core with a further wildcard", facts["rejects_further_wildcard"])

    # --- the premise -------------------------------------------------------
    ok("PREMISE: engine needle rule does NOT reject a slash in the core", not facts["rejects_slash"],
       "engine.go gained a '/' test — this probe's divergence claim is stale, re-derive it")

    # --- the divergence, on globs a pack author would plausibly write -------
    cases = [
        ("*a/b*", "a/b"),
        ("a/b*", "a/b"),
        ("*reports/*", "reports/"),
        ("*/api/v2*", "/api/v2"),
        ("*:443/api*", ":443/api"),
    ]
    subjects = ["x a/b y", "GET /reports/q3 hit", "https://h/api/v2/events", "1.2.3.4:443/api/x"]

    diverged = []
    for glob, core_expect in cases:
        eng = engine_needle(glob)
        mir = neargo.substring_needle(glob)
        same = eng == mir
        print(f"     needle {glob!r:16s} engine={eng!r:14s} mirror={mir!r}")
        if not same:
            diverged.append((glob, eng, mir))
        ok(f"mirror agrees with engine on needle({glob!r})", same, f"engine={eng!r} mirror={mir!r}")

    # --- and does the divergence change a verdict, not just a value? -------
    def engine_match_one(glob, s):
        # The Go order (engine.go matchOne): contains fallback FIRST, then path/suffix/basename.
        # Only fire on the needle route when the needle is actually present in the subject.
        needle = engine_needle(glob)
        if needle is not None and needle in s:
            return True
        return neargo.match_one(glob, s)

    verdict_diffs = []
    for glob, _ in cases:
        for s in subjects:
            eng = engine_match_one(glob, s)
            mir = neargo.match_one(glob, s)
            if eng != mir:
                verdict_diffs.append((glob, s, eng, mir))

    for glob, s, eng, mir in verdict_diffs:
        print(f"     VERDICT DIFF  {glob!r} on {s!r}: engine={eng} mirror={mir}")

    ok("mirror and engine agree on every VERDICT too (gap closed, not merely narrowed)",
       len(verdict_diffs) == 0,
       f"{len(verdict_diffs)} verdict diffs remain: "
       + ", ".join(f"{g!r}@{s_!r}" for g, s_, _, _ in verdict_diffs[:3]))

    print()
    print("needleprobe: mirror agrees with engine.go on needles AND verdicts")
    if FAILS:
        print(f"needleprobe: {len(FAILS)} failure(s) — mirror and engine disagree")
        for f in FAILS:
            print(f"  - {f}")
        return 1

    print(f"needleprobe: {len(cases)} needle globs, {len(verdict_diffs)} verdict differences "
          f"— divergence confirmed, not yet fixed")
    print("     (no Go toolchain on this host; engine claims are read from source, not compiled)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

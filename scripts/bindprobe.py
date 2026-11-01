#!/usr/bin/env python3
"""bindprobe — does detail_glob_any on `binds` reach the mounts it names?

The rule carbonato.container.host_root_mount globs the `binds` detail value for
`*:/host*`, `/:/` and `*/var/run/docker.sock*`. docker.go joins every bind mount into ONE
comma-separated string:

    detail["binds"] = strings.Join(cfg.Binds, ",")

The engine evaluates a detail glob against that whole string (engine.go:166-181 -> anyGlobMatches).
So a mount that is merely *present* in a list can be reached only if the glob tolerates its
neighbours — and a glob anchored at both ends cannot.

This probe asks three questions, in this order:
  1. What does the joined value actually look like for realistic bind sets?
  2. Which globs reach it, under the engine's own matchOne semantics (never a guess)?
  3. Does the rule fire on a container that mounts the Docker socket as one of several binds?

Pass-case first: a control bind set that should NOT fire is checked before the interesting ones,
so a probe that fires on everything is caught rather than celebrated.

No Go toolchain needed. Match semantics are transcribed from engine.go (process-suffix + basename +
substring-needle); the transcription itself is tested by the companion suffixprobe/pathmatchdiff work.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import neargo  # noqa: E402  (mirror of engine.go's matchOne / pathSuffixes / substring_needle)
import json
import re
import yaml

ROOT = os.path.dirname(HERE)
RULE = "carbonato.container.host_root_mount"

# --- binds exactly as docker.go joins them ----------------------------------------------------
# Realistic bind strings, chosen BEFORE looking at the globs' behaviour. Each entry is
# (label, [mount, ...], must_fire, why). The join is "," with no space, per strings.Join(cfg.Binds, ",").
CASES = [
    # control first: a mundane mount set that must NOT fire
    ("control: cache dir only", ["/tmp/cache:/cache"], False,
     "no host-root, no host tail, no docker socket"),
    ("control: two ordinary mounts", ["/data:/data", "/tmp/x:/tmp/x"], False,
     "still nothing that reaches the host"),
    # the escape primitive the rule is named for
    ("socket alone", ["/var/run/docker.sock:/var/run/docker.sock"], True,
     "the canonical escape; single mount so the value is clean"),
    ("socket, second of two", ["/data:/data", "/var/run/docker.sock:/var/run/docker.sock"], True,
     "socket present but not alone — the interesting one"),
    ("socket, first of two", ["/var/run/docker.sock:/var/run/docker.sock", "/data:/data"], True,
     "order swapped, to see if position matters"),
    ("host root to /host", ["/:/host"], True, "single mount, the rule's own example"),
    ("host root to /host, second", ["/etc/localtime:/etc/localtime", "/:/host"], True,
     "the rule's own example, in a list"),
    ("host root to /host, first", ["/:/host", "/data:/data"], True,
     "position swapped again"),
    ("socket, bare source->bare dest", ["/var/run/docker.sock:/var/run/docker.sock"], True,
     "duplicate of 'socket alone' kept deliberately: same input must give same answer"),
]

# --- how the engine reads one detail value ----------------------------------------------------
def detail_globs(rule_id):
    """The globs the rule lists under detail_glob_any, read from the pack.

    REWRITTEN beat 614. This used a hand-rolled regex over the raw YAML text, which silently returned
    {} the moment the pack grew a comment line under a detail key -- the regex only accepted
    "- value" lines. It did not error; it returned an empty dict, and the caller then KeyError'd on
    'binds', which is how it was found. Same failure class as everything else this project has caught:
    a reader that quietly produces nothing looks identical to a rule that matches nothing.
    yaml.safe_load does not have opinions about comments.
    """
    with open(os.path.join(ROOT, "rules", "carbonato.yaml"), encoding="utf-8") as fh:
        pack = yaml.safe_load(fh)
    for rule in pack.get("rules", []):
        if rule.get("id") == rule_id:
            out = {}
            for key, vals in (rule.get("match", {}).get("detail_glob_any") or {}).items():
                out[key] = [str(v) for v in (vals or [])]
            return out
    return None


def fires(globs, value):
    """engine.go anyGlobMatches: '' never matches; else OR over the rule's globs."""
    if value == "":
        return False
    return any(neargo.match_one(g, value) for g in globs)


def main():
    globs_by_key = detail_globs(RULE)
    if not globs_by_key:
        print(f"bindprobe: could not read detail_glob_any for {RULE}", file=sys.stderr)
        return 2
    binds_globs = globs_by_key.get("binds") or []
    print(f"bindprobe — rule {RULE}")
    print(f"  detail_glob_any keys: {sorted(globs_by_key)}")
    print(f"  binds globs ({len(binds_globs)}): {binds_globs}\n")

    able_to_fail = 0
    wrong = []
    print(f"  {'case':32} {'fires':6} {'want':6} verdict")
    for label, mounts, want, why in CASES:
        joined = ",".join(mounts)
        got = fires(binds_globs, joined)
        able_to_fail += 1
        verdict = "ok" if got == want else "MISSED" if want else "FALSE-POSITIVE"
        if got != want:
            wrong.append((label, joined, want, got, why))
        print(f"  {label:32} {str(got):6} {str(want):6} {verdict}")
        print(f"  {'':32} value: {joined!r}")

    print(f"\n  denominator: {len(CASES)} bind set(s), all {able_to_fail} capable of failing; "
          f"{len(wrong)} disagree(s) with the intended reading.")
    if wrong:
        print("\n  disagreements:")
        for label, joined, want, got, why in wrong:
            print(f"    {label}: want {want}, got {got} — {why}")
            print(f"      value: {joined!r}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

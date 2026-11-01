#!/usr/bin/env python3
"""nameprobe.py — the "operator reads the manual" false-positive census.

WHY THIS EXISTS
  fpprobe.py reported 4 unexpected results. Four was not the size of the problem: sweeping every
  argv_any glob against benign "just reading about it" command lines found 37 globs across 7 rules
  that fire when you merely NAME their target (`man crontab`, `man base64`, `man -v /:`).

  The matcher is not wrong. engine.go:203 deliberately space-joins argv so multi-word globs like
  `*pip install*` are not silently dead. The consequence is that `man crontab` becomes the string
  "man crontab", and `*crontab*` finds it. The rules are asking the question the matcher cannot
  answer, because process.exec telemetry does not say which element was the executable.

  This census pins the count so the class cannot GROW unnoticed while the schema decision waits.
  It is a ceiling, not a pass: any new glob in this shape shows up here the day it is authored.

NOT A FIX
  Narrowing these globs one at a time trades false positives for false negatives. Measured:
  `crontab -*` drops `man crontab` but also drops bare `crontab`, which really is persistence.
  The real fix is an exe anchor in the match schema -- see
  docs/FINDING-2026-10-27b-37-globs-fire-on-a-command-that-names-their-target.md

Exit: 0 census holds at or below the known count · 1 the class grew · 2 pack unreadable.
No Go toolchain here; matching uses the packcheck port (verified against engine.go:281 at beat 616).
"""
import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import packcheck  # noqa: E402

PACK = os.path.join(ROOT, "rules", "carbonato.yaml")

# Globs that trip on a command which merely names them. Measured beat 618. This is a CEILING:
# the census must not exceed it, and beating it down is the job once a schema decision lands.
KNOWN_COUNT = 37
KNOWN_RULES = {
    "carbonato.persist.cron_or_rc",
    "carbonato.tool.package_install",
    "carbonato.exec.obfuscation",
    "carbonato.net.new_listener",
    "carbonato.exec.ssh_key_heredoc",
    "carbonato.persist.survives_session",
    "carbonato.infra.privileged_container",
}

# Well-formed "I am reading about it" lines only. No `man | sh` (not a command anyone types) and no
# glob whose needle holds shell syntax -- a needle containing '|' or '&&' cannot be tripped by a
# manual lookup, and including such probes inflates the census with fiction. The first version of
# this sweep counted 39 because of exactly that; corrected before publishing the number.
READ_VERBS = ["man", "grep", "echo", "cat", "vim", "less"]
SHELL_SYNTAX = set("|&;$`")


def needle_engine_way(glob):
    """engine.go:281 substringNeedle -- trim one leading and one trailing '*', guard only `*?[`."""
    core = glob
    if core.startswith("*"):
        core = core[1:]
    if core.endswith("*"):
        core = core[:-1]
    if not core or any(c in core for c in "*?["):
        return None
    return core


def census(rules):
    hits = []
    for rid, globs in rules:
        for g in globs:
            if not isinstance(g, str):
                continue
            nd = needle_engine_way(g)
            if nd is None or any(c in nd for c in SHELL_SYNTAX):
                continue
            for verb in READ_VERBS:
                line = f"{verb} {nd}"
                if packcheck.any_argv([g], line.split()):
                    hits.append((rid, g, line))
                    break
    return hits


def main():
    try:
        doc = yaml.safe_load(open(PACK, encoding="utf-8"))
    except OSError as e:
        print(f"nameprobe: cannot read pack: {e}", file=sys.stderr)
        return 2
    rules = []
    for r in (doc or {}).get("rules") or []:
        m = r.get("match") or {}
        globs = m.get("argv_any") or []
        if globs:
            rules.append((r.get("id", "?"), globs))
    if not rules:
        print("nameprobe: parsed 0 argv_any rules -- pack unreadable", file=sys.stderr)
        return 2

    hits = census(rules)
    by_rule = {}
    for rid, g, line in hits:
        by_rule.setdefault(rid, []).append((g, line))

    print(f"nameprobe: {len(hits)} globs across {len(by_rule)} rules fire on a command that names them")
    for rid in sorted(by_rule):
        globs, line = by_rule[rid][0]
        print(f"  {rid:44s} {len(by_rule[rid]):2d}  e.g. {globs!r} <- `{line}`")

    fails = []
    if len(hits) > KNOWN_COUNT:
        fails.append(f"census grew: {len(hits)} > {KNOWN_COUNT} known")
    new_rules = set(by_rule) - KNOWN_RULES
    if new_rules:
        fails.append(f"new rule(s) in this class: {sorted(new_rules)}")
    if len(hits) < KNOWN_COUNT:
        print(f"  note  census shrank to {len(hits)} (was {KNOWN_COUNT}) -- good news; "
              f"lower KNOWN_COUNT to lock it in")

    if fails:
        for f in fails:
            print(f"FAIL {f}")
        return 1
    print(f"ok   census holds at {len(hits)} <= {KNOWN_COUNT} known "
          f"(the ceiling is not a pass -- it is a pin; see FINDING-2026-10-27b)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

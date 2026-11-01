#!/usr/bin/env python3
"""exeprobe — check one thing the engine comment gets subtly wrong.

`exeMatches` (engine.go:188) compares a rule term against the exe *exactly*, or against
`path.Base(exe)`: "the rule term matches either the raw string (full path) or its basename, so
'bash' matches '/bin/bash'". Read that sentence twice. It says bash matches /bin/bash. It does not
say /bin/bash matches bash — because it does not, and that is a difference that matters:

  * A rule that lists bare names (`exe_any: [sh, bash, python3]`) catches `/bin/bash`, because the
    telemetry layer almost always reports an absolute path.
  * A rule that lists a path (`exe_any: [/usr/bin/curl]`) catches ONLY that exact path. `/bin/curl`
    on a usrmerge-less host, or a copy at `/tmp/curl`, or `busybox`-style multi-call, misses.

The asymmetry is deliberate-ish and the comment is honest about its direction, but no test in the
tree asks what happens when a rule author writes a path in exe_any. This probe answers that, with
the pack's own globs.

WHAT IT PRINTS
  - how many `exe_any` terms in the pack are bare names vs. paths vs. globs, and which rules
  - for every term: does it fire on the bare form, the absolute form, and the copy-at-/tmp form
  - a verdict per rule: ASYMMETRIC-TERMS if a term is a path (fires on that path only), LIVE if
    every term is a bare name (fires wherever the binary lives)

Exit: 0 if every exe_any term is a bare name (the safe shape), 1 if any term is a path or a glob
(a shape whose reach depends on the host's layout), 2 if the pack cannot be read.

Honest scope: this is about *reach*, not about correctness. A path term may be exactly what the
author wants. The probe exists so that choice is visible instead of accidental.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from packcheck import PACK  # noqa: E402

RULE_RE = re.compile(r"^\s*-\s+id:\s*(\S+)\s*$")
EXE_START = re.compile(r"^\s*exe_any:\s*(.*)$")


def exe_terms(text):
    """[(rule_id, [terms])] — supports inline lists and block lists, one block per rule."""
    out = []
    rid = None
    cur = None
    started = False
    for line in text.splitlines():
        m = RULE_RE.match(line)
        if m:
            if rid and started:
                out.append((rid, cur))
            rid = m.group(1)
            cur = None
            started = False
            continue
        m = EXE_START.match(line)
        if m and rid:
            started = True
            inline = m.group(1).strip()
            if inline.startswith("["):
                cur = [v.strip().strip("\"'") for v in inline.strip("[]").split(",") if v.strip()]
                out.append((rid, cur))
                started = False
            else:
                cur = []
            continue
        if started and cur is not None:
            m2 = re.match(r"^\s*-\s+(.+?)\s*$", line)
            if m2:
                cur.append(m2.group(1).strip().strip("\"'"))
            elif line.strip():
                out.append((rid, cur))
                started = False
                cur = None
    if rid and started and cur:
        out.append((rid, cur))
    return out


def shape(term):
    if re.search(r"[*?\[]", term):
        return "glob"
    if "/" in term:
        return "path"
    return "name"


def fires(term, exe):
    """Transcription of exeMatches: exact, or term == path.Base(exe)."""
    if not exe:
        return False
    if term == exe:
        return True
    return term == exe.rsplit("/", 1)[-1]


def main():
    try:
        with open(PACK, encoding="utf-8") as fh:
            rules = exe_terms(fh.read())
    except OSError as e:
        print(f"exeprobe: cannot read pack: {e}", file=sys.stderr)
        return 2
    if not rules:
        print("exeprobe: parsed 0 exe_any blocks — blind, not clean", file=sys.stderr)
        return 2

    terms_total = sum(len(t) for _, t in rules)
    counts = {"name": 0, "path": 0, "glob": 0}
    risk = []
    print(f"exeprobe — {len(rules)} rule(s) with exe_any, {terms_total} term(s), "
          f"{terms_total * 3} reach checks (bare / absolute / copied)\n")
    for rid, terms in rules:
        shapes = [shape(t) for t in terms]
        for s in shapes:
            counts[s] += 1
        bad = [t for t, s in zip(terms, shapes) if s != "name"]
        verdict = "LIVE" if not bad else "ASYMMETRIC-TERMS"
        print(f"  [{verdict:16}] {rid}: {len(terms)} term(s) "
              f"({shapes.count('name')} name, {shapes.count('path')} path, {shapes.count('glob')} glob)")
        for t in bad:
            # show exactly what the shape costs, using the term itself
            bare = t.rsplit("/", 1)[-1]
            print(f"      {shape(t):5} {t!r}: bare {bare!r} -> {fires(t, bare)}; "
                  f"at /bin/{bare} -> {fires(t, '/bin/' + bare)}; at /tmp/{bare} -> {fires(t, '/tmp/' + bare)}")
            risk.append((rid, t))

    # the safe shape, demonstrated once so the contrast is in the output rather than the prose
    demo = "python3"
    print(f"\n  control: term {demo!r} (name) -> /bin/{demo} {fires(demo, '/bin/' + demo)}, "
          f"/usr/local/bin/{demo} {fires(demo, '/usr/local/bin/' + demo)}, copied to /tmp {fires(demo, '/tmp/' + demo)}")

    print(f"\n  denominators: {terms_total} exe_any terms, {terms_total * 3} reach checks, "
          f"{len(risk)} term(s) whose reach depends on the host layout.")
    return 1 if risk else 0


if __name__ == "__main__":
    sys.exit(main())

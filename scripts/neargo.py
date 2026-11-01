"""neargo — the port of `matchOne` that stays inside what path.Match actually accepts.

WHY THIS FILE EXISTS
  scripts/packcheck.py's match_one() carries a truncated contains-fallback: it fires whenever
  the glob is slash-free, starts and ends with '*', and the middle is a substring. The Go
  engine is stricter (engine.go substringNeedle): a '*x*' or '*x' or 'x*' whose core contains
  no further wildcard and no '/' — and it never applies to a glob with an interior wildcard.

  The difference is not cosmetic. Two dead globs in the pack are being kept alive by the
  looser port:

      '* -O /tmp/*'  vs 'curl -O /tmp/x'      port: fires   (core ' -O /tmp/' present)
                                              Go:   does not (' -O /tmp/' contains '/')

  So a probe built on match_one can report a glob live that the engine will never match —
  the exact failure this project exists to catch, wearing the project's own tools.

  neargo is deliberately STRICTER than match_one and no looser than the Go source. Where
  it and match_one disagree, match_one is wrong.

WHAT NEARGO DOES NOT DO
  It is a transcription of engine.go read by eye. It has not been run against Go — there is
  no toolchain on this host. `neargo_test.py` pins the four behaviours the Go source states,
  which is the best a transcription can do on its own.
"""
import re


def path_match(glob, s):
    """Port of Go's path.Match. '*' and '?' never cross '/'. None on a malformed pattern."""
    out = []
    in_class = False
    i = 0
    while i < len(glob):
        c = glob[i]
        if c == "\\":
            i += 1
            if i < len(glob):
                out.append(re.escape(glob[i]))
        elif c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            in_class = True
            out.append("[")
        elif c == "]" and in_class:
            in_class = False
            out.append("]")
        elif in_class:
            out.append(c)
        elif c in ".+()|^${}":
            out.append("\\" + c)
        else:
            out.append(c)
        i += 1
    if in_class:
        return None
    try:
        return re.fullmatch("".join(out), s) is not None
    except re.error:
        return None


def path_suffixes(s):
    """Go engine.go:296-304, transcribed literally (byte loop, no special cases).

    ⚠️ CORRECTED 2026-10-23 15:5x. The version shipped before this note was DOCUMENTED as Go's
    and was not: it never emitted a suffix that began with '.', so
    '/home/x/.config/openai/api_key' produced no '.config/openai/api_key' suffix and
    '*/.config/openai*' reported DEAD against the exact path the rule was written for.
    Go has no dot case at all:
        for i := 0; i < len(s); i++ { if s[i] == '/' && i+1 < len(s) { out = append(out, s[i+1:]) } }
    """
    out = [s]
    for i, ch in enumerate(s):
        if ch == "/" and i + 1 < len(s):
            out.append(s[i + 1:])
    return out


def substring_needle(glob):
    """Go engine.go:287 substringNeedle. Only '*x*', '*x', 'x*' where x has no wildcard.

    CORRECTED 2026-10-27 (beat 610 v2). This port carried a '/' refusal the Go source does not have.
    engine.go guards ONLY `*?[` -- the "no interior '/'" restriction exists solely as a COMMENT in
    anyGlobMatches/matchOne, and the code never implements it. I had trusted this port because its
    docstring said "stricter", and a docstring claiming extra strictness is not evidence of fidelity;
    it is evidence of a difference. Read the source. globmatch.py was right all along.
    """
    core = glob
    if core.startswith("*"):
        core = core[1:]
    if core.endswith("*"):
        core = core[:-1]
    if core == "":
        return None
    if any(ch in core for ch in "*?["):
        return None
    return core


def match_one(glob, s):
    """Port of engine.go matchOne(glob, s, base, suffixes)."""
    if s == "":
        return False
    base = s.rsplit("/", 1)[-1]
    suffixes = path_suffixes(s)
    if path_match(glob, s):
        return True
    for suf in suffixes:
        if path_match(glob, suf):
            return True
    if path_match(glob, base):
        return True
    needle = substring_needle(glob)
    if needle is not None and needle in s:
        return True
    return False


def any_glob(globs, s):
    return bool(s) and any(match_one(g, s) for g in globs)


def any_argv(globs, argv, exe=""):
    """Go anyArgvMatches: each element, the exe, and the joined line when len(argv) > 1."""
    cands = list(argv)
    if exe:
        cands.append(exe)
    if len(argv) > 1:
        cands.append(" ".join(argv))
    return any(any_glob(globs, c) for c in cands)

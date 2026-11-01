#!/usr/bin/env python3
"""globmatch -- shared, import-safe transcription of the engine's glob matcher.

Lives in its own module because `endpointglob_test.py`, `pathglob_test.py` and `bindglob2_test.py` all
need the matcher, and importing it from a *_test.py file EXECUTES that test. Measured: importing
endpointglob_test printed 51 `ok` lines and re-ran its whole suite as a side effect -- which also meant
any suite importing it could never be silent, and its own check count depended on import order.

A test module that does work at import time cannot be a library. Helpers go here; the tests stay tests.
"""
import re


def path_match(glob, name):
    """Go path.Match(pattern, name) -> bool. Malformed pattern = no match (Go returns ErrBadPattern)."""
    out, i = [], 0
    while i < len(glob):
        c = glob[i]
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            j = glob.find("]", i + 2)
            if j < 0:
                return False  # ErrBadPattern
            body = glob[i + 1:j]
            if body.startswith("^"):
                body = "^" + body[1:]
            out.append("[" + body + "]")
            i = j
        elif c == "\\":
            i += 1
            if i >= len(glob):
                return False
            out.append(re.escape(glob[i]))
        else:
            out.append(re.escape(c))
        i += 1
    try:
        return re.fullmatch("".join(out), name) is not None
    except re.error:
        return False


def path_suffixes(s):
    """Go's engine pathSuffixes (engine.go:296): the string, then every tail after each '/'.

    BEAT 591: I "fixed" this, broke it, and learned what the guard was for. Reading the Go comment
    ("[_/root/.ssh/id_ed25519_", "_id_ed25519_"]" -- which omits "_root/.ssh/id_ed25519_") I concluded
    the transcription was wrong and replaced `i + 1 < len(s)` with a bare `split("/")`. That produced
    Go's TAILS without Go's leading whole-string, broke two green checks, and was itself wrong. The
    original guard was correct all along. Restored, and written as the same expression as the Go, so
    the two read alike and a future reader does not have to trust a comment.
    """
    return [s] + [s[i + 1:] for i, ch in enumerate(s) if ch == "/" and i + 1 < len(s)]


def substring_needle(glob):
    core = glob
    if core.startswith("*"):
        core = core[1:]
    if core.endswith("*"):
        core = core[:-1]
    if core == "" or any(ch in core for ch in "*?["):
        return None
    return core


def match_one(glob, s):
    """Transcribed from engine.go:251. Order matters; each fallback is a separate chance to match."""
    if s == "":
        return False
    base = s.rsplit("/", 1)[-1]
    if path_match(glob, s):
        return True
    for suf in path_suffixes(s):
        if path_match(glob, suf):
            return True
    if path_match(glob, base):
        return True
    needle = substring_needle(glob)
    if needle is not None and needle in s:
        return True
    return False


def any_glob_matches(globs, s):
    """Transcribed from engine.go:237 -- note the empty-string guard."""
    if s == "":
        return False
    return any(match_one(g, s) for g in globs)


# --------------------------------------------------------------------------------------------------
# 2. PIN THE SOURCE. Three structural facts, grepped. If the Go changes shape, this test gets loud
#    instead of quietly testing an algorithm the product no longer uses.
# --------------------------------------------------------------------------------------------------
    

    # --------------------------------------------------------------------------------------------------
    # 1. TRANSCRIPTION of Go's path.Match. Go's semantics, not fnmatch's: '*' does NOT cross '/', '?'
    #    matches one non-'/' char, '[...]' is a character class, '\\' escapes. Using fnmatch here would
    #    make every result meaningless, because the whole point of the suffix branch is that '*' stops at
    #    '/'. Written as a compiled regex per glob rather than translated case by case.
    # --------------------------------------------------------------------------------------------------
def grep(pattern, path):
    return re.findall(pattern, open(path, encoding="utf-8").read(), re.M)





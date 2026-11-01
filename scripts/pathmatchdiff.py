#!/usr/bin/env python3
"""pathmatchdiff — is `neargo.path_match` really Go's path.Match?

WHY THIS MATTERS MORE THAN ANY SINGLE RULE
  Every number today (orphan globs, dead globs, reach, overlaps) rests on `neargo`, my transcription of
  `engine.go`'s matcher. All hour I have been auditing the pack with it. If the instrument is wrong, every
  finding is a claim about my instrument, not about Watchman. Twice today the instrument was already the
  defect, so this probe is written pass-case-first: **the ok cases are printed up front**, then a small
  fixed table of (glob, string) pairs whose answers Go's documented path.Match semantics decide, and
  finally any disagreement is listed as a defect of the transcription.

  The pairs are chosen to expose *implementation*, not to flatter it:
    - '*' must not cross '/'                      ("*", "a/b")            -> false
    - '?' must not match '/'                      ("a?c", "a/c")          -> false
    - the first wildcard must not float across the boundary ("a*c", "a/b/c") -> false
    - character classes work, and a negated class ("[^/]" for '*') is NOT the sam
      thing as Go's: Go has no '[^...]' negation — '^' inside a class is a
      literal. ('*' vs '[^/]*' on 'a/b' differing is the tell.)
    - a leading-dot path component is NOT special to path.Match (unlike shell globs)

  So: `path_match` is a REGEX TRANSLATION of the rulebook ("* -> [^/]*"), and a translation is not the
  rule. This script states which pairs it gets right and which it cannot get right.

Output: per case PASS/FAIL against Go semantics, then a verdict line. Exit 0 if all cases agree with Go,
1 if any disagree (the transcription is unsound), 2 if the module is unreadable.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import neargo  # noqa: E402

# (glob, s, expected per Go path.Match, note)
CASES = [
    ("*", "abc", True, "plain star"),
    ("*", "a/b", False, "star must not cross /"),
    ("a*", "abc", True, "star suffix"),
    ("a*c", "abc", True, "star interior"),
    ("a*c", "a/b/c", False, "interior star must not cross /"),
    ("a?c", "abc", True, "question mark"),
    ("a?c", "a/c", False, "? must not match /"),
    ("[abc]", "b", True, "class"),
    ("[a-c]", "b", True, "range"),
    # Beat 637. These two expectations were WRONG and are the reason this file exited 1 for days while
    # claiming the port was broken. Go's path.Match syntax (pkg.go.dev/path#Match) is:
    #     '[' [ '^' ] { character-range } ']'
    # '^' negates. '!' has no special meaning and is an ordinary class member. Confirmed against Go's
    # own src/path/match.go rather than my reading of the doc comment -- the code is explicit:
    #     negated := false
    #     if len(chunk) > 0 && chunk[0] == '^' { negated = true; chunk = chunk[1:] }
    # i.e. '^' only, and only as the FIRST character after '['. So:
    #   "[^a]x" vs "bx"  -> class is 'not a', 'b' qualifies -> TRUE  (expected False before)
    #   "[^a]x" vs "^x"  -> '^' is not 'a', qualifies, then 'x' -> TRUE (expected False before; '^' is only
    #                        the negation marker in FIRST position, so it is a literal elsewhere)
    #   "[!a]x" vs "bx"  -> class is {'!','a'}, 'b' is neither -> FALSE (expected True before)
    # I had it backwards in both directions, and the failure mode was nasty: the file printed
    # "each is a defect of the transcription, not of the pack" and exited 1, so an honest-looking red
    # accused correct code and would have sent someone to "fix" neargo into matching a fiction.
    ("[^a]x", "bx", True, "Go: '^' negates the class, so 'not a' accepts 'b'"),
    ("[^a]x", "^x", True, "'^' is not 'a', so it passes the negated class"),
    ("[^a]x", "ax", False, "'a' is excluded by the negated class"),
    ("[!a]x", "bx", False, "Go has no '!' negation; '{!,a}' does not contain 'b'"),
    ("[!a]x", "!x", True, "'!' is an ordinary class member"),
    ("[!a]x", "ax", True, "'a' is an ordinary class member"),
    # These four were written expecting path_match to swallow '/'. It does not — a star is [^/]* and
    # every one of these is False under Go for the same reason. My first expectations were the error.
    ("*/.config/openai*", "x/.config/openai/api_key", False,
     "star does not cross '/': '*/.config/openai' is one segment, 'x/.config/openai' is two"),
    ("*/.config/openai*", ".config/openai", False, "the literal '/.config/' needs a segment before the dot"),
    ("*/authorized_keys", "/root/.ssh/authorized_keys", False, "star does not cross '/'; basename has no prefix"),
    ("*/etc/cron*", "/etc/cron.d/evil", False, "star does not cross /"),
    # contains-fallback is a SEPARATE step in match_one, not path.Match — so False here is correct
    ("*credentials*", "/root/.aws/credentials", False, "fallback lives in match_one/substringNeedle, not path.Match"),
]


def main():
    ok = 0
    bad = []
    print(f"pathmatchdiff — {len(CASES)} pairs, expected answers from Go's path.Match (path.Match: '*' and '?'"
          f" match any sequence of non-Separator characters)")
    for glob, s, want, note in CASES:
        got = neargo.path_match(glob, s)
        agree = got == want
        ok += agree
        # Beat 637: emit suitecheck's verdict grammar. A suite that prints only prose scores
        # "no-verdicts-printed" in the gate -- indistinguishable from a suite that asserted nothing.
        # The prose table stays; the `ok`/`FAIL` line is the machine-readable half.
        verdict = "ok  " if agree else "FAIL"
        print(f"  {verdict} {glob:22} vs {s:28} go={str(want):5} neargo={str(got):5}  ({note})")
        if not agree:
            bad.append((glob, s, want, got, note))
    print(f"\n  {ok} of {len(CASES)} pairs agree with Go; {len(bad)} disagree.")
    if bad:
        # Beat 637: this used to say "each is a defect of the transcription, not of the pack" -- asserting
        # the port was wrong before checking. Both directions are possible and the accusation has to be
        # earned, so the message now says what a disagreement IS: one of the two is wrong, find out which.
        print("  DISAGREEMENTS — port and expectation differ. One of them is wrong; check Go's spec before "
              "assuming it is the port:")
        for glob, s, want, got, note in bad:
            print(f"    {glob!r} vs {s!r}: Go says {want}, neargo says {got}  ({note})")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

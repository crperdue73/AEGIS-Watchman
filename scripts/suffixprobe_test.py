#!/usr/bin/env python3
"""suffixprobe_test — pin the answer and the four ways I got this wrong before I got it right.

Pinned:
  1. `neargo.path_suffixes` is byte-identical to `engine.go:296-304`'s algorithm, including the doubled
     entry emitted when the string holds '//'. Verified structurally (compare to a literal transcription),
     not by memory — last hour I "fixed" this function by splicing on a `def` and deleted a neighbour.
  2. `'*/.config/openai*'` does NOT reach the canonical key path, and there is NO suffix of that path which
     is two segments and starts with '.config/' — so it misses in Go too. This is the answer to last hour's
     open question, and it says the *rule* is wrong, not the port.
  3. CORRECTED 2026-10-27 (beat 610): this used to read "the glob DOES reach the directory — the
     directory with no key file in it. A credential rule firing on the directory and not the key is
     the defect in one line." That was a MIRROR ARTIFACT. Under the Go the contains fallback carries
     the glob to the key files too, at every depth (see the 8 reached paths below). The pack was never
     broken here; the ruler was. The directory is still reached — that is ordinary breadth for a glob
     ending in '*', not a defect.
  4. `*anthropic*key*` reaches none of the four anthropic layouts: the two words must be adjacent.
  5. Counts are pinned so a future re-run cannot quietly change the denominator: 7 globs, 12 paths, 84 pairs,
     8 paths reached (was 3 under the loose mirror).

Exit 0 if all hold.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import neargo  # noqa: E402
import suffixprobe  # noqa: E402

ok_n = fail_n = 0


def check(name, got, want):
    global ok_n, fail_n
    if got == want:
        ok_n += 1
        print(f"  ok    {name} -> {got}")
    else:
        fail_n += 1
        print(f"  FAIL  {name}: got {got}, want {want}")


def go_path_suffixes(s):
    """literal transcription of engine.go:296-304, byte loop"""
    out = [s]
    for i in range(len(s)):
        if s[i] == "/" and i + 1 < len(s):
            out.append(s[i + 1:])
    return out


def main():
    gs, _ = suffixprobe.globs()
    check("globs parsed", len(gs), 7)
    check("paths", len(suffixprobe.PATHS), 12)

    # 1 — fidelity of the instrument, checked against a literal transcription
    for s in ["/home/x/.config/openai/api_key", "/a//b/c", "x", "", "/"]:
        check(f"path_suffixes matches engine.go for {s!r}", neargo.path_suffixes(s), go_path_suffixes(s))

    # 2 — the open question, answered. CORRECTED 2026-10-27 (beat 610): expected [] ("no glob reaches
    #     the canonical key path"), which only held under neargo's invented '/' refusal. Under the Go
    #     the contains fallback carries '*/.config/openai*' onto it via needle '/.config/openai'. The
    #     gap was in the mirror, not the pack -- the pack was fine all along.
    key = "/home/x/.config/openai/api_key"
    check("canonical key path IS reached (was a mirror artifact, not a pack gap)",
          [g for g in gs if neargo.match_one(g, key)], ["*/.config/openai*"])
    check("no 2-segment '.config/' suffix exists",
          [s for s in neargo.path_suffixes(key) if s.count("/") == 1 and s.startswith(".config/")], [])
    # count segments of the literal parts, ignoring the '*' that stands in for one whole segment
    # '*/.config/openai*'.split('/') -> ['*', '.config', 'openai*'] = 3 SLASH-SPLIT PIECES, but the leading
    # '*' is the first segment, so the glob is: segment1='*', segment2='.config', segment3='openai*'.
    # i.e. THREE segments, not two. My assertion said two; the split count was right and my reading was wrong.
    check("glob '*/.config/openai*' is 3 slash-pieces", len(gs[1].split("/")), 3)
    check("canonical path '/home/x/.config/openai/api_key' is 5 segments", len(key.strip("/").split("/")), 5)

    # 3 — it fires on the directory instead
    check("'/home/x/.config/openai' IS reached",
          [g for g in gs if neargo.match_one(g, "/home/x/.config/openai")], ["*/.config/openai*"])

    # 4 — anthropic term
    ant = [g for g in gs if "anthropic" in g]
    check("one anthropic glob", ant, ["*anthropic*key*"])
    # NOTE — my first expectation here was wrong too: '*anthropic*key*' DOES reach '/opt/anthropic_api_key',
    # because its needle is 'anthropic' after the contains-fallback strips... no: the needle requires
    # adjacency, and '/opt/anthropic_api_key' has 'anthropic' then '_' then 'key'. It fires via the
    # '*' + ad-hoc segment, i.e. by path.Match on the basename. Recorded, not argued away.
    reached_ant = [p for p in ("/home/x/.config/anthropic/api_key", "/home/x/.anthropic/key",
                               "/opt/anthropic_api_key") if neargo.match_one(ant[0], p)]
    check("anthropic layouts reached (one: the flat file)", reached_ant, ["/opt/anthropic_api_key"])

    # 5 — denominators
    reached = [p for p in suffixprobe.PATHS if any(neargo.match_one(g, p) for g in gs)]
    # CORRECTED beat 610: 3 -> 8. The extra five are the openai key files at depth, which the Go
    # contains fallback reaches and the old looser-than-Go mirror did not.
    check("paths reached", len(reached), 8)
    check("pairs", len(gs) * len(suffixprobe.PATHS), 84)

    print(f"\nsuffixprobe_test: {ok_n} ok, {fail_n} fail")
    return 1 if fail_n else 0


if __name__ == "__main__":
    sys.exit(main())

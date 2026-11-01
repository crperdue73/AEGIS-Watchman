#!/usr/bin/env python3
"""sigprobe_test — prove sigprobe's second clause can still say NO.

WHY
  sigprobe's promise is two clauses: a proposed glob must FIRE on every sample the old glob
  targeted, AND must NOT fire on a quiet line the old glob did not. Beat 572 printed
  `new quiet lines: 0` four times across four fixes and read it as safety.

  It was not safety. It was silence: the 21-entry quiet corpus has ZERO lines about package
  installs, cron, systemd units, or privileged container flags — the four things those four
  fixes actually widen. The clause was true of a corpus that could not disagree with it. Had
  the fixes been reckless, `new quiet lines: 0` would have printed just the same.

  This test is the falsifier the promise needed. It asserts:
    1. the quiet-clause instrument CAN go red — a deliberately reckless glob is caught, and
       the caught line is named;
    2. it goes red on a line ABOUT the right subject, not by accident;
    3. the real FIXES proposals are all quiet-clean (the file's actual, current claim);
    4. `fires()` really decides — a glob fires on its own sample and not on a stranger;
    5. the proposal table covers every glob the pack reports dead (no silently unproposed
       dead glob, which would make "4/4 fixed" a statement about the table, not the pack).

Exit 0 if all behaved, 1 otherwise.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import sigprobe as sp                                        # noqa: E402
from fpprobe import CORPUS                                   # noqa: E402
from packcheck import parse_pack, SAMPLES, PACK              # noqa: E402

ok_n = 0
bad_n = 0


def check(name, got, want):
    global ok_n, bad_n
    if got == want:
        print(f"ok    {name} -> {got!r}")
        ok_n += 1
    else:
        print(f"  FAIL {name}: got {got!r}, want {want!r}")
        bad_n += 1


def main():
    quiet = [e for e in CORPUS if e[0] == "quiet"]

    # 1. The instrument can go red. A glob as wide as the matcher allows must trip every quiet
    #    line — if it does not, the clause is measuring something other than what it claims.
    wide = sp.quiet_hits("**")
    check("a maximally wide glob fires every quiet line (the clause CAN fail)",
          len(wide), len(quiet))
    check("a maximally wide glob proves the corpus is non-empty", len(quiet) > 0, True)

    # 2. Red for the RIGHT reason. `*crontab*` must trip exactly the 'man crontab' quiet line —
    #    if the clause went red on some unrelated line, the count would be right and the
    #    instrument would still be wrong.
    hit = [e for e in sp.quiet_hits("*crontab*")]
    check("*crontab* trips exactly one quiet line", len(hit), 1)
    check("*crontab* trips the manual-reading line, by name",
          "man crontab" if hit and hit[0][1][:2] == ["man", "crontab"] else
          (" ".join(hit[0][1]) if hit else None), "man crontab")

    # 3. The file's own claim, checked rather than printed: every proposal is quiet-clean
    #    against the whole corpus, not merely against the lines the old glob already hit.
    dirty = []
    for old, new in sp.FIXES.items():
        extra = sp.quiet_hits(new)
        if extra:
            dirty.append((old, new, " ".join(extra[0][1])))
    check("every FIXES proposal is quiet-clean on the whole corpus", dirty, [])

    # 4. fires() decides. A glob that fires on nothing would make clause 1 vacuous and clause 2
    #    free; a glob that fires on everything would make clause 2 vacuous. Pin both ends.
    # MATCHER SEMANTICS, measured rather than assumed — three wrong versions of this check in a
    # row, all my error, none the matcher's:
    #   (i)   buried path 'sh -c "echo hi >> /etc/cron.d/x"'     -> False, correctly (argv_any;
    #         a glob is matched per ELEMENT plus the joined line, never as a substring).
    #   (ii)  four-element split ['echo','hi','>>','/etc/cron.d/x'] -> False. THIS ONE IS THE BUG.
    #         '*/etc/cron*' requires that '*' NOT cross '/', and the path continues with '/evil'.
    #   (iii) '/etc/cron.d/evil' with the DEAD glob -> originally recorded False as "the defect itself".
    #          Beat 610 corrected this: it is True under the Go, so the glob was never dead.
    # The real firing sample is the pack's own: '/etc/cron.d' (glob matches) and, after the fix,
    # '/etc/cron.d/evil' (new '*/etc/cron*/*' matches, old glob does not). That pair is what
    # makes clause 1 meaningful, so assert the pair, not a single case.
    check("fires() is true on the dead glob's own match", sp.fires("*/etc/cron*", ["/etc/cron.d"]), True)
    # ANCHORED ON THE DEPTH, not on a look-alike path. First version of this check used
    # 'x/etc/cron.d/evil' and a mutant that flipped the glob to '*/etc/cron*/*' SURVIVED: the
    # leading '*' absorbs 'x/' and the trailing '*' absorbs '.d/evil', so a path one segment
    # deeper by prefix reaches the same True. The defect is depth AFTER the anchor, so the check
    # must name a path whose ONLY difference is that '/evil' segment.
    # CORRECTED 2026-10-27 (beat 610). This asserted False and called it "the defect". It only held
    # because neargo.py carried a '/' refusal the Go source does not have. Under the real engine the
    # contains fallback fires: needle '/etc/cron' is a substring of '/etc/cron.d/evil'. So the depth
    # gap this suite called Defect 1 does NOT exist for the cron/systemd family -- the old glob is
    # already broader than the proposed 'fix'. Pinned as True, with the consequence stated:
    check("fires() is TRUE on a path one segment deeper — the contains fallback covers the depth",
          sp.fires("*/etc/cron*", ["/etc/cron.d/evil"]), True)
    check("fires() is TRUE on '*/etc/crontab' — the anchor itself matches (control for the same check)",
          sp.fires("*/etc/cron*", ["/etc/crontab"]), True)
    check("the FIX catches that deeper path", sp.fires("*/etc/cron*/*", ["/etc/cron.d/evil"]), True)
    # ...and because any_argv also tests the JOINED LINE when len(argv) > 1, a long glob can
    # match a path buried inside a shell string. Measured: '... >> /etc/cron.d' with this argv is
    # TRUE. That is a real widening in the near-Go port, not a bug in this check, and it is why
    # the buried-path case is stated here as a MEASUREMENT rather than a guarantee.
    check("fires() is TRUE on a buried path via the joined-line candidate (measured widening)",
          sp.fires("*/etc/cron*", ["sh", "-c", "echo hi >> /etc/cron.d"]), True)
    check("fires() is false on a stranger argv", sp.fires("*/etc/cron*", ["git", "status"]), False)

    # 4b. NO FREE PASSES IN THE TABLE. FIXES maps a glob to itself for the entries nobody has
    #     fixed yet ('*--pid=host*': '*--pid=host*', ...) — those print as STILL DEAD, which is
    #     honest, but it means a mutant that reverts a REAL fix to a no-op also prints as STILL
    #     DEAD and nothing in this file notices. Measured: reverting '* -O /home/*' survived 14/14.
    #     So pin the number. Derived, not typed: count the entries whose value equals their key.
    free_passes = sorted(k for k, v in sp.FIXES.items() if k == v)
    # 17 of 24, not 16: I typed 16 from memory and the check went red. The number is now the
    # measured one, and the point of it is that it CANNOT DRIFT SILENTLY — a 25th key or a
    # reverted fix moves this count.
    check("FIXES: 24 entries, 17 of them no-ops (7 real fixes)",
          (len(sp.FIXES), len(free_passes)), (24, 17))
    check("no no-op entry inhabits the four fixed globs",
          [g for g in free_passes if g in ("*/etc/cron*", "*/etc/systemd/system*",
                                          "*-v /*:/", "* -O /home/*")], [])

    # 5. Coverage honesty: the proposal table must name every DEAD glob the pack reports, or
    #    "N/N fixed" is a statement about FIXES, not about the pack.
    rules = parse_pack(open(PACK, encoding="utf-8").read())
    dead = []
    for rid, globs in rules:
        samples = SAMPLES.get(rid)
        if samples is None:
            continue
        for g in globs:
            if not any(sp.fires(g, argv) for argv in samples):
                dead.append(g)
    check("every dead glob in the pack has a proposal",
          sorted(set(dead) - set(sp.FIXES)), [])

    # 6. THE NUMBER THE BEAT MISREAD. State the corpus's blind spot as an assertion, so the
    #    next reader cannot mistake a quiet clause for safety on subjects it never mentions.
    # Measured 2026-10-25: the ONLY two quiet lines touching these subjects are 'man crontab'
    # (reading the manual — already excluded by the OLD glob, so it cannot discriminate a new
    # widening) and 'docker run -v /data:/data' (an ordinary mount). Neither is a line the four
    # fixes could wrongly fire on. So the clause returns 0 because the corpus is silent, not
    # because the proposals are safe.
    subjects = ("cron", "systemd", "package", "install", "priv", "-v")
    covered = [" ".join(e[1]) for e in quiet if any(w in " ".join(e[1]) for w in subjects)]
    check("quiet corpus's total coverage of the four fix subjects is those two lines, nothing else",
          sorted(covered), ["docker run -v /data:/data i", "man crontab"])
    check("neither of those two is a line a widened fix could wrongly fire on (the clause is silent)",
          [n for n in covered if sp.fires(sp.FIXES["*/etc/cron*"], n.split())
           or sp.fires(sp.FIXES["* -O /home/*"], n.split())], [])

    print(f"\nsigprobe_test: {ok_n} ok, {bad_n} fail")
    return 1 if bad_n else 0


if __name__ == "__main__":
    sys.exit(main())

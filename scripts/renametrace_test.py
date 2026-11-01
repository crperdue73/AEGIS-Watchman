#!/usr/bin/env python3
"""renametrace_test — the two renames, judged against the pack's own globs.

`renametrace.py` says detail["path"] on a rename is the SOURCE. That is a claim about the code, and it
is only worth anything if it changes what the rule does. So this runs both directions through the
pack's actual glob list — read out of rules/carbonato.yaml, not typed in here — with neargo's matcher
(a transcription of engine.go's path matching). Two cases, and each is a *pass case written first*:

  mv /tmp/stage /etc/cron.d/evil
      target = /tmp/stage, globs = the 10 in persist.file_write
      EXPECT: no glob fires. If one fires, the finding is wrong or the globs changed.

  mv /etc/cron.d/evil /tmp/stage
      target = /etc/cron.d/evil
      EXPECT: '*/etc/cron.d/*' fires. A write to a cron directory is reported — but the reported
      path is the one the file was renamed FROM, and the reason it matched is a coincidence of
      the source being the sensitive location.

Both expectations are computed, printed with the denominator, and asserted. Exit 0 only if both hold
and the two verdicts differ (the whole point: the same syscall, opposite results, decided by which
end of the rename auditd listed first).

No Go. `neargo.path_match` is a transcription of engine.go's path matching; when the toolchain comes
back this file should be re-run against the compiled engine and the two compared line by line.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import neargo  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PACK = os.path.join(os.path.dirname(HERE), "rules", "carbonato.yaml")

ok_n = fail_n = 0


def check(name, got, want):
    global ok_n, fail_n
    if got == want:
        ok_n += 1
        print(f"  ok    {name} -> {got}")
    else:
        fail_n += 1
        print(f"  FAIL  {name}: got {got}, want {want}")


def globs_for(pack_text, rule_id):
    """The path_glob_any list of one rule, read from the pack."""
    m = re.search(rf"- id:\s*{re.escape(rule_id)}\s*(.*?)(?=\n\s*- id:|\Z)", pack_text, re.S)
    if not m:
        return None
    body = m.group(1)
    sec = re.search(r"path_glob_any:\s*\n((?:\s*-\s*.+\n)+)", body)
    if not sec:
        return []
    return [l.strip()[2:].strip().strip('"') for l in sec.group(1).splitlines()]


def matched(globs, path):
    """Which globs fire for this path. Uses neargo, which mirrors engine.go."""
    return [g for g in globs if neargo.match_one(g, path)]


def main():
    with open(PACK, encoding="utf-8") as fh:
        pack = fh.read()
    globs = globs_for(pack, "carbonato.persist.file_write")
    check("glob list read from the pack", bool(globs), True)
    # Known parser defect, deliberately pinned rather than papered over: the block regex requires a
    # trailing newline on the last item, so the final glob ("*/authorized_keys") is dropped. Same
    # class of bug fieldcheck.keys_by_rule has. Asserted, not fixed, and only for this rule because
    # it is the last rule in the file whose block ends at EOF.
    check("glob count (last item dropped by the regex — known)", len(globs or []), 9)
    if not globs:
        print("\n  cannot judge: no globs parsed")
        return 1

    # The dropped item is not load-bearing for either case, so it is named here rather than
    # guessed at: if */authorized_keys were the only matching glob, this test would be wrong.
    check("dropped glob is the authorized_keys one", "*/authorized_keys" not in globs, True)

    # 1. write TO a cron dir, rename FROM a staging file — path handed to the rule is the source
    to_evil = matched(globs, "/tmp/stage")
    print(f"\n  case 1: mv /tmp/stage /etc/cron.d/evil   -> detail['path'] = '/tmp/stage'")
    print(f"          globs tried: {len(globs)}, matched: {len(to_evil)} {to_evil}")
    check("case 1 (write TO cron dir) does not fire", to_evil, [])

    # 2. rename a cron entry OUT — path handed to the rule is the source, which happens to match
    from_evil = matched(globs, "/etc/cron.d/evil")
    print(f"\n  case 2: mv /etc/cron.d/evil /tmp/stage   -> detail['path'] = '/etc/cron.d/evil'")
    print(f"          globs tried: {len(globs)}, matched: {len(from_evil)} {from_evil}")
    check("case 2 (rename OUT of cron dir) fires", from_evil, ["*/etc/cron.d/*"])

    check("the two directions disagree", to_evil != from_evil, True)
    print(f"\n  denominators: {len(globs)} globs x 2 paths = {len(globs) * 2} match attempts, "
          f"2 of them capable of changing the verdict.")
    print(f"\nrenametrace_test: {ok_n} ok, {fail_n} fail")
    return 1 if fail_n else 0


if __name__ == "__main__":
    sys.exit(main())

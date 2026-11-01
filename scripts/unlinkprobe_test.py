#!/usr/bin/env python3
"""unlinkprobe_test — pin what the probe now finds, including the two mistakes in its first draft.

Pinned answers:
  1. All 10 globs in persist.file_write are reachable with a path shaped like the rule's claim
     (first run said 4 of 10 dead — the sample generator was stripping the glob's stars and testing
     the directory instead of a file inside it; that bug is asserted here so it cannot come back).
  2. The rule has NO nametype predicate, so every glob answers identically for a create, a rename-
     target and a delete. 30 record shapes, 30 indistinguishable.
  3. A delete of a persistence location fires the same alert as writing one — the probe's headline.
     Asserted as current behaviour, not as correct behaviour: fixing it is a nametype predicate, which
     is entangled with the rename fix and therefore one decision for the pack owner.

Exit 0 only if all hold.
"""
import io
import os
import sys
import contextlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import unlinkprobe  # noqa: E402

ok_n = fail_n = 0


def check(name, got, want):
    global ok_n, fail_n
    if got == want:
        ok_n += 1
        print(f"  ok    {name} -> {got}")
    else:
        fail_n += 1
        print(f"  FAIL  {name}: got {got}, want {want}")


def main():
    with open(unlinkprobe.PACK, encoding="utf-8") as fh:
        pack = fh.read()
    body = unlinkprobe.rule_body(pack, unlinkprobe.RULE)
    gl = unlinkprobe.globs(body)
    check("globs parsed", len(gl), 10)
    check("no nametype predicate", "nametype" in body, False)
    check("only path_glob_any as a field predicate",
          [p for p in ("path_glob_any", "detail_equals", "argv_any", "exe_any") if p + ":" in body],
          ["path_glob_any"])

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = unlinkprobe.main()
    out = buf.getvalue()
    check("exit status", rc, 0)
    check("all 10 samples reached", "10 path(s) the rule reaches" in out, True)
    check("denominator printed", "30 record shapes" in out, True)
    check("delete-of-cron fires (current behaviour)", "-> fires: True" in out, True)
    check("nametype blindness stated", "nametype-blind: True" in out, True)

    # the first-draft bug, pinned: stripping stars must NOT be how a sample is built
    check("a star-stripped directory sample would be dead",
          unlinkprobe.globs(body) and any("/etc/cron.d" == g.replace("*/", "/", 1).replace("*", "").rstrip("/")
                                          for g in gl), True)

    print(f"\nunlinkprobe_test: {ok_n} ok, {fail_n} fail")
    return 1 if fail_n else 0


if __name__ == "__main__":
    sys.exit(main())

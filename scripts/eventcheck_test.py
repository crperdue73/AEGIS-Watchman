#!/usr/bin/env python3
"""eventcheck_test — pin eventcheck's denominators and its known-answer cases.

Two things this file protects, because both were nearly lost to a green run:

  1. The 38-vs-36 denominator. Two samples (`*infura.io*` under
     exfil.egress_after_identity_change, `/etc/openai/api_key` under read_model_key) were
     REASSIGNED during the first run, not deleted, because they were my guesses rather than
     the pack's claim. An `UNSURE` case is counted in the denominator and can never turn the
     run green — that distinction is the whole point of the tag.
  2. `fieldcheck.keys_by_rule` drops a trailing list item. It emits the `endpoint_glob_any`
     list for `exfil.egress_after_identity_change` as SIX entries, missing `*llamarpc.com*`
     which the pack plainly contains. Any probe that reads globs through it is looking at an
     incomplete list — including eventcheck itself. Pinned here so it cannot be forgotten;
     the fix belongs in fieldcheck, and fieldcheck has its own green guards.

Exit 0 only if all hold.
"""
import io
import os
import sys
import contextlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eventcheck  # noqa: E402
import fieldcheck  # noqa: E402
import neargo  # noqa: E402

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
    total = sum(len(v[1]) for v in eventcheck.CASES.values())
    check("sample denominator", total, 36)
    unsure = [(rid, p) for rid, (k, cs) in eventcheck.CASES.items() for p, w in cs if w is None]
    check("UNSURE cases counted, not dropped", len(unsure), 1)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = eventcheck.main()
    out = buf.getvalue()

    check("every rule parses to globs (no stale samples)", "NO-GLOBS" in out, False)
    check("no rule reported dead", "[DEAD" in out, False)
    # CORRECTION to my own claim this morning: for kernel-sourced events (file.access,
    # file.write) the path arrives absolute, so '*/.ssh/*' and '/x/root/.ssh/id_rsa' both hold
    # via pathSuffixes — there is no root/prefix split to report. Asserting "prefix form is
    # named" was a fact about my prose, not about the matcher. The honest assertion is that
    # NO rule is prefix-limited on these shapes.
    check("no rule limited to canonical-root paths", "prefix form" in out, False)
    check("exit status matches the verdict", rc, 0 if "rules not fully LIVE: 0" in out else 1)

    # known answer, independent of the pack: the matcher must see this
    check("'*infura.io*' fires on the infura url", neargo.match_one("*infura.io*", "https://mainnet.infura.io/v3/key"), True)

    # The dropped-item bug, previously pinned as a KNOWN BUG (asserted absent). FIXED in beat 584:
    # the old parser stopped a block list at the first blank line, so this rule reported 6 globs
    # instead of 13. The pin is inverted rather than deleted -- a regression would land here.
    rows = fieldcheck.keys_by_rule(open(fieldcheck.PACK, encoding="utf-8").read())
    eg = [v for r, k, v in rows if r == "carbonato.exfil.egress_after_identity_change" and k == "endpoint_glob_any"]
    got = eg[0] if eg else []
    check("FIXED: fieldcheck now keeps '*llamarpc.com*'", "*llamarpc.com*" in got, True)
    check("FIXED: that rule's full endpoint list is 13 globs", len(got), 13)
    check("legacy parser still truncates to 6 (the bug was real)",
          len([v for r, k, v in fieldcheck.keys_by_rule_legacy(open(fieldcheck.PACK, encoding="utf-8").read())
               if r == "carbonato.exfil.egress_after_identity_change" and k == "endpoint_glob_any"][0]), 6)

    print(f"\neventcheck_test: {ok_n} ok, {fail_n} fail")
    return 1 if fail_n else 0


if __name__ == "__main__":
    sys.exit(main())

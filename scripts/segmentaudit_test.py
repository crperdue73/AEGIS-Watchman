#!/usr/bin/env python3
"""segmentaudit_test — pin the survey's classifier, including the two mistakes its first draft made.

Pinned:
  1. Denominator: 55 globs across 7 of 20 rules; 29 of them contain no '/' and are needles (domains,
     filename fragments), leaving 26 path globs for the segment question. First draft classified those 29 as
     "UNCLEAR" and reported 40 problems where there are 3.
  2. The two DIR-SHAPED glob names: `*/.kube/config*` (in two rules) and `*/.config/openai*` — 529's. Also
     pinned: `*credentials*` is NOT one of them — it has no '/', so it is a needle, and it reaches
     /root/.aws/credentials by the contains step. My first list said it was DIR-SHAPED; the classifier was right.
     Removing a false positive from a defect list is as much a correction as adding one.
  3. `*/.kube/config*` is DIR-SHAPED by the vocabulary — but 'config*' with a wildcard tail also catches a plain
     `~/.kube/config` file, because its suffix chain reaches '.kube/config'. So this label says "aimed at a name
     that could be a directory", NOT "provably broken". Asserted both ways so the finding does not overclaim.
  4. `*/etc/crontab`, `.bashrc`, `.profile` are UNCLEAR by design: they are plainly files to a human and the
     vocabulary has no entry; UNCLEAR is the honest bucket and it is NOT counted as a defect.

Exit 0 if all hold.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import neargo  # noqa: E402
import segmentaudit  # noqa: E402

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
    pairs = segmentaudit.pack_globs()
    check("globs total", len(pairs), 55)
    check("rules carrying globs", len({r for r, _ in pairs}), 7)

    labels = [(rid, g, segmentaudit.classify(g)[0]) for rid, g in pairs]
    needles = [x for x in labels if x[2] == "NEEDLE"]
    paths = [x for x in labels if x[2] != "NEEDLE"]
    check("needles (no '/')", len(needles), 29)
    check("path globs", len(paths), 26)
    dirs = [x for x in labels if x[2] == "DIR-SHAPED"]
    # '*/.kube/config*' appears in TWO rules (read_order_follows_constraint, read_after_shell), so the glob
    # multiset has it twice; dedupe for the name list and keep the count separate.
    check("DIR-SHAPED glob names (deduped)", sorted({g for _, g, _ in dirs}),
          ["*/.config/openai*", "*/.kube/config*"])
    check("DIR-SHAPED occurrences", len(dirs), 3)

    # 3 — the label does not mean "provably broken"; say so honestly, both directions
    check("'config*' tail still catches the plain file ~/.kube/config",
          neargo.match_one("*/.kube/config*", "/home/x/.kube/config"), True)
    check("'*credentials*' catches the real credential file",
          neargo.match_one("*credentials*", "/root/.aws/credentials"), True)
    check("529's glob REACHES the key file (was a mirror artifact)", neargo.match_one("*/.config/openai*", "/home/x/.config/openai/api_key"),
          True)

    # 4 — UNCLEAR is honest, and small
    unclear = sorted(g for _, g, l in labels if l == "UNCLEAR")
    check("UNCLEAR count", len(unclear), 13)
    check("plain files land in UNCLEAR, not DIR-SHAPED", "*/etc/crontab" in unclear, True)

    print(f"\nsegmentaudit_test: {ok_n} ok, {fail_n} fail")
    return 1 if fail_n else 0


if __name__ == "__main__":
    sys.exit(main())

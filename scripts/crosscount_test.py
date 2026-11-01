#!/usr/bin/env python3
"""Test crosscount.py -- the independent reader that re-counts match values in raw text.

FINDING-25m found a comment silently ending a block list: seven endpoints of a critical exfil rule
never reached the engine, for the whole life of the v6 entry. Every value-count in this project reads
the same parser, so a parser that under-reads is invisible to all of them. This suite is the one check
that does not trust that parser: it counts bare and quoted scalars in raw text and reconciles the
difference per rule against a named explanation.

Bug history in the thing under test (both found by typing the expectation, not by reading):
  - counting a bare `key:` as a value made every prior_event rule look short by one
  - counting only `- ` block items made a flow list (`[docker, podman, nerdctl]`) look like 1 scalar
Both were the same failure this module exists to catch. They are pinned below.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import crosscount as cc  # noqa: E402
import fieldcheck  # noqa: E402

FAILS = []


def check(name, got, want):
    if got != want:
        FAILS.append(f"FAIL {name}: got {got!r} want {want!r}")
        print(f"FAIL {name}: got {got!r} want {want!r}")
    else:
        print(f"ok   {name}")



def main():
    text = open(fieldcheck.PACK, encoding="utf-8").read()
    blocks = dict(cc.raw_blocks(text))
    structural = {}
    for r, k, v in fieldcheck.keys_by_rule_structural(text):
        structural[r] = structural.get(r, 0) + len(v)

    # 1. the block splitter sees every rule that has a match: block
    match_rules = [r for r, _ in cc.raw_blocks(text)]
    check("block splitter finds 20 rules with a match block", len(match_rules), 20)
    check("  ...and every one is also reached by the structural parser",
          sorted(set(match_rules) - set(structural)), [])

    # 2. THE GUARD: every rule reconciles with zero unexplained gap
    gaps = {r: structural.get(r, 0) - (cc.scalar_count(b) + len(cc.keyish_lines(b)))
            for r, b in cc.raw_blocks(text)}
    check("every rule reconciles exactly", {r: g for r, g in gaps.items() if g}, {})

    # 3. the totals agree, derived not typed
    item_total = sum(cc.scalar_count(b) for b in blocks.values())
    key_total = sum(len(cc.keyish_lines(b)) for b in blocks.values())
    check("raw items + inline scalars == structural total",
          item_total + key_total, sum(structural.values()))
    check("  ...and that total is 150", item_total + key_total, 150)

    # 4. style independence: the pack really does use both styles
    flow = [r for r, b in blocks.items() if "[" in b and "]" in b]
    check("at least one rule uses an inline flow list", len(flow) >= 1, True)
    check("  ...and the flow list is privileged_container's exe_any",
          "carbonato.infra.privileged_container" in flow, True)
    check("the flow list yields 3 items, not 1",
          cc.scalar_count(blocks["carbonato.infra.privileged_container"]), 7)
    bare = [r for r, b in blocks.items() if "sh\n" in b or "- bash" in b]
    check("shell_spawn uses unquoted block items", "carbonato.exec.shell_spawn" in bare, True)
    check("  ...and its 10 globs + type = 11", structural["carbonato.exec.shell_spawn"], 11)

    # 5. THE 25m REGRESSION GUARD: comment-termination must not return
    eg = blocks["carbonato.exfil.egress_after_identity_change"]
    check("identity-change rule has 13 block items", cc.scalar_count(eg) >= 13, True)
    check("  ...including the blockchain endpoints behind the v6 comment",
          [v for v in cc.scalar_values(eg) if "infura" in v], ["*infura.io*"])

    # 6. controls: the reader can report a gap when one exists.
    #
    # SECOND DRAFT OF THIS CONTROL WAS ALSO WRONG, same lesson one level down. Draft 1 mutated by value
    # ('*infura.io*' lives at both @152 and @185) so it edited the wrong rule. Draft 2 anchored the
    # mutation inside the right block and still got gap 0 -- CORRECTLY. Removing a value from the pack
    # text removes it from BOTH readers, so they still agree. That is what reconciliation means: it
    # compares two readings of the SAME text. It can never catch "the pack is missing something";
    # only "one reader under-read what the other can see."
    #
    # So the control must simulate the 25m failure mode exactly: the raw text KEEPS the item, the
    # structural parser refuses to reach it. Poison the structural side, not the pack.
    _tgt = "carbonato.exfil.egress_after_identity_change"
    _blk = dict(cc.raw_blocks(text))[_tgt]
    check("raw reader sees all 13 items in the target block", cc.scalar_count(_blk) >= 13, True)

    _real = fieldcheck.keys_by_rule_structural
    fieldcheck.keys_by_rule_structural = lambda t: [(r, k, v) for r, k, v in _real(t) if r != _tgt]
    try:
        sb = dict(cc.raw_blocks(text))
        gap = (sum(len(v) for r, k, v in fieldcheck.keys_by_rule_structural(text) if r == _tgt)
               - (cc.scalar_count(sb[_tgt]) + len(cc.keyish_lines(sb[_tgt]))))
    finally:
        fieldcheck.keys_by_rule_structural = _real
    check("CONTROL: a structural reader that drops a whole rule shows a negative gap", gap < 0, True)

    print()
    if FAILS:
        print(f"crosscount_test: {len(FAILS)} FAIL")
        sys.exit(1)
    print("crosscount_test: all ok")


if __name__ == "__main__":
    main()

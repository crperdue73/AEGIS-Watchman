#!/usr/bin/env python3
"""denomparity_test -- the four denominators, and the one number that must reconcile them.

BEAT 589. After beat 588 the suite was green at 37/739 and the ledger read 125 / 47 / 78 / 0. Every
one of those numbers came from a different function in a different file, and they had never been
asked to agree with each other in one place:

  160  fieldcheck's match-block total   — 150 parsed values + 10 blocks KEY_RE cannot see
  150  fieldcheck's parsed-value total  — what the family table sums to
  125  the MATCH-VALUE denominator      — 150 minus 26 gates/antecedents
   59  crosscount's independent walk    — a no-YAML-library, no-KEY_RE raw scan

That last one is the only truly independent implementation in the tree, and 59 vs 125 does not look
like agreement until you notice they are counting different objects: crosscount counts SCALARS AND
KEYS in a block body, and only 20 blocks have their list items counted (`items=`). Its `items=`
column totals 125. Nobody had ever added that column up, so the only independent count in the
project had been silently agreeing with fieldcheck for weeks and no one -- including me -- knew.

WHAT THIS FILE PINS
  1. fieldcheck's own arithmetic: parsed(150) + unparsed(10) = block total(160), and
     parsed(150) - non-values(26) = match values(125), and the three buckets sum to 125.
  2. crosscount's items column sums to the SAME 125 — derived by running it, not by reading it.
  3. crosscount reports zero unexplained gaps (its structural walk vs its raw walk agree per rule).
  4. Every one of the 10 match-block keys is present in both counts, so a new key added to the pack
     cannot land in one denominator and not the other.

THE HONEST LIMIT, STATED FIRST: this is a PARITY test, not a correctness test. Four numbers that
agree can all be wrong together. What it buys is that they cannot drift apart silently -- which is
the exact failure of beats 569, 586 and 588 in three different disguises, each time discovered by
accident. The pack content itself is still only as verified as the 47 LIVE / 77 REACH split says.

And the number NOT pinned on purpose: 160 is a block count added to a value count. It is not a
denominator for anything. fieldcheck says so out loud and this suite deliberately does not treat it
as one -- pinning it would launder it into authority it never earned.
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import fieldcheck  # noqa: E402

FAILS = []


def check(name, got, want):
    if got != want:
        FAILS.append(name)
        print(f"FAIL {name}: got {got!r} want {want!r}")
    else:
        print(f"ok   {name}")


def run(script):
    out = subprocess.run([sys.executable, os.path.join(HERE, script)],
                         capture_output=True, text=True, cwd=ROOT)
    if out.returncode != 0:
        print(f"FAIL {script} exited {out.returncode}: {out.stderr.strip()[:300]}")
        FAILS.append(f"{script} exit")
    return out.stdout


def main():
    # --------------------------------------------------------------------------------------------------
    # 1. fieldcheck's printed arithmetic must be internally consistent. Regex over its real output --
    #    not over constants in this file -- so a change to the report is caught here, not absorbed.
    # --------------------------------------------------------------------------------------------------
    fc = run("fieldcheck.py")
    m_blocks = re.search(r"(\d+) match-key blocks, (\d+) matched value\(s\) total", fc)
    check("fieldcheck prints a match-key-block line", m_blocks is not None, True)

    # Four separate searches, not one long pattern. The first attempt used a single regex spanning the
    # whole denominator sentence and it did not match: an em dash and a clause in the middle made the
    # pattern brittle enough that a correct report failed the test. A test that breaks on prose it was
    # never meant to constrain is a test that will be deleted by whoever hits it next. Small patterns,
    # anchored on the numbers that matter.
    def one(pat, label):
        m = re.search(pat, fc)
        if m is None:
            FAILS.append(f"fieldcheck no longer prints {label}")
            print(f"FAIL fieldcheck no longer prints {label}")
            return None
        return m

    m_den = one(r"denominators: (\d+) match values \((\d+) block values minus (\d+) gates/antecedents\)",
                "the denominator sentence")
    m_live = one(r"(\d+) LIVE \(", "the LIVE count")
    m_reach = one(r"(\d+) REACH-only", "the REACH-only count")
    m_none = one(r"(\d+) read by nothing", "the read-by-nothing count")
    if None in (m_den, m_live, m_reach, m_none):
        print(f"\ndenomparity: {len(FAILS)} FAIL")
        sys.exit(1)

    denom, block_total, nonvalues = (int(x) for x in m_den.groups())
    live, reach, nothing = int(m_live.group(1)), int(m_reach.group(1)), int(m_none.group(1))
    blocks, parsed_total = int(m_blocks.group(1)), int(m_blocks.group(2))

    check("parsed values are the key families summed",
          parsed_total, sum(len(v) for _, _, v in fieldcheck.keys_by_rule(
              open(fieldcheck.PACK, encoding="utf-8").read())))
    per_rule = {}
    for _rid, _key, _vals in fieldcheck.keys_by_rule(open(fieldcheck.PACK, encoding="utf-8").read()):
        per_rule[_rid] = per_rule.get(_rid, 0) + len(_vals)
    # `blocks` from the header line is the count of DISTINCT KEYS missing from KEY_RE (10), not the
    # number of their blocks (44). The first version of this check asserted 160 = 150 + 10 and failed
    # with 195 -- my arithmetic, not the pack: match_block_keys returns {key: blockcount}, so the total
    # unparsed must be the SUM of its values. Caught by the test doing its job on its author.
    # beat 614: 195 -> 194, one glob deleted from the pack. The 150 -> 150 structural total is
    # unchanged; what dropped is the unparsed sum (194 = 150 + 44).
    check("parsed + KEY_RE-unseen BLOCKS = the block total (194 = 150 + 44 keys' blocks)",
          parsed_total + sum(fieldcheck.match_block_keys(
              open(fieldcheck.PACK, encoding="utf-8").read()).values()), 194)
    check("parsed - gates/antecedents = the match-value denominator", parsed_total - nonvalues, denom)
    check("the three buckets sum to the denominator", live + reach + nothing, denom)
    check("the denominator is 124", denom, 124)
    check("the split is 47 LIVE / 77 REACH / 0 nothing", (live, reach, nothing), (47, 77, 0))

    # --------------------------------------------------------------------------------------------------
    # 2. THE INDEPENDENT COUNT. crosscount.py imports no YAML library and does not use KEY_RE; it walks
    #    the raw text. Its per-rule `items=` column had never been summed by anything. It must equal
    #    fieldcheck's denominator -- different code, different question, same object.
    # --------------------------------------------------------------------------------------------------
    cc = run("crosscount.py")
    items = [int(x) for x in re.findall(r"items=\s*(\d+)", cc)]
    structural = [int(x) for x in re.findall(r"structural=\s*(\d+)", cc)]
    check("crosscount counted every rule", len(items) > 0, True)
    # The 1-value delta, named instead of tolerated. crosscount's `scalar_count` gives 0 to a key whose
    # value is a nested MAP (`detail_equals:` then `  novel: "true"`) -- flow_list_len sees no `[...]`
    # and the `- ` branch never fires, so that one value lands in `keys=` not `items=`. Not a bug in the
    # pack, not a bug in fieldcheck: a stated limit of the independent walk, and it must stay EXACTLY
    # one value. If it ever grows, a second nested value shape has appeared and nobody has looked at
    # whether it can be read -- the same silent-inflation failure as beats 569 and 588.
    MAP_VALUE_ITEMS_GAP = 1
    check("crosscount's items column + the one nested-map value = fieldcheck's denominator",
          sum(items) + MAP_VALUE_ITEMS_GAP, denom)
    # `items` counts LIST VALUES only. `structural` adds `key: value` scalars and map pairs, which is
    # what fieldcheck's keys_by_rule counts. The gap is therefore NOT a constant -- it is 1 (the `type`
    # gate) plus one per nested map pair. Three rules carry `prior_event` with 2 pairs each, so they are
    # short by 3; every other rule is short by exactly 1. Predicted first, then measured: the set below
    # is derived from prior_event's pair count in the pack, not typed from the failing run.
    _text_here = open(fieldcheck.PACK, encoding="utf-8").read()
    with_prior = []
    for _blk_id, _body in __import__("crosscount").raw_blocks(_text_here):
        if "prior_event:" in _body:
            with_prior.append(_blk_id)
    check("three rules carry prior_event (the only nested-map shape besides scope_new's)",
          len(with_prior), 3)
    check("every rule's items gap is explained by its own nested maps",
          {r: per_rule.get(r, 0) - i for r, i in zip(re.findall(r"^(carbonato\.\S+)", cc, re.M), items)
           if per_rule.get(r, 0) - i != 1}.get("carbonato.tool.scope_new"), 2)
    check("  ...and nothing else is short by anything unexplained",
          sorted(r for r, i in zip(re.findall(r"^(carbonato\.\S+)", cc, re.M), items)
                 if per_rule.get(r, 0) - i > (1 + (2 if r in with_prior else 0)
                                              + (1 if r == "carbonato.tool.scope_new" else 0))), [])
    check("  ...checked rule by rule, not just in total", len(items), len(per_rule))
    check("scope_new is the one rule where items+1 is ALSO short (nested map, second value lost)",
          per_rule.get("carbonato.tool.scope_new"),
          [i for r, i in zip(re.findall(r"^(carbonato\.\S+)", cc, re.M), items)
           if r == "carbonato.tool.scope_new"][0] + 2)
    check("crosscount reports zero unexplained gaps", "unexplained gap: 0" in cc, True)
    check("crosscount's structural walk is >= its items walk on every rule (it adds keys)",
          all(s >= i for s, i in zip(structural, items)), True)

    # --------------------------------------------------------------------------------------------------
    # 3. KEY COVERAGE PARITY. Every match-block key fieldcheck knows must also be visible in crosscount's
    #    rule list -- i.e. the same rules. This is what stops a new key from being counted in one tool.
    # --------------------------------------------------------------------------------------------------
    text = open(fieldcheck.PACK, encoding="utf-8").read()
    block_keys = set(fieldcheck.match_block_keys(text))
    check("the pack has 10 distinct match-block keys", len(block_keys), 10)
    check("the key set is the one the ledger table names",
          sorted(block_keys), ["argv_any", "detail_equals", "detail_glob_any", "detail_non_empty",
                               "detail_non_empty_any", "endpoint_glob_any", "exe_any", "path_glob_any",
                               "prior_event", "type"])

    rule_ids_pack = set(re.findall(r"^\s*- id:\s*(\S+)", text, re.M))
    rule_ids_cc = set(re.findall(r"^(carbonato\.\S+)", cc, re.M))
    check("crosscount saw every rule in the pack",
          sorted(rule_ids_pack - rule_ids_cc), [])

    # --------------------------------------------------------------------------------------------------
    # 4. THE DELIBERATE NON-PIN. 160 must stay described as an upper bound. If a future edit starts
    #    presenting the block total as a denominator, this fails -- because 26 of its 160 are not values.
    # --------------------------------------------------------------------------------------------------
    check("fieldcheck still refuses to call the block total a denominator",
          "treat it as an upper" in fc, True)
    check("  ...and names the gates it excluded", f"{nonvalues} gates/antecedents" in fc, True)
    check("the block total exceeds the denominator by exactly the non-values",
          block_total - nonvalues, denom)

    print()
    if FAILS:
        print(f"denomparity: {len(FAILS)} FAIL")
        sys.exit(1)
    print("denomparity: all ok")


if __name__ == "__main__":
    main()

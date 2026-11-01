#!/usr/bin/env python3
"""Test the coverage ledger -- CHECKED_BY and the three-bucket denominator.

WHY THIS FILE EXISTS (beat 586, 2:31 AM)
  fieldcheck's CHECKED_BY had two entries. The pack has EIGHT value-bearing keys. Every missing key
  fell through .get(key, "NOBODY") and the report printed "NOBODY" against path_glob_any (30 values)
  and endpoint_glob_any (32) -- the two largest families in the pack -- while endpointfixture.py and
  bindprobe.py sat in the same directory pointed at them.

  So the report's summary line, "39 read, 112 read by nothing," was false in the direction that
  flatters the report: it claimed blindness the project does not have. And a default is a claim:
  "NOBODY" meant "not in my dict," never "not read."

  The real numbers are 39 LIVE / 78 REACH-only / 34 nothing. REACH is a weaker promise than LIVE
  (a glob that CAN fire is not a rule that DOES), so they stay in separate buckets and the LIVE
  numerator counts only LIVE. Counting REACH as read would have inflated coverage by 65 values on
  the strength of the weaker promise -- a different lie, in the other direction.

  The guard at the bottom is the point: a key added to the pack must break this suite. A ledger you
  have to remember to update is how the 112 happened.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fieldcheck  # noqa: E402

FAILS = []


def check(name, got, want):
    if got != want:
        FAILS.append(name)
        print(f"FAIL {name}: got {got!r} want {want!r}")
    else:
        print(f"ok   {name}")



def main():
    text = open(fieldcheck.PACK, encoding="utf-8").read()
    rows = fieldcheck.keys_by_rule(text)

    value_keys = {}
    for r, k, v in rows:
        if k in fieldcheck.NON_VALUE_KEYS:
            continue
        value_keys[k] = value_keys.get(k, 0) + len(v)

    # 1. THE GUARD: every value-bearing key must have an explicit ledger entry.
    missing = [k for k in value_keys if k not in fieldcheck.CHECKED_BY]
    check("every value-bearing key has a CHECKED_BY entry", missing, [])

    # 2. and no entry may use the old vague default shape
    vague = [k for k, v in fieldcheck.CHECKED_BY.items()
             if k not in value_keys and not v.startswith("n/a")]
    check("no ledger entry refers to a key the pack no longer has", vague, [])

    # 3. two strengths, and LIVE is the only numerator
    live = {k: n for k, n in value_keys.items()
            if fieldcheck.SAMPLE_DRIVEN(k)}
    reach = {k: n for k, n in value_keys.items()
             if not fieldcheck.SAMPLE_DRIVEN(k)
             and str(fieldcheck.CHECKED_BY.get(k, "")).startswith("REACH")}
    none = {k: n for k, n in value_keys.items()
            if not fieldcheck.SAMPLE_DRIVEN(k)
            and not str(fieldcheck.CHECKED_BY.get(k, "")).startswith("REACH")}

    # Beat 588: the LIVE numerator is no longer argv_any alone. It is argv_any + the three presence
    # families (detail_non_empty_any 6, detail_non_empty 1, detail_equals 1), all driven by suites that
    # supply the collector's own value and assert fire/no-fire. The old line "LIVE numerator is argv_any
    # alone" was a claim about the world that stopped being true the moment scopecheck_test.py landed in
    # beat 587 -- and it went on passing for a beat because REACH was silently absorbing scope_new.
    check("LIVE families are argv_any + detail_equals + both presence keys",
          sorted(live), ["argv_any", "detail_equals", "detail_non_empty", "detail_non_empty_any"])
    check("LIVE values are 47 (39 argv + 8 presence/detail)", sum(live.values()), 47)
    # beat 614: 78 -> 77; the pack dropped one redundant binds glob.
    check("REACH-only values are 77", sum(reach.values()), 77)
    check("read-by-nothing values are 0", sum(none.values()), 0)
    # Beat 587: detail_equals moved 8 -> 7 unread when scopecheck_test.py was written. Beat 588: the
    # remaining 7 -- detail_non_empty_any (6) and detail_non_empty (1) -- moved to LIVE when
    # presencecheck_test.py was written, and NOBODY is now empty for the first time in this file's life.
    # The guard fired on both edits and I changed the number ON PURPOSE, not to go green. Worth saying
    # plainly: "unread" reaching zero does NOT mean the pack is verified. It means nothing in this
    # project is unread BY CONSTRUCTION any more. Whether a reader is RIGHT is a different question, and
    # REACH's 78 values are still read by nothing that drives a rule.
    #
    # The denominator is 125 MATCH values. 150 is every value at match-block depth, which includes the
    # 20 `type` gates and 6 `prior_event` pairs -- real block values, but nothing can "read" a gate, so
    # counting them as unread is the phantom-value mistake again. Derive the total, never type it.
    _nonvalue = sum(len(v) for r, k, v in rows if k in fieldcheck.NON_VALUE_KEYS)
    check("gates + antecedents are 26 block values", _nonvalue, 26)
    check("the three buckets sum to the MATCH-value total",
          sum(live.values()) + sum(reach.values()) + sum(none.values()), 150 - _nonvalue)
    check("the LIVE ledger entries all name presencecheck or a live suite (traceable, not a yes)",
          [k for k in live if k != "argv_any" and "presencecheck_test" not in str(fieldcheck.CHECKED_BY[k])
           and "scopecheck_test" not in str(fieldcheck.CHECKED_BY[k])], [])
    check("  ...and that total is 124", 150 - _nonvalue, 124)
    check("no gate or antecedent is counted as unread",
          [k for k in none if k in fieldcheck.NON_VALUE_KEYS], [])
    check("no value-bearing key lands in two buckets",
          len(set(live) & set(reach)) + len(set(live) & set(none)) + len(set(reach) & set(none)), 0)

    # 4. the two biggest families are exactly the ones the old dict called NOBODY
    check("path_glob_any is REACH, not unread",
          str(fieldcheck.CHECKED_BY["path_glob_any"]).startswith("REACH"), True)
    check("endpoint_glob_any is REACH, not unread",
          str(fieldcheck.CHECKED_BY["endpoint_glob_any"]).startswith("REACH"), True)
    check("  ...together they are 62 values the old report called blind",
          value_keys["path_glob_any"] + value_keys["endpoint_glob_any"], 62)

    # 5. an n/a entry must name what the key actually is
    check("type is marked as a gate", "gate" in fieldcheck.CHECKED_BY["type"], True)
    check("prior_event is marked as an antecedent",
          "antecedent" in fieldcheck.CHECKED_BY["prior_event"], True)

    # 6. CONTROL: the guard can actually fail. Add a value-bearing key with no ledger entry.
    _saved = dict(fieldcheck.CHECKED_BY)
    try:
        fieldcheck.CHECKED_BY.pop("detail_equals")
        miss = [k for k in value_keys if k not in fieldcheck.CHECKED_BY]
    finally:
        fieldcheck.CHECKED_BY.clear()
        fieldcheck.CHECKED_BY.update(_saved)
    check("CONTROL: removing a ledger entry makes the guard fire", miss, ["detail_equals"])

    print()
    if FAILS:
        print(f"ledger_test: {len(FAILS)} FAIL")
        sys.exit(1)
    print("ledger_test: all ok")


if __name__ == "__main__":
    main()

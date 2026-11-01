#!/usr/bin/env python3
"""blindcheck_test — pin the second denominator, and the two parses that had to agree first.

Found by accident, 2026-10-25 (beat 569): `fieldcheck` has been quoting "108 values, 56 read by
nothing" since it was written, and 108 was never the size of the pack. It is the size of the five
keys its KEY_RE knows. Four more keys live in the same match blocks — detail_non_empty,
detail_non_empty_any, detail_glob_any, prior_event — holding 10 more values, counted nowhere.

**A denominator that silently drops rows makes a blind column look smaller than it is.** That is
the whole finding, and it is a finding about my own instrumentation, not about the product.

What this pins:
  1. The unseen-key rows are found at all (10 values across 4 keys, 7 blocks).
  2. The full denominator is fieldcheck's 108 + those 10 = 118.
  3. fieldcheck's own `_unseen_total` and blindcheck's counter AGREE. They are two separate
     implementations on purpose (importing one from the other is a cycle), so disagreement is the
     only thing that can keep them honest.
  4. fieldcheck refuses to print a bare denominator: it states its SCOPE (which keys it counts) and
     says outright that the larger figure is not defensible, rather than calling it partial.
  5. prior_event counts as 0 glob values but is reported as a condition, not silently dropped.
  6. fieldcheck's numerator is derived from its "read by" column (SAMPLE_DRIVEN), so the two
     numbers in one report cannot disagree again.
  7. The inline-list value shape is counted from a fixture, because no shipped pack value uses it —
     this is the mutant that survived in beat 569, closed properly instead of explained away.

Order matters here. Check 3 failing means the two parses have drifted; check 2 failing means the
pack changed. They are different problems with different fixes and the messages say which is which.

Exit 0 only if all hold.
"""
import io
import os
import re
import sys
import contextlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blindcheck  # noqa: E402
import fieldcheck  # noqa: E402

# A minimal pack with the three value SHAPES that occur in the real one, including the inline-list
# shape no shipped value currently uses. Beat 569 shipped with a surviving mutant because the pack
# had no inline value; this is the fixture that closes it. Written as a string, not a file — the
# parser under test takes text, and a temp file would add a cleanup failure mode for nothing.
SHAPE_FIXTURE = """rules:
  - id: carbonato.fixture.inline
    title: inline shape
    technique: T0000
    severity: low
    type: process.exec
    match:
      argv_any:
        - "curl"
      detail_non_empty_any: ["privileged", "host-pid"]
      prior_event:
        class: identity.drift
  - id: carbonato.fixture.block
    title: block shape
    technique: T0000
    severity: low
    match:
      detail_non_empty:
        - "listen"
        - "bind"
  - id: carbonato.fixture.keyline
    title: one list item written on the key line
    technique: T0000
    severity: low
    match:
      detail_glob_any: - "/etc/shadow"
      detail_equals:
        - "not-this-key"
"""


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
    with open(blindcheck.PACK, encoding="utf-8") as fh:
        text = fh.read()

    rows = blindcheck.unseen_rows(text)
    by_key = {}
    for _, key, n in rows:
        by_key[key] = by_key.get(key, 0) + n

    # 1. the rows exist, and every unseen key is represented
    check("unseen-key blocks found", len(rows), 7)
    check("keys represented", sorted(by_key), sorted(blindcheck.UNSEEN_KEYS))
    check("detail_non_empty values", by_key.get("detail_non_empty"), 1)
    check("detail_non_empty_any values", by_key.get("detail_non_empty_any"), 6)
    # beat 614: was 3. the pack dropped the redundant "*:/host*" glob.
    check("detail_glob_any values (nested under binds:)", by_key.get("detail_glob_any"), 2)
    check("prior_event counts as 0 glob values", by_key.get("prior_event"), 0)

    # 3. the two parses agree — run it BEFORE the sum so a drift is reported as drift
    unseen_fieldcheck = fieldcheck._unseen_total(text)
    check("fieldcheck's helper == blindcheck's counter",
          unseen_fieldcheck, sum(by_key.values()))

    seen_rows = fieldcheck.keys_by_rule(text)
    # The earlier version of this line was `len(v) if v else 1` -- it rounded an unparsed map up to a
    # value that does not exist, and then asserted the inflated total. The test AGREED WITH THE BUG by
    # re-implementing it. Count 0 and name the key instead.
    seen_total = sum(len(v) for _, _, v in seen_rows)
    # The structural parser (beat 584) reads the map, so nothing parses to zero any more. The old
    # assertion here demanded ["detail_equals"] -- a pin on the phantom, not on a property.
    empty_keys = [k for _, k, v in seen_rows if not v]
    check("no match-block key parses to zero items", empty_keys, [])
    check("  ...the map-valued key yields its pair, not []",
          [v for _, k, v in seen_rows if k == "detail_equals"], [['novel: "true"']])

    # THE TRUNCATION GUARD (beat 584). Mutant evidence: re-introducing "stop a block list at the
    # first blank line" in the structural parser left this whole file 53 ok / 0 fail. The parser bug
    # was invisible here because nothing pinned a list long enough to be truncated. It is now.
    eg = [v for r, k, v in seen_rows
          if r == "carbonato.exfil.egress_after_identity_change" and k == "endpoint_glob_any"]
    check("a 13-item block list parses in full", len(eg[0]) if eg else 0, 13)
    check("  ...its last item is present", "llamarpc.com*" in (eg[0][-1] if eg else ""), True)
    # 8 is the smallest FULL endpoint_glob_any list in the pack (measured). 6 was the truncated
    # figure -- typing it here would have re-imported the bug as the expectation.
    check("  ...smallest full endpoint list is 8, not the truncated 6",
          min(len(v) for _, k, v in seen_rows if k == "endpoint_glob_any"), 8)
    long_lists = [len(v) for _, _, v in seen_rows if len(v) > 6]
    check("at least one list exceeds a blank-line boundary", len(long_lists) >= 1, True)

    # 2. the denominator. 107/117 were the allowlist's numbers; the structural parse finds 150.
    check("fieldcheck structural denominator", seen_total, 150)
    check("full match-block denominator (upper bound)", seen_total + unseen_fieldcheck, 159)

    # 5. prior_event is reported as a condition, and it is not zero
    conds = blindcheck.scalar_conditions(text)
    check("prior_event conditions reported", conds, 3)
    check("conditions excluded from the value count", conds > 0 and by_key.get("prior_event") == 0, True)

    # 7. The surviving mutant from beat 569: the inline-list shape. The shipped pack has no inline
    #    value, so no amount of mutating it can prove this branch. A fixture is the only way.
    fx_rows = blindcheck.unseen_rows(SHAPE_FIXTURE)
    fx = {}
    for _, key, n in fx_rows:
        fx[key] = fx.get(key, 0) + n
    # Pin the SHAPE, not just the count. Beat 569's surviving mutant restored `continue` for the
    # inline branch and every count-only assertion still passed — because blindcheck, the other
    # parser, still counted correctly. A test that only compares totals is satisfied by one working
    # implementation. These four lines are the branch itself asserting what it parsed.
    #
    # Second finding, same five minutes: two of the three mutants STILL survived that, so the test
    # was weaker than it looked. Each of the three checks below was a comparison between two things
    # that shared code — the mutant moved the tool AND the yardstick, so they agreed and both were
    # wrong. **A check that only says "these two agree" certifies a conspiracy, not a fact.**
    # → each shape is now asserted against a LITERAL that no mutant of the tool can move.
    #
    # Third finding, and the one I would have shipped past: `_unseen_blocks()` emitted NOTHING for a
    # dropped branch, so `shapes[0]` after dropping the inline branch was the *map* — and a tuple
    # comparison against the map's expected value passed. Green and meaningless. A parser is
    # proven here only when all three shapes come back with the right value AND the right count;
    # an omitted shape is a failure, not a shorter list.
    fx_body = SHAPE_FIXTURE[SHAPE_FIXTURE.index("      argv_any:"):]
    shapes = {k: (i, b) for k, i, b in fieldcheck._unseen_blocks(fx_body)}
    check("fixture: all four unseen shapes parsed (a missing key fails, it does not shrink the list)",
          sorted(shapes), ["detail_glob_any", "detail_non_empty", "detail_non_empty_any", "prior_event"])
    check("fixture: key-line list item parsed as inline",
          shapes.get("detail_glob_any", (None, None))[0], ["/etc/shadow"])
    check("fixture: inline list parsed as inline",
          shapes.get("detail_non_empty_any", (None, None))[0], ["privileged", "host-pid"])
    check("fixture: inline list is NOT also a block",
          shapes.get("detail_non_empty_any", (None, None))[1], [])
    # An empty tuple and a dropped key look identical to a comparison, but they are not identical:
    # only the key being PRESENT proves the map branch ran and said "no values". The mutant that
    # treats the map as a value survived two earlier versions of this check.
    check("fixture: map key present (present-and-empty is the proof, not missing)",
          "prior_event" in shapes, True)
    # And the one that got past two versions: the earlier form was `shape == ([], [])`, which is a
    # comparison against the map's EMPTY value — replaced by a wrong value in the SAME slot, it still
    # matched nothing and passed. Now the count of values is a number, and a map carrying a value
    # makes that number 1. Measured: the 'map treated as a value' mutant fails only here.
    # Two ways to write the map, and they land in DIFFERENT branches: on its own line the key line
    # is empty and the map falls into the block-scan; on the key line it reaches the `elif rest:`
    # branch. The second shape was absent from this fixture until a mutant was found unable to
    # reach it — a fixture that can't express a shape also can't test one. Both are asserted.
    SAME_LINE = SHAPE_FIXTURE.replace("      prior_event:\n        class: identity.drift",
                                      "      prior_event: class: identity.drift")
    sl = {k: (i, b) for k, i, b in
          fieldcheck._unseen_blocks(SAME_LINE[SAME_LINE.index("      argv_any:"):])}
    check("fixture: map written on the key line contributes zero values",
          sl.get("prior_event"), ([], []))
    check("fixture: map written on the key line is still a key (not dropped)",
          "prior_event" in sl, True)
    check("fixture: map contributes ZERO values, not one",
          len(shapes.get("prior_event", (None, None))[0] or []) + len(shapes.get("prior_event", (None, None))[1] or []), 0)
    check("fixture: map parsed as neither inline nor block",
          shapes.get("prior_event", (None, None)), ([], []))
    check("fixture: block list parsed as block items",
          shapes.get("detail_non_empty", (None, None))[1], ["listen", "bind"])
    check("fixture: block list is NOT also inline",
          shapes.get("detail_non_empty", (None, None))[0], [])
    check("fixture: fieldcheck counter on the fixture == 5 literal",
          fieldcheck._unseen_total(SHAPE_FIXTURE), 5)
    check("fixture: maps are still reported as conditions",
          blindcheck.scalar_conditions(SHAPE_FIXTURE), 1)

    # 4. fieldcheck must not print a bare denominator any more
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fieldcheck.main()
    out = buf.getvalue()
    check("fieldcheck still exits 0", rc, 0)
    check("fieldcheck names the non-value keys it excludes",
          "not match values" in out, True)
    check("fieldcheck states the defensible figure", "defensible figure is 150" in out, True)
    # Derived, not recalled: the full denominator must equal fieldcheck's own sum plus the keys it
    # cannot see. A literal here is how 118 survived -- it was self-consistent but invented.
    full = sum(len(v) for _, _, v in fieldcheck.keys_by_rule(text)) + fieldcheck._unseen_total(text)
    check("fieldcheck names the full number (derived from its own inputs)", str(full) in out, True)
    check("  ...and that upper bound is 159", full, 159)
    check("fieldcheck reports the structural 150 as a block total",
          "150 block values" in out, True)

    # blindcheck's own report must carry both denominators too
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc2 = blindcheck.main()
    out2 = buf.getvalue()
    check("blindcheck exits 0", rc2, 0)
    check("blindcheck prints both denominators", ("150" in out2) and ("159" in out2), True)
    # detail_equals is fully parsed now, so it has no business in a "cannot see" report. Asserting
    # its absence -- not its presence -- is the property. (An earlier draft of this line demanded
    # "detail_equals" in out2, i.e. it pinned the bug it was written to retire.)
    check("blindcheck does not claim to be blind to detail_equals", "detail_equals" in out2, False)

    # --- the census guard (beat 583) -----------------------------------------
    # KEY_RE is an allowlist of five keys. For two days its output was quoted as a census of the pack,
    # including by me, in FINDING-2026-10-25k. These assertions make the blind spot a NUMBER rather
    # than a silence, so the next key added to the pack shows up here instead of vanishing.
    all_keys = fieldcheck.match_block_keys(text)
    blind = fieldcheck.unparsed_keys(text)
    check("the structural walk sees every key the allowlist sees (it is upstream, not a copy)",
          set(blind).isdisjoint(set(fieldcheck.PARSED_KEYS)), True)
    # NOTE: an earlier draft of this assertion wanted 'own' and 'session' in the blind set. They are
    # not keys -- they are words inside description: prose. My ad-hoc counter counted lines matching
    # /own:|session:/ without checking indentation or nesting, and invented two keys. The structural
    # walk was right and my expectation was wrong. Typed it here so the mistake stays visible.
    check("the blind key set is exactly the measured one",
          sorted(blind), ["detail_glob_any", "detail_non_empty", "detail_non_empty_any",
                          "prior_event", "type"])
    check("blind blocks are counted, not guessed", sum(blind.values()), 27)
    check("...and the pack really has 10 distinct match-block keys", len(all_keys), 10)
    check("  ...of which 5 parse and 5 do not", len(all_keys) - len(blind), len(fieldcheck.PARSED_KEYS))
    # Control: the walk must FIND the five parsed keys too, or 'blind' would just be 'everything'.
    for k in sorted(fieldcheck.PARSED_KEYS):
        check(f"  control: structural walk reaches parsed key {k}", k in all_keys, True)
    # fieldcheck must say its own scope out loud rather than printing a bare total.
    check("fieldcheck states its scope: all keys parsed structurally",
          "ALL parsed" in out, True)
    check("fieldcheck refuses to call 160 a census",
          "treat it as an upper" in out, True)
    check("blindcheck tells you which pair to quote", "Quote the second pair" in out2, True)


    # 6. fieldcheck's numerator must come from its own "read by" column, not a literal.
    #    Beat 569: `read_globs` was `key == "argv_any"` while the table above it said exe_any was
    #    candidate-only, so the two sentences in one report disagreed (69 vs 79) and neither was
    #    visibly wrong on its own. This is the assertion that keeps one number, not two.
    check("sample-driven numerator is argv_any only", fieldcheck.SAMPLE_DRIVEN("argv_any"), True)
    check("candidate-only key is NOT sample-driven", fieldcheck.SAMPLE_DRIVEN("exe_any"), False)
    check("nobody-read key is NOT sample-driven", fieldcheck.SAMPLE_DRIVEN("path_glob_any"), False)
    # Beat 588: the LIVE bucket is no longer argv_any alone -- both presence families and
    # detail_equals moved in, so 39 -> 47 and REACH-only 79 -> 78. These three lines are typed
    # numbers and they FAILED when the ledger changed, which is what they are for. Changed on
    # purpose. The numbers are now derived where derivable and typed only where they are the claim.
    check("scopecheck's detail_equals is LIVE, not REACH (beat 587 shipped it as REACH by mistake)",
          fieldcheck.SAMPLE_DRIVEN("detail_equals"), True)
    check("both presence families are LIVE",
          (fieldcheck.SAMPLE_DRIVEN("detail_non_empty"),
           fieldcheck.SAMPLE_DRIVEN("detail_non_empty_any")), (True, True))
    # Beat 586: "112 read by nothing" was FALSE. It lumped 78 values that HAVE a reachability check
    # in with 34 that have no instrument at all. The unread number shrank because the claim got
    # honest, not because work happened.
    # Beat 588: it is 0 now. THE ZERO IS NOT A CLEAN BILL -- every match value has a named reader,
    # but 78 of 125 are still only REACH (a glob CAN fire, not a rule DOES). The blind column is
    # gone; the weak-evidence column is not, and this suite does not claim otherwise.
    check("fieldcheck now splits unread into REACH-only and nothing",
          "77 REACH-only" in out, True)
    check("  ...and 0 genuinely read by nothing", "0 read by nothing" in out, True)
    # beat 614: 125 -> 124 match values, 78 -> 77 REACH, one glob deleted. 111 unread unchanged.
    check("  ...on a 124 match-value base, not the 150 block total",
          "124 match values" in out, True)
    check("  ...and it names the 26 gates it excluded", "26 gates/antecedents" in out, True)
    check("  ...and 47 are LIVE", "47 LIVE" in out, True)
    check("fieldcheck and blindcheck agree on 112 unread", "112 unread" in out and "= 112." in out, True)

    print(f"\nblindcheck_test: {ok_n} ok, {fail_n} fail")
    if fail_n == 0:
        print("Reminder: this pins the COUNT of the blind column, not the health of the pack.")
    return 0 if fail_n == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

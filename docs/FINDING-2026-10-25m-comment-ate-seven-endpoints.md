# FINDING 2026-10-25m — a comment ate seven endpoints of a critical exfil rule

**Status:** FIXED in `scripts/fieldcheck.py`; **pack unchanged** — the pack was always correct, the reader was not.
**Instrument:** `scripts/blindcheck_test.py` (57 ok), `scripts/eventcheck_test.py` (10 ok),
`scripts/keys_by_rule_structural`. Go engine read, not run (no toolchain).

## What the structural parser found

Replacing the five-key allowlist (`FINDING-25l`) was supposed to be housekeeping: make the counter
count what is actually in the pack. It turned up a live detection defect instead.

`carbonato.exfil.egress_after_identity_change` has this in its match block:

```yaml
      endpoint_glob_any:
        - "*slack.com*"
        ...
        - "*0x0.st*"
        # v6: a chain RPC call after an identity change is the same exfil/C2
        # shape as a chat call after one (the Graphalgo construction).
        - "*infura.io*"
        - "*alchemy.com*"
        - "*quiknode.pro*"
        ... (7 total)
```

`keys_by_rule_legacy` ended a block list at **any non-item line**, comments included. So from the day
v6 was authored, the parser read **6** endpoints for this rule and dropped the other **7** — every
blockchain-RPC exfil path the v6 entry was added to catch. The rule fired on Slack and missed Infura.

**Evidence, both directions:**

| parser | `egress_after_identity_change` endpoint globs |
|---|---|
| legacy (allowlist) | **6** |
| structural | **13** |
| mutant reintroducing comment-termination | **6** (11 assertions red) |

This is not a phantom like `25k` — 13 is what the YAML says, and `*infura.io*` is verifiably in it.

## The lesson I nearly missed

My first explanation for 6-vs-13 was "the parser stopped at a blank line." I built a blank-line mutant,
it **survived**, and I almost recorded that as a test hole. It wasn't: **there is no blank line there.**
The mutation I invented didn't reproduce the bug, so its survival said nothing.

Had I stopped at "add a truncation guard," I'd have shipped a guard against a mechanism that does not
exist and left the real one (comment-termination) free to return. The surviving mutant was the clue, not
the conclusion — same shape as beat 581's dead-candidate work, opposite outcome.

## Fixed

- `keys_by_rule` now delegates to `keys_by_rule_structural`; `keys_by_rule_legacy` is kept solely to
  prove the delta and is asserted to still truncate to 6.
- `eventcheck` now sees all 13 globs on that rule (and 8/11 on the other two): 7 rules, 36 samples, all
  LIVE. Its long-standing `KNOWN BUG: fieldcheck drops '*llamarpc.com*'` pin is **inverted, not
  deleted** — a regression lands there.
- `fieldcheck` reports its own scope honestly: 10 distinct match-block keys, all parsed; 151 values;
  23 blocks that are gates/antecedents rather than match values, named.
- Guards added after the survivor: a 13-item list must parse in full; the smallest full
  `endpoint_glob_any` list is **8** (typing the truncated 6 as the expectation would have re-imported
  the bug as truth); at least one list must exceed the comment boundary.
- Mutants killed: comment-termination (11 FAILs), map-pairs dropped (11), scalar keys dropped (8).

## Canonical numbers, superseding `25k` and `25l`

**151 match values** parsed structurally (was quoted as 117, then 118). **39 read** by a sample-driven
check (26%). **112 read by nothing.** The 161 figure adds block counts to value counts and is an upper
bound, not a census; the tools now say so.

Every coverage claim rests on the parser this finding fixes, so **151 is the first defensible
denominator this project has had.** The defect findings in `25e`–`25j` remain valid — each was measured
on glob values directly and is re-verified by the structural parse.

## Open, for a person

The pack should probably carry a test that the number of globs per rule matches an independent count —
belt and braces, since a silently dropped list item is invisible to every check that reads the same
parser. Not done tonight: it needs a decision about what the expected counts are, which is the pack
owner's call.

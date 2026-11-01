# FINDING 2026-10-24 (f) — A total that disagreed with its own breakdown, on one line, exiting 0

**Status:** FIXED — and the fix caught a second defect within a minute of being written. The instrument was the
defect, for the twelfth time today.

**Beat:** 557 · **Instrument:** `scripts/guardtriage.py` · **Trigger:** Callie 6371 —
*"If a script has no falsifier, the honest label is not 'untested,' it is 'unread.'"*

---

## Callie's pushback, and where it's right

She pushed back on my beat-556 proposal for `make probes-check`:

> On demand and chained to nothing is a third thing that nothing invokes — the same orphan one layer up.

She is right, and it's the sharpest correction of the night. A target nobody chains is still a target nobody
chains; putting 19 green self-checks behind it would have made the *appearance* of a reading. I withdraw the
proposal as stated. If the probes get a target it must be chained somewhere, or the finding is not closed.

She also relabelled my bottom bucket: the 13 aren't "untested," they're **unread**. That sent me to look at
what they can actually claim — and to `guardtriage.py`, which is one of the 13.

## The bug

`guardtriage.py` printed this, and exited 0:

```
guard triage: 26 flagged = 16 T1 defects + 8 with nothing to count + 26 harnesses + 4 gated off this host +
              1 libraries — green
```

16 + 8 + 26 + 4 + 1 = **55**. The sentence claims **26**. A total and its own breakdown, disagreeing in
public, in one line, for a day and a half.

The tell is that T3 alone equalled the total: 26 = 26. Two populations sharing a label. `flagged` is a
subset of the walk; every tier is computed over the **whole walk** (that widening was beat 550's fix, a few
lines above, with a comment explaining it). So the sentence put an equation between a subset count and
per-population counts. No amount of careful printing would fix it — the two sides were never the same
question.

This is verbatim the defect I flagged in beat 550 as "a total and its own breakdown must visibly agree," and
then wrote a whole finding about. I wrote the rule and left the violation sitting in my own instrument.

## What I changed, and what I refused to change

I did **not** touch the tiers. They're correct; the widening in beat 550 was right. I deleted the false
equation and replaced it with per-tier flagged counts that must sum:

```
guard triage: 26 flagged, all accounted for across the tier tables — green
     T1  16 guard(s) (5 of them flagged)
     T2   8 guard(s) (8 of them flagged)
     T3  26 guard(s) (8 of them flagged)
     T4   4 guard(s) (4 of them flagged)
     T5   1 guard(s) (1 of them flagged)
     OK  29 guard(s), 0 flagged
     tier totals: 16+8+26+4+1+29 = 84 tiered guards (walk is 86); flagged 26 of them
     accounted for: 84 tiered + 2 untiered-by-design = 86 of 86 walked
```

Every number in that block now names its own denominator, and two checks can go red.

## The fix caught a second bug immediately

My first coverage check compared `84 tiered` to `86 walked` and went red. Two guards were in the walk and in
no tier. That looked like a real missing-guard defect — and it was the check being wrong: `guardreach.py` and
`guardtriage.py` are the two instruments that do the judging, and line 132 deliberately skips tiering them.
The new check had forgotten its own documented exclusion.

I had written the check one minute earlier and it went red on me. Both directions are now explicit: the two
by-design-untiered files are named in a set, not subtracted as a magic 2, and the failure message prints both
halves (`tiered 84 + untiered-by-design 2 does not cover the walk of 87`).

## Falsifiers — both directions, both checks

| # | Injection | Expected | Observed |
|---|---|---|---|
| F1 | append a guard to the walk that no tier contains (`zz_ghost.py`) | coverage check red, naming it | `FAILED: 1 guard(s) in the walk are in no tier: ['zz_ghost.py']`, EXIT=1 |
| F2 | add `tokruns.py` to T3 as well as T1 | `seen` map rejects the duplicate | `FAILED 1: tokruns.py is in both T1 and T3 — first match wins silently`, EXIT=1 |
| — | restore | EXIT=0 | EXIT=0 |

## The twelve

Reader/logic bugs in my own instruments today, now twelve. This one is the worst kind because it did not need
a clever reader to catch — it needed someone to add the numbers. Anyone reading that line for two seconds with
a pencil would have found it. I read it a dozen times and saw a green.

The related tally matters more than the count: **twelve instrument bugs, zero confirmed defects in the tree
they were pointed at.** The tools are the buggy part. Callie's word is better than mine was — most of these
aren't tests that failed, they're scripts nobody read.

## What this does not claim

- **`guardtriage.py` is not now trustworthy**, only arithmetically consistent and falsified in two directions.
  It still inherits every limitation of `guardreach.py`, including that it cannot detect a frozen-but-balanced
  ledger.
- **The 13 are still unread** as a class. Fixing the one that was auditing the others is not a sweep.
- **Nothing was wired.** `guardtriage.py` is still an orphan, still has no Make target, and now exits 0 for
  the right reason instead of the wrong one.

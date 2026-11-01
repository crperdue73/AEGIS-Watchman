# FINDING 2026-10-25k — the number I've quoted for two days had a phantom in it

**Status:** FIXED — `fieldcheck.py` and `blindcheck.py` corrected; nothing in the pack touched.
**Instrument:** `scripts/blindcheck_test.py` (39 ok), `scripts/fieldcheck.py`, `scripts/blindcheck.py`.

## What was wrong

The house denominator was quoted for two days as **118 / 39 / 79**. It should have been
**117 / 39 / 78**. One of those 118 values did not exist.

Both `fieldcheck.py:171` and `blindcheck.py:143` did this:

```python
rows = [(rid, key, vals if vals else ["<inline pair>"]) for rid, key, vals in rows]
```

`keys_by_rule` parses keys whose value is an inline list. `carbonato.tool.scope_new` has:

```yaml
      detail_equals:
        novel: "true"
```

That's a **map**, not a list. It parses to `[]`. The placeholder then replaced `[]` with a one-element
list, and every downstream sum counted a value that does not appear anywhere in the pack.

Verified: `sum(len(v) for ...)` over `keys_by_rule` = **107**. Plus the 10 values in keys fieldcheck
can't see = **117**. The tools printed 108 and 118 because of the placeholder.

## Why the test didn't catch it

`blindcheck_test.py:111` re-implemented the same inflation:

```python
seen_total = sum(len(v) if v else 1 for _, _, v in seen_rows)
check("fieldcheck denominator", seen_total, 108)
```

The test reconstructed the bug and then asserted the buggy total. Both sides agreed. **That is the exact
failure this suite was written to catch** — "a check that only says these two agree certifies a
conspiracy, not a fact" — and it was inside the suite that preaches it. Eleventh time this project has
found a guard agreeing with itself.

It went unnoticed because a placeholder is *plausible*: a key with no parsed values might reasonably be
one value. It isn't. A map is not a list.

## The fix

- Both files count 0 for an unparsed map and **name the key** instead of rounding it up.
- `fieldcheck` now prints `map-valued keys counted as 0: detail_equals (a map is not a list of values;
  not rounded up)`.
- `blindcheck` prints the same line with the rule id.
- The test no longer hard-codes 107/117: it **derives** the full denominator from `keys_by_rule` +
  `_unseen_total` and asserts the printed string contains it. A literal 118 was self-consistent and
  invented; a derived number cannot be.

## Mutation-tested

Reinstating the placeholder in a `/var/tmp` copy → **4 FAILs**: the derived-full-number assertion, the
5-key number, the 68-unread line, and the 78-agreement line. The fix is load-bearing, not cosmetic.

## Corrections to prior work

Six findings docs (`25e`, `25f`, `25g`, `25h`, `25i`, `25j`) quoted `118 / 39 / 79`. All corrected to
`117 / 39 / 78`. `25d` carried a forward-looking claim and now says so. The WORKLOG entries are left as
written — they were measurements of the instrument as it stood, and the record of what I believed when
is worth more than a cosmetically consistent log.

The errors these docs describe are unaffected: every one of them was measured against glob values
directly, not against the total.

## The lesson, which is the same one as beat 579

I found this by asking "does the number I keep quoting still come from somewhere?" It didn't — it came
from a placeholder standing in for a parse failure, and a test that reproduced the placeholder. **A
denominator is a claim and needs a derivation, not a memory.**

New canonical numbers: **117 values / 39 read (33%) / 78 unread (66%)**.

# The report called 62 values blind. They had instruments.

**Status:** FIXED
**Instrument:** `scripts/ledger_test.py` (18 ok) · `scripts/fieldcheck.py` · `scripts/crosscount_test.py`
**Date:** 2026-10-26 (beat 586)

## What was claimed

`fieldcheck.py` printed, every run, every quote, for weeks:

> 151 values in the pack, 39 read by a sample-driven check (25%), **112 read by nothing.**

That 112 was in findings 25e through 25m. It was the number that made the project's coverage look
catastrophic and it is the number I quoted to justify building more instruments.

## What was true

`CHECKED_BY` — the dict the report consults for "who reads this" — had **two entries**:

```python
"argv_any": "packcheck.py + sigprobe.py ..."
"exe_any":  "packcheck.py, as a candidate source only ..."
```

The pack has **eight** value-bearing keys. The other six fell through `.get(key, "NOBODY")`.

`"NOBODY"` was never a measurement. It meant *"not in my dict."* And the two keys it swallowed
hardest were the two biggest families in the pack:

| key | values | old report | actually |
|---|---|---|---|
| `path_glob_any` | 30 | NOBODY | `packcheck.py` — every glob must be able to fire |
| `endpoint_glob_any` | 32 | NOBODY | `endpointfixture.py` part 1 — reader reachability |

`endpointfixture.py` and `bindprobe.py` were sitting in the same directory, written by me, pointed at
those exact families. The report did not know they existed.

**62 values.** The blind number was wrong in the direction that flatters the report: it claimed
blindness the project does not have, which is a self-serving error — it manufactures urgency.

## The second error, found while fixing the first

The three buckets did not sum to the denominator, because **151 is not the match-value total.** It is
every value at match-block depth, which includes 20 `type` gates and 6 `prior_event` pairs. Nothing
can "read" a gate. The honest base is **125 match values**.

That is the phantom value of FINDING-25k wearing a new coat: a denominator containing things that
were never eligible to be counted. Second time in two days.

## The honest numbers

```
125 match values (151 block values minus 26 gates/antecedents)
  39 LIVE       — a sample event drives the rule, and it fires
  78 REACH-only — a check proves the glob CAN fire
   8 read by nothing at all
```

Two strengths, kept apart on purpose. **REACH is weaker than LIVE**: a glob that *can* fire is not a
rule that *does*. Counting REACH in the numerator would have inflated coverage by 78 values on the
strength of the weaker promise — the same lie in the opposite direction. `SAMPLE_DRIVEN()` now
matches `LIVE` explicitly.

The 8 that are genuinely read by nothing: `detail_non_empty_any` (6), `detail_equals` (1),
`detail_non_empty` (1). The first and third are presence tests where there is little to read. The
one worth naming is **`detail_equals` — the single rule `carbonato.tool.scope_new` has no sample at
all**, so the one rule that detects scope broadening is never exercised.

## Why it took a guard to fix it

`ledger_test.py` fails if **any** value-bearing key lacks a `CHECKED_BY` entry. A key added to the
pack breaks the suite. A ledger you have to remember to update is exactly how 112 happened.

And two assertions actively pinned the bug, both mine:
- `fieldcheck_test.py`: `check("path_glob_any read by nobody", CHECKED_BY.get("path_glob_any"), None)`
  — asserting absence as if it were a fact.
- The test's own docstring: *"path_glob_any is read by nothing — the moment that stops being true,
  this fails."* It would have caught the fix and defended the lie.

Third occurrence this week of an assertion pinning the defect it was written near (584: `detail_equals`
name-presence; 582: the test that re-implemented the inflation). The pattern is consistent enough to
name: **I write tests that describe what I believe, and belief is where the bug lives.**

## Caveat

The LIVE/REACH classification is read from `CHECKED_BY`, which is a table a human (me) maintains. The
guard forces every key to have an entry; it cannot force the entry to be *true*. `endpointfixture`
part 1 being "reachability only" is asserted from its docstring and its output, not by running the Go
engine — no toolchain on this host. Do not quote REACH as though it were LIVE.

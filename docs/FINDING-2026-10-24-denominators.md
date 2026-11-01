# FINDING 2026-10-24 (k) — Four denominators in eight beats, and no test in the tree can say "the finding expired"

**Status:** MEASURED · OPEN — a corrected census of the 18 self-checks, and the real bug of the night is
that I reported three different populations for the same set of files without noticing.

**Beat:** 562 · **Instrument:** `scripts/bindprobe_test.py` and `scripts/pathdiff_test.py` read in full; the other 16 test files censused by style and retirement language · **Trigger:** trying to write down "3 of 16" and
failing to be able to say what the 16 was.

---

## The denominator problem, in order

| beat | what I called the population | number | source |
|---|---|---|---|
| 556 | "orphan self-checks" | **19 test files** | counted via `orphanprobe.py` ORPHAN rows |
| 558 | "the 16 testable probes" | **16** | probes with a matching `_test` among the 39 orphans... or something |
| 560/561 | "two of sixteen / three of sixteen" | **16** | carried forward, never re-derived |
| 562 (now) | `scripts/*_test.py` on disk | **18** | `ls scripts/*_test.py \| wc -l` |

Nineteen, sixteen, sixteen, eighteen. Same tree, four beats, three numbers, and I never once printed the
command that produced the one I was using. **"The denominator is the claim"** is a rule I wrote in this repo.
I then used a denominator I couldn't reproduce three beats running.

The correct number for "test files in `scripts/`": **18**. It is not the same question as "tests attached to
orphan probes," which is a different and smaller set — and the finding is that I was conflating a
filesystem count with an orphan-probe count and reporting the difference as drift.

## Reader bug fifteen — `ok(` is not how all tests assert

First pass this beat printed `assertions=0` for six files including `eventcheck_test.py`, apparently proving
six "tests" never assert. Reading `eventcheck_test.py` killed that: it is one of the more careful files in the
tree, pins a 38-vs-36 denominator and a known `fieldcheck.keys_by_rule` truncation, and asserts plenty — just
not through a helper named `ok`.

Correct split: **10 files assert via `ok(...)`, 8 via `assert`/`sys.exit` (or both).** Six files show `ok()=0`
and are entirely plain-style. Any future reader that counts `ok(` calls is measuring one house style, not
testing.

## Reader bug sixteen — the phrase wrapped, and I missed it again

A per-file grep for `retire|premise|HAS_GUARD|expired` reported `bindprobe mentions=0`, contradicting beat
559 where I had read its phrase. Cause:

```python
   "expected the asymmetry; if this now passes, the pack or engine changed")
```

The phrase sits on a **continuation line inside a string literal**, so a grep anchored to line starts misses
it. This is the "normalise a carrier before matching content" rule — already written down in this campaign,
already violated once, violated again tonight. The lesson wasn't retained; only counted.

## The census, read by eye instead of by regex

**18 test files. 3 mention retirement at all** (`pathdiff` 6 mentions, `clockprobe` 3, `starveprobe` 2,
`batchprobe` 1 — with `pathdiff`'s six being one section counted repeatedly). **0 of 18 can signal retirement
through the exit status.**

```
NONE of 18 test files can emit exit 3 — the convention from beat 560 exists nowhere in the tree
```

That is the beat's answer, and it is stronger than "the guards are weak." The three-exit-code convention I
proposed at 12:38 AM as the fix **does not exist anywhere**, including in the `clockprobe` file I held up as
the standard and the `pathdiff` file whose docstring inspired it. I proposed a convention to a repo that had
already half-inherited it in prose and nowhere in code.

## What is actually true tonight

- **18** self-check files exist in `scripts/`. Ten use `ok()`, eight don't.
- **3** of 18 contain any retirement language; **0** of 18 act on it via exit code.
- **1** (`bindprobe`) was verified in beat 558 to have a real, targeted kill radius.
- **0** were verified to distinguish "instrument broken" from "finding expired" — because none can.
- The 39-orphan figure and the 18-test figure are **different questions** and I have been quoting them as one.

## What this does not claim

- **Not that 15 of 18 are blind.** One verified, seventeen unmeasured, by budget not by suspicion.
- **Not that a sweep is unnecessary.** It is more necessary: the denominator was wrong, so no ratio computed
  from it can be trusted until it's re-derived.
- **Not that anyone else should have caught this.** The numbers were mine, in my own posts, all in one night,
  and the only reader in a position to notice was me.

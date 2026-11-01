# FINDING 2026-10-30b — every append past the cap rewrites the whole spool, on the poll path

**Status:** Instrument added (`scripts/appendcost.py`). Source reading; no Go, no benchmark.
**Instrument:** `scripts/appendcost.py` · **Beat:** 675 · **Family:** 661, 671, 674

## Credit first

`trim`'s cheap path is correct and well-placed: `os.Stat`, then `if st.Size() <= s.maxBytes { return nil }`. Whoever
wrote that knew the common case is "under the cap, do nothing," and made it cost one stat. `rewrite` is also the right
shape — temp file, flush, fsync, close, rename — the same crash-safe pattern drain uses. Neither of those is the problem.

## The question nobody asked

Beats 659/670/671 all asked whether the spool *holds* things and whether it *makes progress*. All three treated `Append`
as a local, cheap operation whose only interesting failure is the disk. Nobody asked **what Append costs when the spool is
already at its limit** — which is the state trim exists to handle.

## The mechanism (comments stripped)

`Append` (spool.go:60-85) ends with `return s.trim()` — **unconditionally**, on every successful write. `trim`
(spool.go:226-256) then:

1. `os.Stat` — cheap, and returns early if under the cap (correct)
2. `s.read()` — **decode every record in the file**
3. `for len(recs) > 1 {` with an **inner `for _, r := range recs { json.Marshal(r) }`** — every surviving record is
   re-marshalled, and this inner loop re-runs for **each** record dropped → O(n²)
4. `s.rewrite(recs)` — full write to a temp file, fsync, rename

So past the cap, one append costs: an O_APPEND write + fsync, a full read+decode, a quadratic re-marshal loop, and a full
rewrite with fsync and rename.

## Why it matters, and why it is more than slow

`Append` is called from `Poll` (behavior.go:417-424), **synchronously, before `drain()`**. So trim's cost is paid on the
same loop that ships the backlog. With `DefaultSpoolMaxBytes = 16 << 20` (beat 671) and up to 2520 events per record
(beat 661), a spool sitting at the cap can hold thousands of records, and every 60-second poll re-reads, re-decodes,
re-marshals and rewrites megabytes it is about to partially discard anyway.

**The cost grows with the backlog — so it is greatest exactly when the collector is already behind.** That is the same
coupling beat 674 named, one layer over: 674 was a *read* error stopping Poll before drain; this is *append's own
housekeeping* on the poll path, getting more expensive as the recovery gets more urgent.

## Claim / not claimed

**Claimed (from source):** Append always calls trim after a successful fsync'd write; trim's cheap path is real and
correct; over the cap trim does a full read+decode, a re-marshal nested inside the drop loop, and a full rewrite with
rename; Append is synchronous and runs before drain in Poll.

**Not claimed: any latency number.** No toolchain and no benchmark — the O(n²) is read off the nesting in the source, not
off a timer, and I will not quote a millisecond figure I did not measure. **Not claimed: that this loses events** — trim
drops oldest *by design*, and 659/671 already cover that. **Not claimed: the trim/Drain race** — Drain and trim both touch
the same path and I did not measure the interleaving; that stays an unmeasured suspicion, exactly as it was in 671.

## Fix (Dad's call)

1. **Trim on a ratio, not on every append** — drop to ~75% of the cap so the following appends take the cheap path. This
   is the one that actually removes the repeated cost.
2. **Size once, walk once** — compute each record's size a single time, then walk the prefix. Removes the O(n²) without
   changing behaviour. (Mutation 1 below proves the check retires when this lands.)
3. **Move trim off the poll path** — after drain, or on its own cadence.

## Mutation proof

| mutation | result |
|---|---|
| **Fix 2 applied** (size once, walk once) | **FAIL** — "an inner loop RE-MARSHALS every surviving record" inverts, as it must · rc=1 |
| `return s.trim()` → `return nil` (trim removed) | **FAIL** — "Append ends by calling s.trim()" · rc=1 |
| control | 21 rows green · rc=0 |

## Two of my own checks were wrong on correct code first

1. I asserted `"CreateTemp" in rewrite` — the temp file is opened by another constructor, so the check went red on correct
   code. Fixed to test the **property** (temp file renamed over the real path), not the name of a function (beat 661).
2. I asserted `rew.index("Sync()") < rew.index("Close()")` — but the **first** `Close()` in `rewrite` is an error-path
   `f.Close()` inside the write loop, so the ordering compared against the wrong call and went red on correct code. Fixed
   to `rindex("Close()")`, the happy-path close. Plain substring indexing on a repeated call is a trap: it finds whichever
   one is textually first, not the one in the branch you mean.

Both were caught by running the instrument, not by reading it — which is the only reason they are recorded here.

## Gate

**88 suites / 1693 checks / 0 failures.** Baseline rewritten (`appendcost.py 21`).

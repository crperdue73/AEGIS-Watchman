# FINDING 2026-10-24 (h) — A stale finding and a broken instrument have the same exit code

**Status:** MEASURED · OPEN — method ruling with a demonstrated failure. Not fixed tonight; the fix is a
pattern that needs a decision about the whole probe suite, not one file.

**Beat:** 559 · **Instrument:** `scripts/bindprobe_test.py` (worked example), `scripts/clockprobe_test.py`
(the pattern done right) · **Trigger:** Callie 6378

---

## Callie's correction, and why it's the useful one

> That is not "the self-check works." That is "the self-check works on the one thing it was built for," which
> is a smaller and truer claim, and the smaller one is the one that holds.

A kill radius is a claim about *today's* code. The radius only means something while the defect it kills for
still exists. So the question after beat 558 is not "does the self-check fire?" but **"what happens when the
thing it watches is fixed?"**

## The demonstration

`bindprobe_test.py` asserts the pack has an asymmetry: a socket mount listed *first* of two misses, listed
*second* it fires. Its docstring calls this "the finding." I simulated the engine being fixed — had `fires()`
consult each comma-separated value instead of the whole string, which is exactly the fix the finding asks for:

```
FAIL socket as FIRST of two MISSES  [expected the asymmetry; if this now passes, the pack or engine changed]
FAIL host root as FIRST of two MISSES
FAIL the failure is the trailing comma
FAIL position decides: same mount, different list order
FAIL main() exits non-zero on disagreement  [0]
EXIT=1
```

Restored: EXIT=0.

**Both cases exit 1.** A genuine regression in `bindprobe.py` (beat 558, removing the quote-strip) exits 1.
The pack or engine being *fixed* exits 1. Somebody reading the red light cannot tell which world they're in
from the exit code, and the docstring phrase "if this now passes, the pack or engine changed" is a sentence,
not a check — it only prints when the assertion has already failed.

This is the stale-finding problem in its mechanical form. Every evidence probe in this repo records a
condition of the tree; most of them assert that condition **holds**. When the condition stops holding, they
go red — reporting a defect that no longer exists, in the same voice they use to report one that does.

## The pattern that does it right

`clockprobe_test.py` is the only one of the seven I sampled that tests **the detector**, not just the
condition:

```python
ok("NEGATIVE: a timestamped Action would be caught by the premise check",
   _re.search(r"\b(TS|Time|At|Timestamp)\b\s+time\.Time", ACTION) is None,
   "premise check is live: it finds no such field today")
```

It asserts that *if* Action gained a timestamp, the probe's premise-check would notice. So the premise's
liveness is itself a tested fact. If the world changes, that check tells you the world changed. That is the
difference between "the finding is still true" and "the finding is still being checked."

Premise-guard counts across the seven sampled `_test.py` files: **clockprobe 3, bindprobe 1, pathdiff 1,
driftprobe 0, starveprobe 0, wirecheck 0, predaudit 0.** And the counts are weak evidence — I learned that in
beat 558 when token-matching in test source produced four false positives out of four. `bindprobe`'s single
hit is a docstring phrase. Only `clockprobe`'s are assertions.

## What the fix looks like, and why I'm not doing it tonight

Not a code change — a shape every evidence probe needs:

1. Each probe's *finding* stays a normal assertion (the condition holds today).
2. Each probe gets a **premise guard**: a constructed input that the probe's own premise-check must catch.
   `clockprobe` already shows the form.
3. The self-check distinguishes red-because-instrument-broke from red-because-premise-changed, since tonight
   proved those share exit 1.

That's a change to sixteen-plus instruments and to what a red light means in this repo. It's a decision about
the suite's contract, which is the same class of thing as the parity question in beat 554 and gets the same
treatment: measured, written down, left for whoever owns the contract. I'm not going to spend a night turning
one probe into the pattern and announcing the class solved — I did that in beat 550 and it didn't hold.

## What this does not claim

- **Not that all 16 are stale.** One was demonstrated, one is already right, five have no premise guard and
  were not tested tonight. "Five lack it in source" is a grep, and grep is not a reading.
- **Not that `bindprobe` is wrong today.** Its finding still holds; I verified it in beat 558 with a targeted
  mutation. The defect is what happens the day the finding expires.
- **Not a fix.** Nothing in `scripts/` changed this beat except a restore-to-backup I verified.

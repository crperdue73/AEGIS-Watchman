# FINDING — 2026-11-02b: `ModTime` is collected, shipped, and never compared

**Status:** source reads **+ executed measurement**. **NOT observed in a deployment. NOT APPLIED.**
**Instrument:** `scripts/modtimecensus.py`.
**Origin:** Callie's 7400 — *"a check that can see a count and hasn't asked whose count it is."* This is the same
question asked of a field: the collector carries a timestamp through three layers and no comparison ever reads it.

## The claim

`FileState.ModTime` is captured (`collector.go:209`), put in the inventory (`:216`), sent over the wire
(`http.go:64`), stored in the baseline, and rendered on the agents page (`agentspage.go:160`). `Drift` — the only
function that decides whether anything changed — **compares `SHA256` and nothing else** (`drift.go:52`).

So `ModTime` is a **display field wearing a detection field's clothes.** For every path where the agent page shows a
modification time, that time informed zero decisions. It is not wrong; it is *inert*. The question Callie's line forces
is: when an operator reads that column, what are they reading?

## What that column actually is

`info.ModTime().UTC()` — the **filesystem's** recorded mtime, as reported by the kernel, for a walk the collector ran.
Three properties follow, and none are stated anywhere near the field:

1. **It is the filesystem's opinion, not ours.** Any process holding write access can set it. `os.Chtimes` moves mtime
   backwards, forwards, or to a value of the attacker's choosing. `touch -d '2019-01-01' SOUL.md` after editing
   `SOUL.md` is a **one-line, unprivileged, no-detection** change to this display field.
2. **It costs a stat per file and buys nothing.** `d.Info()` on every matched file, guarded by `if ierr == nil` — a
   failure silently yields the zero `time.Time`, which the page renders as an em dash (that part is *right*, and
   `agentspage.go:83` says why). So the field is honest about failure and dishonest about tampering.
3. **mtime granularity is not the question; the comparison is.** Even with perfect nanosecond mtimes, an inert field
   detects nothing. The issue is not resolution.

## Where this becomes a *detection* claim — and why it isn't one

An operator seeing "`soul.md` — modified 2019-01-01" on a fleet page reasonably infers someone touched the file then, or
that the file is old and untouched. **Neither is supported.** The hash is what protects the operator here — a
backdated `touch` cannot fool `SHA256`, and `Drift` will report the content change correctly. So the system is not
vulnerable *because* of this field.

The finding is narrower and still worth writing: **the field is surfaced with no indication that it is unverified,
uncompared, and attacker-settable.** In an intrusion-detection product, that is the same shape as beat 731's
`LastSeen` — a value rendered as signal that is derived from something other than what its label implies. 731's was
derived from the wrong *event*; this one is derived from the *subject's own claim about itself*.

**And the counter-argument, which I think wins today:** for the intended use — "did this file change since I last
looked, and roughly when" — mtime is a legitimate *hint*, and the hash is the *proof*. The two together are a
reasonable design. What is missing is one sentence saying which is which. That is why option 4 below is the honest
cheapest fix and the others are not obviously worth it.

## Measured, not argued

I ran the real `Drift` against two inventories. The transcripts, verbatim:

```
RESULT case1_mtime_moved_back_3y events=0
RESULT case2_content_changed_mtime_forged events=1 severity=high
```

Two things established by execution, not by reading:

- A `touch -d` on an identity file produces **zero events** — `Drift` is correctly indifferent to mtime, so backdating
  is not a way to hide a change *that also changes content*.
- A content change with a **forged-identical** mtime is still caught, at full severity, with both hashes. The hash is
  doing its job; the mtime neither helps nor hurts detection.

So the "attacker can forge it" concern is real only about the **page**, never about the detector. That sentence is the
finding, and it is much smaller than my first draft of it. **Beat 729's rule: keep the measured claim narrower than
the dramatic one.**

## Options — listed, not picked

1. **Compare `ModTime` as a secondary signal** — flag mtime-only changes at `low` severity. Rejected on reflection:
   it fires on every legitimate `touch`, `rsync`, and checkout, and pays a false-positive budget for nothing the hash
   doesn't already cover. Recording it because it is the obvious first idea and I want the reasoning on file.
2. **Rename the page column** to "reported mtime" — one word, tells the operator whose claim they're reading.
3. **Drop the field** from the wire and the page. Costs nothing in detection; loses the "roughly when" hint.
4. **Document it** next to `FileState.ModTime`: *"as reported by the filesystem, unverified, not compared; the SHA256 is
   the authoritative change signal."* **Cheapest, and my pick if I were picking.**

Option 1 changes the alert contract ⇒ **Dad's call** (beat 724). Options 2–4 are documentation-adjacent.

## What I am NOT claiming

- **Not** a vulnerability. `Drift` is content-authoritative and I measured it staying correct under both tampering
  shapes above.
- **Not** that `ModTime` should be removed. It is a legitimate hint and the em-dash rendering is a good decision.
- **Not** that mtime is compared somewhere I didn't look — I grepped all three packages; `drift.go` is the only
  comparison site and it reads `SHA256` only. If one is added, check A goes red.
- **Not** observed in a deployment: the transcript above is the real `Drift` on constructed inventories, not a
  running fleet.

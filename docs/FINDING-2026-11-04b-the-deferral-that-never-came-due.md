# FINDING 2026-11-04b — the deferral that never came due

**Status:** APPLIED. `staleclaim.py` now expires pending claims after `PENDING_MAX_DAYS = 14`.
**Instrument:** `scripts/staleclaim.py` (self-checking; 5 mutants, all distinguishing) +
`internal/rules/engine.go` STALECLAIM annotations, which are the two claims it currently ages.
**Follows:** `2026-11-03` beat 784 (the annotations), beat 792 (queued the hardening), `2026-11-03t`.

## The defect

`pending=` in a STALECLAIM line means "known-queued verification, not a pass." The design was that it
prints on every run so the queue cannot fade from view. That was right about *visibility* and wrong about
*pressure*.

Four beats running, `coverfloor.py --check` has printed a note about `NewEngineRules` /
`normalizePathGlobs`. I have read it four times and done nothing. **That is the measurement** — the
mechanism worked exactly as designed and produced no decision. A queued item with a `next=` field says what
would clear it and never says *when*, so it is not really queued; it is parked.

## Built

- `PENDING_MAX_DAYS = 14`; a pending claim older than that is a **FAIL**, not a `PENDING` line.
- `days_pending(checked)` — dates compared to dates.
- `malformed` split into `malformed` (no `next=`) and `baddate` (unparseable `checked=`), because they are
  different faults and one message cannot name both. N5 proved it: the split branch had been printing
  *"declares pending= but no next="* for a claim that **had** a `next=` and a corrupt date. A failure message
  that names the wrong fault sends the reader to the wrong line.
- `expired` and `baddate` counted in the summary and in `bad`.

## My first version measured nothing, and looked like it did

```python
age = current_beat() - int(beat.split("-")[-1])
```

`checked=2026-11-03` → `3`. `current_beat()` → `795`. It printed **"pending=decide for 792 beats."**
Every fragment of that sentence is wrong: it is not beats, the claim is one day old, and the subtraction
compared a day-of-month to a beat counter.

I found it because **four mutants aimed at the check all failed to fire.** That was the signal, and I had
spent six beats learning to write "failed to fire" as an instrument-power note — so my first instinct was
to blame the mutants. The mutants were fine. There was no coherent number underneath for them to break.

Rebuilt on dates. `current_beat()` was then deleted: no callers left, and a helper nobody calls is the
thing this beat is about.

## Mutation table

| # | mutant | result |
|---|---|---|
| N1 | threshold 100000 | rc=0, 0 expired — silently back to forever-pending |
| N2 | threshold −1 | rc=1, 2 expired — cry-wolf on a 1-day-old claim |
| N3 | backdate `checked=` to 2026-10-01 | rc=1, 1 expired |
| N5 | corrupt `checked=` to `NOPE` | rc=1, and names the **date** after the message split |
| — | control | rc=0, 2 pending, 0 expired |

**Three mutants failed to fire against the garbage-number version** (N1/N2/N3 as first written). Recorded
here rather than dropped, per the rule that a table of only the mutants that worked is a table of successes.

## I destroyed a Go file and nearly filed it as a scanner bug

Restoring a backup, I `cp`'d the wrong source and `internal/rules/engine.go` became a **copy of
`staleclaim.py`** — 15,123 bytes of Python with a `#!/usr/bin/env python3` shebang at the top of a Go
package. My verification step then grepped it, found no STALECLAIM lines, and I came within one sentence of
reporting *"the scanner is dropping claims silently."*

That is the accuser-worse-than-a-blank failure from beat 644, committed again by me, tonight. I caught it by
diffing against `projects/watchman-git` at `c515037` instead of reasoning about my own output.

**And the restore took my own work with it.** `c515037` predates beat 784, so restoring pristine wiped the
annotations that corrected the false `"(used by tests)"` comments. That is beat 789's lesson — *a backup is
only usable if you know which state it holds* — that I had written down myself and then walked into. Both
annotations re-applied with `checked=2026-11-04`, so the expiry clock starts at the re-measurement rather
than pretending to be from yesterday.

## What this does NOT claim

- **Not that the two functions should be removed.** That is the pack owner's call. What changed is that
  the call now has a deadline.
- **Not that 14 days is right.** It is a forcing function, not a policy; Dad can move it one line.
- **No expiry for `ok`/`live` claims** — only `pending=`. Live claims are aged by their cause, not by time.

## Lessons

1. **`pending=` with no deadline is a decision that quietly stops being one.** Visibility is not pressure.
2. **When several mutants aimed at a new check all fail to fire, suspect the check** — I had to unlearn six
   beats of correctly blaming the mutant.
3. **`cp` a backup by absolute path from a source you name out loud**, and after any restore diff against a
   known-good reference before believing the tree. Corrupt a scratch copy, never the real file.

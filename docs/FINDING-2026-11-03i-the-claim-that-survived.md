# FINDING 2026-11-03i — the claim that survived because nothing called the function

**Status:** MEASURED · **FIXED at beat 781** · mutation-proven, `gofmt` clean.
No production code changed; `rules/carbonato.yaml` untouched.
**Instrument:** `internal/rules/needle_engine_test.go`, `scripts/staleclaim.py` (`pending=` branch).
**Follows** `FINDING-2026-11-03h` (the excuse outlives the reason).

## What the audit of the remaining claims found

Beat 780's guard watched one claim. Thirteen more files mention the toolchain. I read all thirteen
and sorted them, because they are not one thing:

| kind | files | verdict |
|---|---|---|
| **design statement** — "runs without a Go toolchain" as a *requirement*, not an excuse | `packcheck.py`, `bindprobe.py` | not a claim; annotating them would be noise |
| **history** — a past measurement described in past tense | `auditsplit.py`, `pruneprefix.py`, `durabilitygap.py`, `hostidshape.py`, `seencensus.py`, `appendcost.py` | not stale; they record what was done |
| **scope note** — "this reads argv, so path globs have no samples" | `eventcheck.py`, `wirediff.py`, `argvany_test.py` | about *this script's* inputs, still true |
| **genuine unverified-engine claim** | `needleprobe.py`, `presencecheck_test.py`, `pathglob_test.py` | STALE — the toolchain is back |

So: ten of thirteen were not stale. **A sweep that annotated all thirteen would have made the guard
cry wolf, and a guard that cries wolf is deleted.** Reading them was the work.

## The sharp one

`substringNeedle` (engine.go:281) decides whether roughly twenty exfiltration rules fire — every
`*domain.com*` glob depends on the contains fallback. Beat 644's retraction, which established that
this fallback is load-bearing on endpoints and pure loss on paths, rested on a mirror.

Measured at beat 781: **no Go test named it.** `grep substringNeedle internal/rules/*_test.go`
returned exactly one hit — `argv_semantics_test.go:195`, and that hit is inside a *Python mirror's
docstring*. The function was exercised only because some argv fixtures happened to route through it.
Coverage by accident, not by assertion.

That is the finding, and it is worse than the stale note: the note was honest. The engine's most
load-bearing helper had no test that would fail if its contract changed, while `go test ./...` was
green.

**Fixed:** `internal/rules/needle_engine_test.go` — twelve direct cases over the extractor
(both star shapes, no-star, interior `*`/`?`/`[` disqualifying, bare `*`, empty) plus the
end-to-end consequence: `*slack.com*` fires on a full URL *only* because of the needle, which is
beat 644's scenario made a compiled assertion instead of a retracted recommendation.

**Mutation-proven:**
| mutant | measured |
|---|---|
| drop the interior-wildcard guard | `substringNeedle("*sl*ck.com*") ok = true, want false` (+2 more) |
| withdraw the needle entirely | `glob "*slack.com*" no longer fires ... roughly 20 exfiltration rules are now silent` |
| control | green |

## The `pending` category, and the escape hatch inside it

`presencecheck_test.py` is genuinely stale and genuinely unfixed — its `detail_string` transcription
*could* now be compiled and isn't. Two bad options existed: leave the gate red forever (people learn
to ignore it, then delete it) or delete the annotation (the queue vanishes).

So `staleclaim.py` gained `pending=N next=<what would clear it>`. A pending claim **prints on every
single run** and does not fail the gate. The queue cannot fade; the gate stays meaningful.

**M1 found the escape hatch in my own mechanism.** With `pending=782` and no `next=`, the claim was
silently pushed into the `undated` bucket, which printed nothing about the missing `next` — the
summary merely shifted a number. A guard that quietly reclassifies a malformed claim as another kind
of bad claim is hiding the defect it just found. Now it fails by name: *"declares pending=782 but no
next= — a deferred verification must say what would clear it."*

First draft of that test was also wrong: `sed` deleted the whole annotation, so the file fell through
to the STALE branch and never exercised the new path. Rebuilt to edit only the `next=` field.
*Verifying a branch requires a mutation that reaches it* — the third time in three beats that a
check of mine was aimed next to the behaviour instead of at it.

## Not claimed

- **Not that all thirteen files are correct.** Ten are judged not-stale by my reading, not by a
  mechanism. A reading is what beats 776–779 kept getting wrong.
- **Not that `pending` cannot be abused.** It can — anyone can write `pending=9999 next=soon`. The
  defence is that the line prints every run in front of whoever reads the gate, which is social, not
  mechanical. A mechanical defence (e.g. an expiry date that fails after N days) is the obvious next
  hardening and is not built.
- **Not that `needleprobe.py` is redundant.** It still cross-checks the source-reading mirror against
  the compiled behaviour. That comparison is now possible precisely because the Go test exists.

## Beat 782 addendum — the `pending` cleared, and two more defects in the guard

`presencecheck_test.py`'s pending item said: "a Go test calling the real `detail_string`".
Measured first — `grep detailString internal/rules/*_test.go` → **zero files**, across nine call
sites (engine.go:105–173). Every path, endpoint and exe the engine judges passes through that reader,
and nothing asserted its contract. Built `internal/rules/detail_engine_test.go`: 9 `detailString`
cases, 8 `detailStringSlice` cases, and an end-to-end test that a `[]byte` path (the wire-decoded
shape) still reaches the matcher. Mutation-proven: dropping the `[]byte` branch fails both the unit
case and the end-to-end test; dropping the element-type skip panics on an `int` in argv.

**Two defects in `staleclaim.py` itself, both mine, both the same species as the thing it hunts:**

1. **A resolved claim printed "still live."** `resolved` claims were bucketed into `ok`, whose output
   line reads *"claim is still live"* — the exact opposite of what a resolution asserts. A guard
   whose output misdescribes the claim is the failure this file exists to catch, one level up. Now
   `RESOLVED ... cause=toolchain ended; verified by X`, and a resolution against a cause that is
   still present fails as premature.
2. **I reinstated the exit-code defect four lines above the comment explaining it.** The verdict was
   computed before the print loop — and that loop is where a premature resolution appends to `stale`.
   So it printed `FAIL` and exited 0. That is verbatim the defect documented in the comment
   immediately below, which I had read while editing. Fixed by computing the verdict after every
   classifier has run.

**Lesson, and Callie's 7566 gave it better words than mine:** *a number is only worth the width of
what it measured.* She spent 159 hours writing "desk proven empty" while only ever checking INBOX;
the fix was to widen the check, not to soften the sentence. A green suite is a width claim too. Mine
said "engine tested" while seven of its readers had never been named by a test.

## Lesson

*Green tests say what is exercised, not what is covered.* `substringNeedle` was "covered" by argv
fixtures, by the pack lint, by the mirror — everything except a test that would notice if it changed.
The check that found it was not a new instrument; it was reading the thing the stale note was
pointing at.

# FINDING 2026-11-03p — the helper written for this case finally got called

**Status:** MEASURED · APPLIED (first production change in 14 beats) · mutation-proven M1/M2 + control,
M3 recorded as failed-to-fire.
**Depends on:** `FINDING-2026-11-01n-fleet-view-is-a-5000-record-sample.md` — **option 4, scoped on 11-01,
never applied until now.**
**Instrument:** `internal/central/fleetorder_test.go` (new, mutation-probed M1/M2 + control),
`scripts/contractruncensus.py` (verifies the sort precedes the Recent append),
`scripts/fleetsamplecensus.py` (check E rewritten — it was green on the defect).
**Files:** `internal/central/fleet.go` (fix), `internal/central/fleetorder_test.go` (new),
`scripts/fleetsamplecensus.py` + `scripts/contractruncensus.py` (census updated),
`scripts/coverfloor.py` (declaration retired).

## The thing I want to write down

I found this three times and filed it as a curiosity three times.

- **beat 725** — "`SortRecordsNewestFirst` has **zero callers** — dead code by design, recorded as a known."
- **beat 01n** — wrote it up properly, listed four options, noted option 4 was *"a two-line change, fixes the
  real fragility rather than the comment's current target."*
- **beat 01o** — found it again, wrote *"the helper written for precisely this case sits with zero callers."*
- **beat 789** (this one) — queued it as `call-it-or-remove-it`.

Each time I produced a *true observation* and no change. The observation kept being restated because it kept
being available. **A finding with options and no decision is a to-do list I keep re-deriving.**

This beat I read option 4 and applied it. Two lines.

## What the code said

```go
// fleet.go:126 (before)
// Records are newest-first, so the first seen is the latest.
if h.LastSeen.IsZero() || rec.When.After(h.LastSeen) { h.LastSeen = rec.When }
```

Three things wrong with that one comment, none of them about the numbers:

1. **The comment describes a precondition, not an enforcement.** `buildFleetView` said "Records arrive
   newest-first" and did nothing to make it so. The promise was the caller's; the dependency was the callee's;
   nothing connected them.
2. **It was attached to the wrong reader.** `rec.When.After(...)` takes the max regardless of order. The
   `lastseen_test.go` file already asserts this, proving the comment was protecting a line that did not need
   protecting.
3. **The reader that genuinely needed the order had no comment.** `Recent` keeps the *first* `recentN` seen:

```go
if recentN > 0 && len(h.Recent) < recentN { h.Recent = append(h.Recent, rec) }
```

On a backend returning arbitrary order, this fills "Recent" with arbitrary records. That is the fragile line,
and it was carrying no documentation at all while the robust line carried a confident, wrong one.

## Is it live?

No. Both backends honor the order today — `eventstore_sqlite.go:204` issues `ORDER BY when_unix DESC, id DESC`,
and the in-memory store walks newest-first. **This is a latent assumption, and I am not claiming anyone's fleet
view is currently wrong.** What makes it worth fixing anyway is that the interface's own documentation
anticipates the backend that would break it (*"a future SQL backend could return any order"*), and the helper
written for exactly that case was sitting uncalled.

`eventstore_sqlite.go:85` even annotates the query with *"The fleet view's primary question: this host, newest
first."* The knowledge was present in three separate places and enforced in none.

## The fix

```go
// ORDER IS ESTABLISHED HERE, NOT ASSUMED (beat 789).
func buildFleetView(recs []Record, recentN int) fleetView {
	if recentN > 0 {
		SortRecordsNewestFirst(recs)
	}
	...
```

Sorted only when `Recent` will actually be populated: sorting is the fix for an order-dependent reader, and
`LastSeen` is deliberately order-independent, so call sites that don't use `Recent` don't pay for it. The
comment moved from `LastSeen` (where it was false) onto `Recent` (where it is true).

## Mutation table

| mutant | aim | measured |
|---|---|---|
| M1 delete the sort | documented-only guarantee returns | 4 assertions name the exact wrong records: `h-1: Recent[0] = "r-b", want "r-d" ... an unordered backend must not put arbitrary records in Recent`; cap test fails |
| M2 sort oldest-first | plausible direction typo | same failures, rc=1 |
| M3 sort unconditionally (`if true`) | is the `recentN > 0` guard load-bearing? | **no test distinguishes it — FAILED TO FIRE.** Recorded, not hidden |
| control | — | 8 packages green, `internal/central` only red on the declared `2026-11-02g` pin |

M3 is honest evidence that the conditional is a **cost choice with no behavioural assertion behind it**. I am
not going to invent a test that asserts an optimisation; the guard stays and the gap is written down.

## Two guards fired, and one of them was green on the defect

`fleetsamplecensus.py` and `contractruncensus.py` both asserted *"zero non-test callers"* — real assertions
that went red the instant one appeared. **That is the system working**: a change to the world forced a
re-derivation instead of allowing silent drift.

But `fleetsamplecensus` check E had an inverted branch:

```python
if re.search(r"// Records are newest-first", src):
    ok("E: the newest-first guarantee is a COMMENT at the point of use")   # green on the defect
else:
    fail("E: the ordering comment is gone ...")
```

It reported health **because** the guarantee was still only a comment, and would have gone red on the fix.
Rewritten to ask the question the cause actually poses. Same species as the `STALECLAIM` checks that went
green on expiry and the census that scored a missing package as a passing one — **a probe that goes green on
the defect is a probe wired backwards**, and the only reason this one surfaced is that I happened to fix the
thing it was watching.

Both censuses now verify the *mechanism* rather than the name: the sort must appear in `buildFleetView` and
must precede the `Recent` append (offset comparison in `contractruncensus`), so a future edit that moves the
sort after the append fails the check.

## Two self-inflicted errors, recorded

1. **I deleted my own fix mid-beat.** Backed up `fleet.go` *after* patching, then ran the M-mutants, then
   "restored" using a copy taken *before* the patch. The control went red, I looked at `Recent[0] = "oldest"`
   and initially read it as a mutant leaking — it was the fix being gone. Lesson already written at beat 786 and
   applicable here: **a backup is only usable if you know which state it holds.** Name it `*-fixed.bak` or don't
   trust it.
2. **An edit failed and printed `AssertionError` while the file stayed unchanged.** I checked the file instead
   of trusting the tool's silence. The old text had a line-wrap I mis-transcribed.

## What this does NOT claim

- **Not that the fleet view was ever wrong** on a shipped backend. Latent assumption, not an incident.
- **Not that the 5000-record cap is fixed** — that is finding 01n option 1 (`Truncated`/`Scanned` banner to make
  the sample's size visible), still untouched and still the cheapest honest fix remaining.
- **M3 has no asserting test**; the `recentN > 0` guard is unverified as behaviour.
- `SortRecordsNewestFirst` **mutates its argument**. Correct here (`buildFleetView` owns `recs`), but a future
  caller reusing a slice should know — worth a doc line, not yet written.

## Lesson

Three beats produced a correct observation of a zero-caller helper. Zero beats produced a caller. The finding
that mattered was already written, with the fix scoped, and I kept re-reading it as though reading were the
work. **When a finding lists options, the next beat's job is to pick one — not to confirm the list again.**

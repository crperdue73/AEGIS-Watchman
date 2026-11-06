# FINDING 2026-11-04a — the loss was real; now it is countable

**Status:** option 3 APPLIED. `2026-11-02g` remains **OPEN** — options 1 and 4 are Dad's call.
**Instrument:** `internal/central/dropped_test.go` — 5 tests, 7 mutants (6 fired, 1 recorded as
failed-to-fire then fixed), negative control on both surfaces.
**Follows:** `2026-11-02g` (open since beat 746), `2026-11-03q` (a count is a claim), `2026-11-03r` (both surfaces).

## What was already known

`2026-11-02g`, written at beat 746 and untouched for ~50 beats: a batch central accepts nothing from still
answers `202`, so a collector deletes spooled evidence that was never stored. `IngestActions` skips an event
with no name and, when that empties the batch, returns `(0, nil)`. The handler sees no error and writes `202`.

I had been treating this as blocked on Dad because options 1 and 4 are. But the finding lists four options
and I had been reading "the contract is Dad's call" as "all of it is." It wasn't. **Option 3 doesn't change
what the API promises — it changes whether anyone can see that the promise is being broken.**

## Applied: option 3, count the drops

- `Service.droppedUnnamed` (`atomic.Int64`), incremented at the single drop point in `IngestActions`
- `Service.DroppedUnnamed()` accessor — a plain read, no caching
- `fleetView.DroppedUnnamed`, `json:"dropped_unnamed,omitempty"`
- set on **both** handler paths (the nil-store early return and the normal path) and on the HTML page
- page renders a `role="alert"` note, `data-testid="dropped-unnamed"`, naming the count **and the cause**

## Two shape decisions worth defending

**1. The page note is outside the truncation block.** A drop happens whether or not the scan hit its cap.
Tying the warning to `Truncated` would hide the case where a *small* fleet silently loses records — the
fleet that never hits the cap, and therefore never looks like the fleet with a problem. Mutant M7 moves the
note inside the block and the test goes red.

**2. `omitempty` on both surfaces, with a negative control on both.** A permanent `"0 dropped"` is training
material for ignoring the alert — the same argument beat 791 used for the rank caveat, and the same failure
I'd otherwise walk into twice.

## Mutation table

| # | mutant | result |
|---|---|---|
| M1 | remove the counter increment | rc=1 — names both the partial and the total-drop case |
| M2 | counter kept, JSON never sets it | rc=1 — "the count exists but was never reported" (beat-790's shape) |
| M3 | report unconditionally | rc=1 — "a field that always reads 0 teaches operators to skip it" |
| M4 | page note removed, JSON still correct | rc=1 — the page-only regression beat 790 exists to catch |
| M5 | page note unconditional | rc=1 — negative control fires |
| **M6** | **page drops the CAUSE ("carried no event name")** | **rc=0 first run — FAILED TO FIRE** |
| M7 | note moved inside the `Truncated` block | rc=1 |
| — | control | rc=0 |

**M6 is the finding inside the finding.** The test asserted `"were received and discarded"` — that the note
says something was lost — but never that it says *why*. An operator reading "3 events were discarded" cannot
act on it. "Because they carried no event name" is the sentence that points at the collector. I added a
separate assertion for the cause and M6 now goes red, but I am recording that it took a mutant to show me my
own test was asserting presence instead of meaning — **the fourth beat in five** that this has happened.

## What this does NOT claim

- **`2026-11-02g` is not closed.** A total drop still returns `202`. The declared-red pin
  `TestHTTPActionsTotalDropIsNotAccepted` still fails and I left it failing rather than re-deriving it to
  match what I built — a pin re-derived to fit the code is not a pin.
- **Not that the collector is fixed.** The spool still treats `202` as acceptance. This half is central's
  visibility; the collector half is untouched since beat 746.
- **Not that a drop is prevented.** Prevention is options 1 and 4.
- **No alert is raised on a drop.** It is a count on the console, not a page. Whether a non-zero drop should
  page someone is a product decision.

## Mistakes I made and corrected in this beat

- Wrote the first test file against **invented** helpers (`fakeStore`, `recordingStore`, `Event.Path`,
  `stringsContains`) instead of reading the tree. Rebuilt against `NewMemoryStore`, `NewMemoryRecordStore`,
  and the real `schema.Event`.
- Wrote the page test against `renderFleetPageFor`, which builds its own `&Service{records: store}` and
  **can never carry a drop count**. It would have been a test asserting a service I did not construct.
  Switched to driving the template with a view I built, and wrote the reason in the comment so the next
  person doesn't "fix" it back.

## Lesson

"Blocked on decision" covered four options when only two of them were blocked. **When a finding lists
options, separate the ones that change the contract from the ones that change the visibility** — the second
kind is usually mine to do, and doing it makes the first decision easier by putting a number next to it.

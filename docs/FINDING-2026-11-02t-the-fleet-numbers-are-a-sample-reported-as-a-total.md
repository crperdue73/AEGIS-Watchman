# FINDING 2026-11-02t — the fleet numbers are a sample reported as a total

**Status:** MEASURED · **LIVE at HEAD** · **NOT APPLIED**
**Instrument:** `internal/central/fleetsample_test.go` (Z1–Z5), `-race` clean.
**Sequel to:** 2026-11-02s ("Last seen" is a detection time) — same table, same class of harm.

## The short version

`GET /api/v1/fleet` builds its counters from the records it **scanned**, and the scan is capped:
`defaultFleetLimit = 5000` (`fleet.go:41`), read from `?limit=` at `fleet.go:67`. Every number on the fleet page —
`TotalEvents`, per-host `Total`, `By severity`, `By technique`, `By source`, `By pack` — is computed from that window.

**`fleetView` has no field that can say "showing N of M".** So when the cap is reached, the console shows partial counts
as totals, and it shows them to the one reader who is about to make a triage decision with them.

## Measured

| Test | Question | Measured |
|---|---|---|
| **Z1** | are counts exact under the cap? | **Yes** — 15 records, `TotalEvents=15`, per-host sums to 15. The arithmetic is right. |
| **Z2** | is truncation reported when the cap is hit? | **No** — store holds **80** records, view reports `TotalEvents=10`, and `fleetView`'s fields are exactly `[GeneratedAt TotalEvents Hosts]`. Neither can express "10 of 80". |
| **Z3** | does the cap change the ranking? | **Yes** — view ranks `h1` first with `Total=3` while the store holds **30** for `h2`. The documented "worst-first, leading with what an operator must look at" ordering is computed over the sample. |
| **Z4** | can a caller set the cap? | **Yes** — `?limit=1` is a legal request. No minimum, no warning, no echo of the effective limit in the response. |
| **Z5** | does anything else report it? | **No** — no `Truncated`, `ScannedLimit`, or `TotalAvailable` field exists. The page renders these numbers under `Total` / `Max sev` headers. |

**Mutation:** doubling `h.Total` per record → Z1 red (`sum to 30, want 15`). `fleet.go` md5 `fa3967fc34e83450f1a591556c180b36`
restored. A mutant lowering `defaultFleetLimit` does **not** move Z1 — correct, because Z1 tests `buildFleetView`, which
takes the record slice it is given and has no opinion about the handler's cap. That distinction is the reason Z2 tests at
the store+query level instead.

## Why this is the sharper half of the pair

Finding `s` is a naming problem: the column means "last detection" but says "Last seen." This one is a **quantity**
problem, and quantities are worse because they look self-evidently authoritative. A label can be argued with; a number
cannot. "Total: 3" on a host that has 30 events is not ambiguous, it is wrong, and nothing on the page marks it as an
estimate over a window.

And the truncation is not hypothetical: with `limit` operator-settable (Z4), any dashboard that adds `?limit=100` for
speed silently converts every count on the page into a sample.

## Severity, honestly

- **Bounded by default:** 5000 records is a lot, and retention bounds the store. Below the cap, Z1 says every number is
  exact. So this bites on busy fleets or small `?limit=`, not on a quiet two-host lab.
- **Loud-adjacent, not silent:** the ordering in Z3 makes the distortion *visible* if you know what you're looking at —
  a host with 30 events appearing below one with 3 is a hint. But you'd have to already suspect the number to notice.
- **No detection is missed.** Nothing stops firing; the console misdescribes volume and therefore priority.

So: **latent-to-live depending on fleet size, wrong-direction (under-reports), on the primary triage surface.** I'm
rating it above `s` on impact and below `s` on likelihood.

## Options — Dad's call, none applied

1. **Report the window.** Add `ScannedLimit int` and `Truncated bool` to `fleetView`; the page renders "showing 5,000 of
   ≥ N" when set. Cheap, honest, and keeps the cap. **Recommended.**
2. **Ask the store for a count.** A `Count(ctx, query)` alongside `Query` lets the view say "3 of 30" without scanning —
   the right fix if the store can do it, and it retires Z3's misranking entirely. Bigger change.
3. **Drop the cap and rely on retention.** Simplest to reason about, worst for latency on a large store — and the comment
   explicitly added the cap so a query stays cheap.
4. **Warn only.** A log line when the cap is hit. Helps an operator who reads logs, not one who reads the page.

Option 1 is the smallest honest step and it's the one I'd build; option 2 is the one that makes "worst-first" true.

## Process note — the fixture was wrong first, again

Z3 failed on the first run with `expected a capped window (<=3 records for h1), got 5 for h2`. **The code was right and
my fixture was backwards** — I appended `h2` last and assumed `h1` would occupy the newest window, without reading that
`MemoryRecordStore.Query` walks the slice newest-first (`eventstore.go:166`). Fixed the fixture by reversing the append
order, which made the cap genuinely hide the busier host.

That's the fifth beat in a row where the first red was me. **The pattern is consistent and worth naming: I write the
fixture from my mental model of the call, and the repo's actual order/typing catches me.** The countermeasure that is
working is reading the callee before writing the fixture — not reading it after the red.

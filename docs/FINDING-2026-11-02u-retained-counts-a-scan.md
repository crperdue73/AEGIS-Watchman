# FINDING 2026-11-02u — "event(s) retained" counts a scan, and the page can't be filtered

**Status:** MEASURED · **partly LIVE (header wording), partly NOT REACHABLE (empty state)** · **NOT APPLIED**
**Instrument:** `internal/central/fleetclaim_test.go` (A1–A5, A2b), `-race` clean.
**Sequel to:** 2026-11-02t (fleet numbers are a sample reported as a total).

## The short version

Three findings in a row on the same surface, so I read the page header — a sentence, not a column:

> `fleetpage.go:157` — `{{.TotalEvents}} event(s) retained`

`TotalEvents` is a count over the **scanned** records (finding t). "Retained" is a word about the **store** — how much
history exists. Those are the same number only while the scan is short. The page asserts the identity with no hedge.

**And then I was wrong about how an operator reaches it, which is the more useful half of this finding.**

## Measured

| Test | Question | Measured |
|---|---|---|
| **A1** | below the cap, is the number exact? | **Yes** — 12 records → `total_events:12`. Below the cap the sentence is true. |
| **A2** | over the cap, does `total_events` under-report? | **Yes** — store holds **60**, `?limit=5` → `total_events:5`, rendered as "event(s) retained". |
| **A2b** | is that sentence actually pinned by a test? | **It was not.** Renaming "retained"→"scanned" left **every test green**. I added the pin; the same mutant now fails by name. |
| **A3** | what does `?limit=0` do? | **Unbounded, not zero** — returns all 20. So the cap only narrows for a *positive* integer; there is no floor between 0 and 1. |
| **A4** | can a filter make the HTML page claim "No events retained" over a full store? | **No.** `handleFleetPage` takes **no query parameters** (`fleetpage.go:64` passes `""` and `defaultFleetLimit` unconditionally). With a genuinely empty store the sentence is **true**. |
| **A5** | how wide is the mouth? | **The JSON endpoint: one parameter. The HTML page: 5,000+ records.** The two surfaces have different exposure. |

## The correction, since it shrinks the finding

I wrote A4 as a filter-reachability test: ask for `?since=` a year in the future, get an empty view over a full store,
and conclude the page tells the operator "the fleet is quiet" when it isn't. **That test failed, and the code was right.**
`handleFleetPage` never reads the query string — it's the JSON endpoint (`handleFleet`) that parses `limit`/`since`/`host`/
`min_severity`.

So the honest split is:

- **JSON `/api/v1/fleet`:** `?limit=5` gives `total_events:5` from a 60-record store. **LIVE, one parameter.** (finding t)
- **HTML `/`:** no parameters, `defaultFleetLimit` = 5000. Needs **5,000+ retained records** to under-report, and when it
  does, the header quietly says "retained" over a scan count. **Latent by default, live on a busy fleet.**

The empty-state sentence "No events retained. Either the fleet is quiet — or nothing has reported yet." is **accurate
today**. It becomes misleading the day the page gains filters, which is a plausible next feature — so I've pinned it to
fire at that moment rather than calling it a bug now.

That's a downgrade, and I'd rather deliver it than the tidier version I first believed.

## What's still a defect

1. **The header's word is wrong at fleet scale.** "N event(s) retained" over a capped scan. Same fix as finding t —
   report the window — but the header is the most-read sentence on the page, so it matters more than a table cell.
2. **No instrument covered it.** A2b measured that renaming the sentence kept every suite green. That's now pinned.

## Options — Dad's call, none applied

1. **Same as finding t, one word wider:** when the scan was capped, render "event(s) scanned" or "showing N of ≥M"; leave
   "retained" for when it's true. **Recommended** — it's the honest minimum and it costs a template conditional.
2. **Count, don't claim:** a store `Count(ctx, q)` lets the header say the real retained total regardless of the scan.
   Retires both t and u. Bigger change.
3. **Drop "retained", say "shown."** Truthful immediately, loses the history information entirely. Cheapest.
4. **Leave it.** Only defensible if the fleet is expected to stay under 5,000 events — worth saying out loud if so.

## Process note — the pin that didn't exist

The most damaging thing I measured this beat wasn't the wording; it was that **no test covered the wording.** I had
written A2 to assert on the JSON and *describe* the HTML in a log line — a claim with no instrument, which is precisely
the defect class I've spent a week documenting. Mutating the sentence proved it.

Fixed by adding A2b, which now goes red by name on the same mutant, verification run both directions.

Three self-corrections this beat: the header had no pin, the empty-state reachability was wrong, and my test called a
`*`-path handler at `/api/v1/fleet` and got a 404 it read as an empty page. All three were mine; none changed the code.

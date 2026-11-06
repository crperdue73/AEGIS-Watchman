# FINDING 2026-11-03r — the table made the same claim the header did

**Status:** FIXED (disclosure), ranking change left as a product decision · mutation-proven R1–R3 + controls.
**Instrument:** `internal/central/fleetpage_trunc_test.go` — `TestFleetPageNeverReportsMoreEventsThanItsOwnCap`
(P4 closure), `TestFleetPageFlagsThatTheRankingIsSampled`, `TestFleetPageDoesNotFlagRankingWhenComplete`.
**Files:** `internal/central/fleetpage.go`, `internal/central/fleetpage_trunc_test.go`.
**Follows:** `2026-11-03q` (the header said "retained"), `2026-11-01n` (the 5000 cap), Z3 in
`fleetsample_test.go` (measured 11-01, never acted on).

## Two things went wrong, and they were the same thing

**1. P4 — my own fix, unenforced.** Beat 790 shipped the truncation flag and recorded honestly that
deleting `recs = recs[:want]` changed no assertion. The capability was right, the enforcement was
untested. A page that reported `cap+5` events under a stated cap of `cap` would have passed every test
I had.

**2. Z3 — a claim I made on one surface while fixing another.** The header stopped calling a sample a
total. The table underneath kept sorting hosts worst-first by a severity computed over that same
sample. The comment on that sort reads:

```go
// Order hosts worst-first: by max severity, then by total, so the console
// leads with what an operator must look at.
```

*"The console leads with what an operator must look at."* With a cap, it leads with what an operator
must look at **among the first 5000 records**. The worst host in the store may not be on the page at
all. I fixed the sentence about the count and left standing the sentence about the ranking, in the same
document, twelve lines apart.

**When a number stops being a total, every sentence derived from it stops being true.** The header was
one derivation. The sort order was another. I treated the fix as one edit to one string.

## What was done

**P4 closed with an assertion that reads the page.** Not `fleetView.TotalEvents` — the rendered text:

```go
shown := parse `(\d+) event\(s\)` from the body
capN  := parse `capped at (\d+)` from the body
if shown > capN { /* counting records it claims not to have read */ }
```

Placement is the whole point. P4's defect lived in `handleFleetPage`, between the query and the view. A
test that began at `buildFleetView` — as several existing ones do — could never have observed it. Beat
790's P4 failed to fire for exactly this reason, and the lesson is one I have now written down four
times: **aim the mutant at the behaviour, and aim the assertion at the same place the mutant is.**

**The ranking caveat.** The page now says, only when truncated:

> Ranking is over the scanned window, not the whole store — a host with higher severity outside the cap
> is not shown, and the worst host here may not be the worst host you have.

I did **not** change the ranking. Ordering by a severity computed over a partial set is a product
decision — the honest alternatives are (a) rank over the whole store, which means a second query or an
aggregate, or (b) bound the claim (`"worst is ≥ high"`), which changes what the column means. Both are
Dad's call. What is mine is whether the page *admits* what it is doing, and it did not.

## Mutation table

| mutant | aim | measured |
|---|---|---|
| **P4 replanted** — delete `recs = recs[:want]` | the enforcement of my own beat-790 fix | `page reports 5001 events while stating a cap of 5000` |
| P4b — slice a copy, build the view from the full set | the subtler version of the same | same failure, rc=1 |
| P4c — cap enforced, `Truncated` forced false | half-fix | `page says 'event(s) retained' for a capped scan` |
| R1 — remove the caveat block | the claim returns | `capped page ranks hosts worst-first with no caveat` |
| R2 — print the caveat unconditionally | cry wolf | `complete view says its ranking is a sample; ... a caveat printed everywhere is a caveat read nowhere` |
| R3 — soften to "Sorted by severity" | wording drift | `caveat missing or reworded`, rc=1 |
| control | — | green |

R2's existence is the point of `TestFleetPageDoesNotFlagRankingWhenComplete`. Disclosure has a failure
mode in both directions, and the loud one is not the dangerous one: a caveat printed on every page —
including correct ones — is training material for ignoring it.

## A stale message caught by my own mutant

P4c's output surfaced that `TestFleetPageDoesNotClaimRetentionWhenTruncated` still said *"a capped scan
of 40 records with limit=10"* — the superseded beat-790 premise, where I had wrongly assumed the page
took a query param. The assertion was right; the failure message described a world that no longer
existed. A failure message is what a person reads at 3 AM when the gate is red — **it has to describe
the tree as it is, not the tree as it was when I wrote the line.** Now interpolates
`defaultFleetLimit+5` / `defaultFleetLimit`.

## What this does NOT claim

- **Not that the ranking is fixed.** The order is still computed over the window. It is now labelled.
- **Not that the caveat is the right fix.** It is the cheapest honest one. If Dad wants severity bounds
  (`Column reads "≥ high"`), that is a better answer and this note should be replaced by it, not coexist
  with it.
- **Not that Z3 is resolved.** `fleetsample_test.go` Z3's `t.Logf` still describes the misranking; the
  finding is disclosed to the operator, which is not the same as the ranking being sound.
- **No change to `rules/carbonato.yaml`.** Detection behaviour is untouched; this is console surface.

## Lesson

Beat 790's headline was "retained is a claim about the store." The header was the obvious place that
claim lived. It was not the only place — it was the place I was looking. The table below made a
stronger version of the same claim (*"leads with what an operator must look at"*) and I read past it
because I had already decided what the fix was.

**A defect is a claim, not a string.** Find every place the claim is made before declaring the claim
withdrawn. I fixed the one I could grep for.

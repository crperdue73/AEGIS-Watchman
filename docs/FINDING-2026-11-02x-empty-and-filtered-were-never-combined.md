# FINDING 2026-11-02x — the suite tested "empty" and "filtered", never "filtered to empty"

**Status:** MEASURED · **method finding, not a new defect** · **NOT APPLIED**
**Instrument:** `internal/central/filteremptyguard_test.go` (D1–D3), `-race` clean.
**Explains:** why finding 2026-11-02v was invisible to a suite that already had the two neighbouring tests.

## The short version

`agentspage_test.go` contained both halves of the question and never combined them:

| Existing test | What it constructs | What it asserts |
|---|---|---|
| `TestAgentsPageEmptyState` (line 135) | **empty store, no filter** | the sentence appears — comment says it renders *"honestly"* |
| `TestAgentsPageFilters` (line 156) | **two baselines, filter h-2 (matches)** | h-2 present, h-1 absent |

The intersection — **non-empty store, filter matches nothing** — is the exact cell where "honestly" turns false, and it
was never rendered. That is not a random coverage hole. It is the specific cell where the sentence stops being true, and
the suite was arranged so that cell could not be reached by either test.

**D1 completes the 2×2 and measures all four cells:**

1. empty store, no filter → sentence, **true**
2. non-empty store, filter matches → rows, no sentence
3. **non-empty store, filter matches nothing → same sentence as cell 1** ← the missing cell
4. empty store + filter → sentence, true (different reason; included so the grid is real)

**Mutation:** changing the sentence to "No baselines match that filter." → **D1 fails by name** (*"if the page now
distinguishes filtered-empty, update this pin and retire finding 2026-11-02v"*). Restored; `agentspage.go` md5
`bfe4b4dd775a07959ca21967e0b42876` unchanged.

## D2 — the guard, and the false positive it caught in me

D2 asserts: *no page may reuse a store-emptiness sentence for a filter-emptiness outcome.* Today the roster violates it,
so D2 **declares** the violation in its log rather than hiding it — a violation that's announced is auditable; one that's
silently tolerated is the whole problem.

My first draft listed **two** pages, roster *and* fleet, and D2 dutifully reported two violations. **The fleet one was
mine.** `fleetpage.go:57-67` calls `Recent(ctx, "", defaultFleetLimit)` and never reads the query string, so
`/?host=no-such-host` cannot filter it — the "violation" was my URL being ignored. Exactly the error I made at beat 767
and retracted, reappearing inside the guard I wrote to prevent it.

Fixed by removing the fleet page from the list *with the measurement in the comment* ("no URL can filter it and this
defect cannot arise there"). Guarding a page that can't reach the failure is noise, and noise in a guard is how a guard
stops being read.

D3 exists because otherwise D2 is a log line that has never been red: it runs the same predicate over synthetic bodies
and requires `true` for a store claim with no filter marker, `false` for the honest wording.

## The transferable part

This is the third finding in this cluster (v, and 767's correction) where the defect was not in the code but in **the
shape of the evidence I had collected**. Two tests each covered a neighbouring cell; the union of their coverage looked
like the whole question and was not. The failure mode is:

> **Two tests that each cover one side of an intersection create the appearance of covering the intersection.**

That is worth more to me than the roster bug. I have been treating "the suite covers empty state and it covers filters"
as evidence that both work together. It is evidence of exactly nothing about both together — and because both tests
pass, the suite actively *recommends* confidence it hasn't earned.

## Options — Dad's call, none applied

1. **Fix the roster (finding v option 1) and let D1's pin fail.** The sequence the pin was written for: apply the fix,
   D1 goes red with instructions, re-point D1 at the filtered wording, D2 stops declaring. **Recommended.**
2. **Leave production alone; D2 keeps declaring the violation every test run.** Costs nothing, stays visible in `-v`
   output — but declared noise in a passing suite has a short half-life.
3. **Generalise D2 into a page audit:** enumerate every HTML handler, decide mechanically whether it can filter, and
   require the emptiness wording to match. More work; catches the *next* page before I write it wrong.

# FINDING 2026-11-03q — "retained" is a claim about the store; 5000 was a claim about a LIMIT clause

**Status:** FIXED (finding 2026-11-01n option 1, open since 11-01) · mutation-proven M1–M6, P1–P3 + control,
**P4 recorded as failed-to-fire**.
**Instrument:** `internal/central/fleettrunc_test.go` (5 tests, drives the HTTP handler),
`internal/central/fleetpage_trunc_test.go` (2 tests, renders the real template and reads the words),
`scripts/fleetsamplecensus.py` checks A and D re-derived.
**Files:** `internal/central/fleet.go`, `internal/central/fleetpage.go`, `internal/central/fleetsample_test.go`
(Z2/Z5 rewritten), 2 new test files, 1 census script.

## The claim

The console header said:

```
{{.TotalEvents}} event(s) retained · {{len .Hosts}} host(s)
```

`TotalEvents` is `len(recs)` — the records **scanned**. The scan is capped at `defaultFleetLimit = 5000`,
and the cap is operator-controllable via `?limit=`.

So on any store holding more than 5000 records, the page stated as fact a number that was really *"the
first 5000 I looked at"*. The word doing the damage is **"retained"**. Retention is a property of the store.
This number was a property of a `LIMIT` clause.

Worse in kind than a wrong counter: finding `2026-11-02s` shows a quantity with the wrong *meaning*, this
shows a **truncated** quantity with no marker at all. And worse still because the cap is reachable by the
reader — `?limit=1` made the console report one event, with no warning, no minimum, and no echo of the
effective limit.

## Why it sat open for two days

I wrote `fleetView` the truncation question on **11-01** (`2026-11-01n`), with four options. Option 1 was
*"add a `Truncated` flag"*. Option 4 was the ordering fix I shipped at beat 789. I picked the interesting
option and left the honest one.

The measurement was already in the tree — `fleetsample_test.go` Z2 logs *"store holds 80 records; view
reports TotalEvents=10 ... no field can express 'showing 10 of 80'"*. **A test that measures a defect and
asserts only the measurement is documentation, not a guard.** And Z5, written as the brake, hardcoded its
own field list:

```go
return []string{"GeneratedAt", "TotalEvents", "Hosts"}
```

It was supposed to fail the moment a truncation field appeared. It could not: the list was a literal, so
adding `Truncated` to the struct left Z5 green. **A guard that contains the answer cannot notice the answer
changing.** That is the same shape as beat 788's bare-identifier keys and beat 789's inverted census — the
third time this week that a check named the right thing and read the wrong one.

## The fix

**1. Ask for one more than the cap.**

```go
Limit: scanLimit + 1,
```

This is the whole trick, and it took the longest. From a bare `LIMIT n` there is no way to distinguish
*"exactly n and complete"* from *"n of more"*. The extra row is the probe:

```go
truncated := len(recs) > scanLimit
if truncated { recs = recs[:scanLimit] }
```

M1 proves it matters: without the probe — using `len(recs) >= scanLimit` — a store holding exactly 5000
records is labelled **truncated**. That is a lie in the safe direction, and safe lies are how a flag gets
ignored.

**2. `Truncated *bool`, not `bool`.** A pointer keeps "absent" distinguishable from "false" on the wire, so
an older client sees a missing field rather than being told `false` by a server that never checked.

**3. `ScanLimit` echoed.** A number whose ceiling the reader cannot see is read as a total.

**4. The page says which it is.**

```
{{if deref .Truncated}}
  {{.TotalEvents}} event(s) scanned (capped at {{.ScanLimit}} — this is a sample, not the store's total)
{{else}}
  {{.TotalEvents}} event(s) retained
{{end}}
```

## The `*bool` trap

The template's first version was `{{if .Truncated}}`. Go's `text/template` treats **any non-nil pointer as
true** — so a complete view took the "sample" branch and printed *"capped at 5000, this is a sample"* over
five records. Caught by `TestFleetPageStillSaysRetainedWhenComplete`, which exists purely as the control so
that "never say retained" cannot become the fix.

The type that made the JSON honest made the HTML lie. A `deref` template func resolves it; P1 reverts and
proves the test catches it.

I also had to write a **second** probe into `handleFleetPage`, because that handler built its own query and
called `buildFleetView` directly — it never set `Truncated` at all, so the page would have kept the old
sentence even after the JSON endpoint learned better. **Adding a field is not reporting it.** The API and the
console are two surfaces; I fixed the one I was looking at first.

## Mutation table

| mutant | aim | measured |
|---|---|---|
| M1 drop the `+1` probe (`>=`) | the obvious implementation | `TestFleetViewAtExactlyTheCapIsNotTruncated`: `truncated = true when the store holds exactly the limit (10)` |
| M2 never report truncated | pre-fix behaviour | `TestFleetViewOverTheCapReportsTruncated`: `truncated = 0xc00... for 40 records under a limit of 10` |
| M3 always report truncated | cry wolf | 4 tests fail incl. `TestFleetEndpoint` |
| M4 handler stops setting the field | field exists, response lacks it | `truncated is absent; a complete view must state that it is complete` |
| M5 scan limit not echoed | ceiling hidden | `scan_limit = 0, want 50` |
| M6 revert Z5's helper to a literal | Z5's own blind spot | rc=1 |
| P1 template reverts to `{{if .Truncated}}` | the `*bool` trap | `complete view claims it is a sample` |
| P2 page stops setting Truncated | the original gap | `page says 'event(s) retained' for a capped scan` |
| P3 page says retained unconditionally | pre-fix sentence | same, rc=1 |
| **P4 page never slices recs** | **cap not enforced on the page** | **rc=0 — FAILED TO FIRE** |
| control | — | green |

**P4 is the honest gap.** If `recs = recs[:want]` is deleted, the page builds a view over `cap+5` records
while still claiming `scan_limit = 5000`. No assertion catches it, because `TotalEvents` would be 5005 and
nothing pins that number against the cap. The fix is correct; the *enforcement of the cap on the page* is
untested. Recorded rather than papered over.

## Two census guards fired and both were right

`fleetsamplecensus.py` A and D both went red on my change. Both had been written to **assert the defect** —
A required the literal single-call query and failed if `fleetView` grew a `Truncated` field; D required
`Recent(ctx, "", defaultFleetLimit)` exactly.

That is the census doing its job: a change to the world forces a re-derivation instead of permitting silent
drift. Note this is the *opposite* polarity from beat 789's check E, which went **green** on the defect.
Both are the same principle — *the census must encode what is true now, and fail when the world moves* —
and only one of the two polarities can be right for a given sentence.

Re-derived, A now requires `Limit: scanLimit + 1` **and** `Truncated *bool` **and** says the gap has
reopened if the field vanishes. D now requires the probe, the assignment, **and**
`{{if deref .Truncated}}` in the template — because a page that fetches correctly but never labels itself
is precisely the gap this beat closed.

## My own wrong premises, recorded

1. **Tested the page at `/fleet?limit=10` and got 404.** The console is served at `/` and takes no query
   params. The route guard `r.URL.Path != "/"` caught my guess.
2. **Then seeded 40 records and expected truncation** — but the page uses `defaultFleetLimit`, not my query
   param, so 40 records is a *complete* view and the page was right. I nearly edited the template to satisfy
   a premise that was wrong. Fixed the test, not the code.
3. **Template comment contained `{{.TotalEvents}}`**, which terminates a Go template comment early, and
   produced a syntax error two vet runs in a row that I initially read as a stray `}}`.

## What this does NOT claim

- **Not that anyone's console was misreading a real fleet.** Default cap is 5000; it bites only on stores
  larger than that. Latent for most deployments, live for a large one, and trivially reachable via `?limit=`.
- **P4** — the page's cap enforcement is not asserted (see table).
- **The JSON `total_events` name is unchanged.** It still sounds like a total. `Truncated` + `scan_limit` now
  qualify it, which is honest but not *self-evident*; renaming the field is a breaking API change and Dad's
  call, and 0.2.0 is already public.
- **Z3's misranking stands.** With a cap, "worst-first" ordering is computed from the sample. This beat made
  the sample *visible*, not representative.

## Lesson

Finding `2026-11-01n` listed four options on 11-01. Beat 789 shipped option 4 because it was the interesting
one. Beats 725, 01n, 01o and 789 all *noticed* option 1 and none did it, because noticing it produced a
correct sentence and a sentence felt like progress.

The measurement was in the tree the whole time, logged as `t.Logf`. **A logged measurement is a note to
myself; only an assertion is a promise to the next person.** Z2 asserted the *bug* and Z5 asserted a
*hardcoded* field list, so the pair looked like coverage from every angle except the one that mattered.

# FINDING 2026-11-03s — the host that was loud and then went quiet is not ranked low, it is absent

**Status:** DISCLOSED (page), root cause open · mutation-proven S1–S4 but **S3 initially failed to fire and the
test was strengthened**.
**Instrument:** `internal/central/fleetpage_trunc_test.go` —
`TestFleetPageSaysHostsCanBeMissingWhenCapped`, `TestFleetPageDoesNotWarnAboutMissingHostsWhenComplete`.
**Follows:** `2026-11-03q` (count), `2026-11-03r` (ranking), `2026-11-01n` (the cap), Z3.

## The measurement

I had been describing this defect as *misranking* since `2026-11-01n` — "worst-first ordering is computed
from a sample." Beats 790 and 791 labelled that. Then I asked a narrower question: **what does a host look
like when none of its records are in the window?**

Store: three `critical` records for `OLD-CRITICAL`, older; fifty `info` records for `noisy-info`, newer.
Cap 10.

```
PROBE: 10 records in window -> hosts: noisy-info(10,info)
PROBE: OLD-CRITICAL present? false
```

The critical host is not at the bottom of the table. **It is not in the table.** The header reads
`1 host(s)`.

The window is newest-first — `ORDER BY when_unix DESC, id DESC LIMIT ?` in `eventstore_sqlite.go` — so the
cap keeps the most *recent* events, not the most *severe*. A host that raised three critical alerts on
Friday and has been quiet since is precisely the host excluded by a recency-based cap.

**"1 host(s)" is a claim that there is one host.** It is a count of hosts in a window, presented as a
roster. Beats 790 and 791 fixed two claims about *numbers*; this is a claim about *membership*, and it is
the one that loses an actual incident.

## Why this took three beats to see

At `2026-11-01n` I wrote "worst-first ordering is computed from a sample" and Z3 demonstrated it by
checking that the top row had `Total <= 3` — a host that *was* present but under-counted. That measurement
was correct and it taught me the wrong lesson: it made the defect look like a *ranking* problem, and a
ranking problem sounds like it needs a better sort. A better sort cannot fix an absence. **Z3's fixture
guaranteed the critical host appeared in the window**, because it only put 33 records in a 5-record view
across two hosts. The absent-host case was never in the fixture.

That is the useful part: **the test that measured the defect also defined what I thought the defect was.**
I inherited its framing for two days without asking whether the harshest case looked different.

## The disclosure

Two sentences now print only when the view is capped:

> Ranking is over the scanned window, not the whole store — a host with higher severity outside the cap is
> not shown, and the worst host here may not be the worst host you have.
>
> **Hosts can be missing entirely:** the window holds the most RECENT 5000 events, so a host with nothing
> recent is not ranked low — it is absent from this list. The host count below is a count of hosts in the
> window, not a roster.

The second is stronger, so it gets its own element and its own testid. Merging them would have made the
weaker sentence carry both claims.

## Mutation table

| mutant | aim | measured |
|---|---|---|
| S1 remove `hosts-incomplete`, keep rank caveat | new claim returns | `the page lists N hosts with a host silently absent` |
| S2 print it unconditionally | cry wolf | `complete view warns that hosts may be missing; none are` |
| **S3 caveat → "some hosts may not appear"** | **cause removed** | **rc=0 — FAILED TO FIRE** |
| S3 after strengthening | cause removed | `the caveat does not say the window is the most RECENT events` |
| S4 keep cause, drop "not a roster" | the actionable half removed | `the caveat does not correct the 'N host(s)' reading` |
| control | — | green |

**S3 failed on the first attempt.** My test asserted the `data-testid` and nothing else, so a caveat
stripped to *"some hosts may not appear"* passed — a warning that names no cause, which an operator reads
as a glitch rather than a property of the query. I could have recorded that as a known-weak assertion; it
was cheaper to assert the *why* and the *correction* separately, so now removing either half fails by name.

This is the third beat running where a test I wrote named the right element and asserted too little of it.
The recurring shape is not "I forgot to test" — it is **"I asserted the presence of the thing instead of
the meaning of the thing."**

## Root cause — Dad's call, scoped

Disclosure is not a fix. The real options, in order of what I'd argue for:

1. **Per-host cap instead of a global cap.** The table has one row per host; the query has one global
   `LIMIT`. `GROUP BY host_id` with a per-host window, or N per-host queries, would make "every host I
   have events for is present, with its last K events." Costs a schema/index change and makes `TotalEvents`
   mean something different. **This is the shape of the right answer** — the page's unit is a host, so the
   cap should be applied at the unit the page reports.
2. **Two-phase query: roster first, detail second.** `SELECT DISTINCT host_id` (cheap, bounded by fleet
   size, not event count), then top-N events per host. Every host appears; `Recent` is still a sample and
   already labelled.
3. **Severity-ordered window instead of recency-ordered.** `ORDER BY severity_rank DESC, when_unix DESC`
   keeps the loudest events. Cheaper than (1) and (2) but wrong in a different direction: it would drop
   *quiet recent* activity, and an operator would see a stale critical row as if it were current. I'd
   reject this, but it is a legitimate option and it is not mine to pick.
4. **Leave it, rely on the disclosure.** Defensible only if 5000 is rarely reached. It is not — the default
   store is `7 days / 10k records` (`eventstore.go:122`), so a fleet producing a few thousand events a
   week hits the cap routinely, not only at extremes.

I did **not** change any query. Options 1 and 2 change the wire schema and the meaning of `TotalEvents`,
and 0.2.0 is public.

## What this does NOT claim

- **Not that the console is now correct.** It now says it is incomplete. Incomplete and honest is better
  than incomplete and confident, and still incomplete.
- **Not that Z3 is fixed.** Z3's `t.Logf` still describes misranking; the absent-host case is now a real
  test but Z3 itself remains a measurement, not a guard.
- **Not that the two caveats are the right permanent answer.** If option 1 or 2 lands, both should be
  deleted, not left as noise — `TestFleetPageDoesNotFlagRankingWhenComplete` and
  `TestFleetPageDoesNotWarnAboutMissingHostsWhenComplete` exist to make that deletion *visible* rather
  than silent.
- **No change to detection.** `rules/carbonato.yaml` untouched; this is entirely console surface.

## Lesson

For two beats I called this "misranking," because the first test that measured it framed it that way and
its fixture happened to keep the most important host inside the window. **The instrument that found the
defect also told me what kind of defect it was, and I believed it without asking what the worst case
looked like.**

A ranking bug and a missing-row bug need completely different fixes, and the label I inherited pointed at
the wrong one.

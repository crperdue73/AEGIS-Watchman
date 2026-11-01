# FINDING — 2026-11-01n: the fleet view is a 5000-record sample that never says so

**Status:** source reads. **NOT CLAIMED as observed** (no toolchain, beat 642). **NOT APPLIED.**
**Instrument:** `scripts/fleetsamplecensus.py`.
**One line:** the fleet rollup is built from at most 5000 of the newest records; once a store exceeds that, **a host
with no recent event is absent from the fleet view**, and nothing on the view says the view is a sample.

## The chain

```go
// fleet.go:42
const defaultFleetLimit = 5000          // "a second guard so a query is cheap"
// fleet.go:67
Limit: atoiDefault(..., defaultFleetLimit)
// fleet.go:79-92
recs, err := s.records.Query(ctx, q)
view.TotalEvents = len(recs)
```

Every backend honors `Limit` by stopping early — Memory breaks the loop at `q.Limit` (`eventstore.go:170`), File does
the same (`:273`), SQLite appends `LIMIT ?` (`eventstore_sqlite.go:205`). So `recs` is the newest N records overall,
and `buildFleetView` walks exactly that slice: it discovers hosts from `rec.HostID` as it goes, and every per-host
number (`Total`, `BySeverity`, `ByTechnique`, `BySource`, `ByPack`, `MaxSeverity`, `LastSeen`) is computed from the
sample. `fleetpage.go:64` renders the same view through `Recent(ctx, "", defaultFleetLimit)` — same cap, same page.

## What that does once the store passes the cap

- **A host with no event in the newest 5000 does not appear at all.** Not shown at zero — absent. From a page titled
  *fleet*.
- **`TotalEvents` is 5000**, sitting next to the host list. A reader takes it for how many events the fleet has. It is
  how many the query was allowed to read.
- **Per-host totals shrink as other hosts get noisier**, because the window slides. The same host, same week, quieter
  neighbours, different numbers.

**The numbers themselves are correct.** They describe the 5000 records read, faithfully. The defect is narrower and
worse: **nothing on the view says the view is a sample.** No `Truncated`, no `LimitApplied`, no `Sampled`, no
`Scanned` — the reader has no way to know they are looking at a quota rather than at the fleet.

That is the same family as beats 730 and 731, one more step out: 730 was an unread file reported as deleted; 731 was a
last-alert time displayed as freshness; **733 is a bounded query displayed as a census.** Each time, a limit the system
knows about is not carried to the place a human reads the answer.

## Reachability

The cap is 5000 and the store is retention-bounded, so this needs a store that actually holds more than 5000 records —
a noisy host under a long window, or a pentest against one rule, gets there. **I have not measured a production store
and am not claiming a body count** — the claim is the mechanism, not that it is currently tripped on anyone's box.

## The second half: an ordering guarantee held by a comment

```go
// fleet.go:126   // Records are newest-first, so the first seen is the latest.
if h.LastSeen.IsZero() || rec.When.After(h.LastSeen) { h.LastSeen = rec.When }
```

`LastSeen` is correct today because all three backends happen to scan newest-first. The helper written for the case
where a backend does *not* guarantee that — `SortRecordsNewestFirst`, whose own comment says *"a future SQL backend
could return any order"* — has **zero callers** (beat 725, unchanged).

Worth noting plainly: even if the ordering broke, `LastSeen` would still be right, because the `After` comparison
takes the max regardless of order. **So the comment is protecting a field that doesn't need protecting.** The actual
order-dependent things are `Recent` (`if len(h.Recent) < recentN` keeps the *first* N seen — a non-newest-first
backend would put arbitrary records in "Recent") and the early `break` in the store scans. That is the fragile part,
and the comment is attached to the robust part.

## Options — listed, not picked

1. **Surface it**: add `Truncated bool` / `Scanned int` / `StoreTotal int` to `fleetView` and show a banner on the page
   when the sample is capped. Cheapest honest fix; makes the quota visible without changing any number.
2. **Count hosts from state, not from records** — requires a per-host row that exists whether or not it has events,
   which is beat 731's `LastIngest` shaped problem again. Bigger.
3. **Raise the cap or page the view** — treats the symptom; the cap exists for a reason and any cap has the same
   unlabelled-sample problem one order of magnitude later.
4. **Move the ordering comment onto `Recent`** where the assumption is actually load-bearing, and call
   `SortRecordsNewestFirst` on the slice `buildFleetView` receives. Two-line change, fixes the real fragility rather
   than the comment's current target.

## What I am NOT claiming

- **Not** that any number in the view is computed incorrectly.
- **Not observed** — no run, no store over 5000, no toolchain.
- **Not** that 5000 is the wrong cap. Whether the page should scan more is a cost decision belonging to whoever runs
  the fleet.
- **Not** that a host has ever been missed in practice — the mechanism is reachable, the incident is hypothetical.

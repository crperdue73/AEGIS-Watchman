# FINDING 2026-11-03t — the roster is cheap; the counts are what's expensive

**Status:** COSTED, NOT APPLIED — wire-visible, Dad's call. Supersedes the options list in `2026-11-03s`.
**Instrument:** `internal/central/fleet_test.go` and `internal/central/fleetsample_test.go` — these pin the
per-host aggregates (`Total`, `BySeverity`, `ByTechnique`, `BySource`, `ByPack`) that a top-K-only rewrite
would silently break, and they are what constrains the proposal below.

The measurements themselves came from a throwaway `-tags sqlite` probe (`TestProbeRosterQuery`), printed and
deleted after use; every number quoted here is its output. **No permanent guard was added this beat** —
this finding proposes a change, so it ships a measurement plus the tests that would bound it, not a check.
**Follows:** `2026-11-03s` (absent host), `2026-11-03r` (ranking), `2026-11-03q` (count), `2026-11-01n` (cap).

## Why I measured instead of waiting

Beat 792 ended with "the root-cause choice is Dad's." That is true, but it left the question *uncosted*,
and an uncosted question sits. Four beats went by on `2026-11-01n` while I wrote up options instead of
pricing them (beat 790's own lesson: "when a finding lists options, the next beat's job is to pick one").
So this beat did not add an option — it put a number on the one I'd argue for.

## The measurements

Store: SQLite, 21 hosts (20 × 500 events + 3 critical records on a quiet host), 10,003 records total.

| question | measured |
|---|---|
| how many hosts does the global cap show? | **10 of 21** — eleven invisible, including the quiet critical host |
| cost of `SELECT host_id, MAX(when_unix), COUNT(*) ... GROUP BY host_id` | **2.9 ms**, returns all 21 |
| cost of one per-host fetch | **156 µs** |
| does a supporting index exist? | yes — `idx_records_host_when (host_id, when_unix)` |

So the roster is **not** the expensive part. It is one aggregate bounded by *fleet size* (how many hosts
you run), not by *event count* (how busy they were), plus N index seeks. Both options I listed at beat 792
assumed the roster was the cost. It isn't.

## The catch I found while costing it — this is the actual content

A per-host top-K fetch gives you host **membership** while silently converting every **aggregate** into a
top-K statistic. `buildFleetView` computes all of this from the `recs` slice:

```
Total, MaxSeverity, LastSeen, BySeverity, ByTechnique, BySource, ByPack
```

Fetch 3 recent records per host and `"total": 3` becomes a claim about the top 3, not about the host.
Every row on the page would carry a wrong number, and nothing would go red — the suite asserts what it
asserts, and the values would still be *internally consistent*.

**That would trade an absent-row bug for a silently-wrong-number bug.** The absent row is visible: an
operator notices a host that isn't there. A plausible wrong count is not visible at all. I nearly shipped
the strictly worse failure while fixing the better-known one.

## The correct shape, and why it's cheap

The roster aggregate supplies the three aggregates that actually matter, as **true** values:

- `COUNT(*)` → true `Total`
- `MAX(severity_rank)` → true `MaxSeverity`
- `MAX(when_unix)` → true `LastSeen`

The four `By*` maps need grouped queries — `GROUP BY host_id, severity`, `... , technique`, etc. — still
bounded by hosts × distinct values, not by event count.

`Recent` comes from the per-host top-K, and **`Recent` is already labelled a sample** (beat 790). That is
the one field where a sample is honest, because it is a list of the latest things that happened, and that
is exactly what it is.

```
true membership (roster)  +  true aggregates (roster/grouped)  +  explicitly-sampled Recent (top-K)
```

## Cost, stated as a proposal

- one aggregate over `host_id` — **2.9 ms measured**
- ~4 grouped aggregates for the `By*` maps — same order
- N index seeks for `Recent` — **156 µs each measured**

Against a global 5000-row fetch that currently drops 11 of 21 hosts. This is not a schema migration and it
is not an index addition; the index is already there.

## The parts that are wire-visible, i.e. Dad's call

1. **`TotalEvents` changes meaning.** Today it is `len(recs)` — the window. Under this design it becomes
   the store's real count. That is a better number and a *different* number. `0.2.0` is public.
2. **`RecordStore` grows a method** (`Aggregate(ctx, RecordQuery) ([]HostAgg, error)` or similar), which
   both backends must implement. The interface documents that `Recent` is "defined in terms of Query so
   every backend serves the common case through the same path" — an aggregate is the first thing that
   *cannot* be served that way (it needs `GROUP BY`, which `Query` does not express), so the interface's
   stated invariant stops holding at exactly this point.
3. **`buildFleetView` splits** into `buildFleetViewFromAggregates(aggs, recents)` with the current
   `buildFleetView(recs, n)` retained for the in-memory/test path, or replaced entirely.

## What I did NOT do, deliberately

I did not ship a roster-only patch. Leaving `buildFleetView` aggregating over `recs` while adding the
roster would put every host on the page with **wrong counts on every row** — worse than today's absent
row, because absence is visible and a wrong number is not. A half-fix here is not half a fix; it is a new
defect wearing the shape of the old one's remedy.

## What this does NOT claim

- **Not that the redesign is scheduled.** It is costed and awaiting Dad.
- **Not that 2.9 ms is a production number.** It is a 21-host probe on a dev box; a 500-host fleet with
  many distinct techniques is a different shape. The *scaling argument* is the claim (bounded by fleet
  size, not event count), not the millisecond.
- **No production code changed this beat**, so the beat-792 disclosure remains the current state of the
  console.

## Lesson

Beat 792 ended by calling the fix "Dad's call," which was accurate and also a way to stop. The next
question — *what does it cost?* — was mine to answer and I hadn't. **An uncosted decision looks like
someone else's decision.**

And costing it found the thing that mattered more than the option list: the cheap-looking fix that
preserves membership while corrupting every count, which no test I have would have caught because every
number would still be self-consistent.

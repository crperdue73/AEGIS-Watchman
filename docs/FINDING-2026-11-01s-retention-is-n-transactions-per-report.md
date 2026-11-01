# FINDING — 2026-11-01s: retention is N transactions per report, and the storm multiplies the cost it exists to bound

**Status:** source reads only. **NOT observed** — no toolchain (beat 642), and no benchmark. This is arithmetic on
counts read from the source, and I mark every number as a reading rather than a measurement.

**Instrument:** `scripts/retainpathcensus.py`. **NOT APPLIED** — the fix is a write-path change under load (Dad's call,
beat 724).

**One line:** `retain` calls `Append` **once per event**, and on SQLite each `Append` is three commits and a full
`DELETE … NOT IN (SELECT …)` — so one collector report of a few hundred drift events becomes a few hundred transactions
plus a few hundred full-table trim scans, and **the storm the cap exists to survive is exactly what makes it expensive.**

## Where the caller is, finally

Five beats inside the store and I never once read what calls it. Here it is:

```go
// internal/central/service.go:82-93
func (s *Service) retain(host schema.Host, agent schema.Agent, events []schema.Event) {
    ...
    when := s.nowTime()
    for _, ev := range events {
        rec := RecordFromEvent(host, agent, ev, when)
        if err := s.records.Append(context.Background(), rec); err != nil && s.logger != nil {
            s.logger.Printf("central: record store append failed: %v", err)
        }
    }
}
```

Called from exactly two places — `Ingest` (`:160`) and `IngestActions` (`:205`) — under the "Best-effort" comment.

## What's correct, and it's most of it

- `when := s.nowTime()` is **hoisted out of the loop**, so every event in one report shares one timestamp. That is
  deliberate and it is *why* one report sorts correctly: `Record.When` is constant across the batch. **It also
  quietly confirms beat 735** — the cap's trim-by-arrival is safe today because within a report there is nothing to
  mis-order.
- The context is `context.Background()`, not the request's. Correct: the record write must not be cancelled because
  the collector hung up mid-report.
- Failures are logged, never fatal, and the reason is written down and right — the baseline is already durable and the
  alert already fired; a lost history row must not fail an ingest.
- `RecordStore` is a one-method interface (`:99-101`). The single-method shape is honest about this being an
  append-only history.

## The multiplier

Counts read from source, not timed:

| Quantity | Reading |
|---|---|
| `retain` call sites | 2 (`Ingest`, `IngestActions`) — exhaustive |
| `Append` per event | 1 (`service.go:89`) |
| Statements per `Append` (SQLite) | **3** — INSERT, trim, and the window pass runs inside the same `prune` |
| Trim scans per report | **N**, where N = events in the report |
| Default collector report interval | 30s (`config.go:143`) |

```go
// eventstore_sqlite.go:142-150
_, err := s.db.ExecContext(ctx, ins, ...)   // 1 commit
if err != nil { return err }
return s.prune(ctx)                          // 2 more commits
```

**So: events-per-report × 3 statements, and events-per-report × 2 full `DELETE … NOT IN` table scans — per collector,
per report.** A drift storm (a package upgrade touching a few thousand files) is precisely the case where N is large,
and it is precisely the case the `MaxRecords` cap exists to survive. **Beat 735's refrain for the fifth time: the
assumption is weakest exactly where the guard is most needed.**

Nothing here is a correctness bug. Every row lands, the cap holds, and a failed prune logs. It is a **cost multiplier
in the code path that runs during an incident**, and it is invisible because it's a `for` loop over a slice — the
cheapest-looking thing in the file.

## One thing I checked and could NOT confirm either way

`Ingest` (`:160`) and `IngestActions` (`:205`) both call `retain` → `Append` on the same store. Whether those two paths
can run **concurrently** for the same host depends on the runner in `cmd/central` (a ticker? a manual shell?), which I
have not read. **Not claimed.** If they can, beat 737's interleaving is not theoretical — it's the normal state during
a storm, and the cap's idempotency is doing more work than anyone wrote down.

## Options — listed, not picked

1. **Batch append.** Add `AppendBatch(ctx, recs []Record) error` to `RecordStore`; SQLite wraps it in one transaction.
   Turns N transactions into 1 and N trim scans into 1. Also **fixes beat 737's three-commit split in the same change**,
   because the batch is naturally one transaction. Biggest win; changes the interface and the write path.
2. **Prune once per report.** Keep per-event `Append` but skip pruning until the end of the loop — one trim per report
   instead of N, and the over-limit window becomes a report's worth of rows rather than one. Small, local, and it
   concedes the `MaxRecords+1` transient grows to `MaxRecords+N`.
3. **Trim every K appends** — bounded cost with an approximate cap. Wrong direction for a storm.
4. **Document the cost.** One sentence next to the loop: "one Append per event; SQLite incurs three commits and a trim
   scan each." Cheapest honest step. Fixes nothing, but stops the next reader from assuming it's one write.

## What I am NOT claiming

- **Not** that this is slow. I have not timed it, and I refuse to put a plausible-sounding number next to it (Fern's
  rule, 7300). The claim is the *shape* — N transactions and N scans per report — not a rate.
- **Not** that any production deployment hits it. Phase 2 isn't deployed; this is discovery.
- **Not** that `Ingest` and `IngestActions` run concurrently — I named that as an unread question, not a finding.
- **Not** that batching is safe to just do: it changes error semantics from "this record failed" to "this batch failed,"
  which is a contract change worth its own beat.

# FINDING — 2026-11-02d: a failing record store is logged 250 times and reported as success

**Status:** source reads **+ executed measurement**. **NOT observed in a deployment. NOT APPLIED.**
**Instrument:** `scripts/retainfailcensus.py`.
**Origin:** Callie's 7406 — she moved the shell, not the resolve: *"what changed tonight isn't my resolve, it's the
shell."* Same lens on `retain()`: the thing that changed is a log line, and the shell around it still says fine.

## The claim

`Service.retain` (`service.go:82-93`) appends the cycle's events to the record store **one at a time**, and on error:

```go
if err := s.records.Append(context.Background(), rec); err != nil && s.logger != nil {
    s.logger.Printf("central: record store append failed: %v", err)
}
```

Two properties follow, both measured below.

1. **No error escapes.** `retain` returns nothing; `Ingest` (`:157-164`) then alerts and returns
   `&IngestResult{...}, nil`. A store that is disk-full, locked, or corrupt produces a **200 to the collector** and a
   baseline that is committed. The collector believes it reported.
2. **The log grows with the event count.** One line per failed append. 250 events → 250 identical lines.

And the retention is not decorative: it is what the fleet view and the query API read (beat 740/733/738). Losing it
means the fleet page silently shows a stale or empty history while every status code says healthy.

## Measured

Real `Service.Ingest` with a record store whose `Append` always fails (`database or disk is full`):

```
RESULT events=250 append_attempts=250 log_lines=250 ingest_err=<nil>
```

Every event attempted, every attempt failed, **ingest returned nil**. The 250 log lines are not a detail — at fleet
scale (beat 745 measured 1,000-event cycles) a full disk produces **1,000 log lines per cycle, every cycle**, which is
a log flood that buries the one fact worth seeing: *the store is down and nothing is being recorded.*

## Sound parts, checked before writing this up

- **Drift events still reach the collector.** `IngestResult.Events` is returned regardless, and the doc comment at
  `:155-158` says so deliberately: *"Delivery failures are logged, never fatal: the collector still gets its events,
  and the store is already correct."* For the **alert** path that reasoning holds — the baseline was committed first,
  so a failed notification loses nothing that can't be re-derived from the next walk.
- **The same reasoning does not transfer to the record store**, and that is the whole finding. A failed alert is
  recoverable; a failed *retention* is not — those records are only ever produced once, from that cycle's events. The
  comment claims a guarantee ("the store is already correct") that belongs to the baseline write and gets silently
  extended over a subsystem where it is false.
- **`resolveActors` before `retain`** (`:160-161`) is correctly ordered — the stored record carries the resolved uid
  username. No defect there.
- **The alert still fires** when retention fails. Detection is not lost. Only history is.

## Why this is not the same as beat 745

745 was *delivery* — the alert hitting a receiver. This is *persistence* — the events never landing anywhere local.
Different failure, different recovery: a dropped webhook can be re-read from the store; a dropped record cannot be
re-read from anywhere. Both share the shape **"guard at the cheap boundary, silence where it matters"**, but fixing
745's cap does nothing for this.

## Latent trap found alongside, not filed as a bug

`s.alert` (`:171`) and `retain` (`:89`) both guard with `err != nil && s.logger != nil`. **With no logger installed, a
delivery or retention failure is completely silent.** `cmd/central/main.go:56` always calls `SetLogger(log.Default())`,
so this is **unreachable in the shipped binary** — hence not a bug. But `NewService` defaults `logger` to nil
(`:46-48`), so *any* future embedding of the package without a logger inherits silent data loss. Recorded, not fixed.
(The package's own tests all set a logger or use a nil alerter, so nothing in the suite would catch a regression here.)

## Options — listed, not picked

1. **Return the failure from `Ingest`** (or a typed `IngestResult.RetainError`) so the collector learns its report was
   not recorded. Cleanest; changes the wire contract and the collector's retry story ⇒ **Dad's call**.
2. **Log once per cycle, not once per event** — `%d events not retained` with the first error. Cheap, keeps history
   honest in the log, changes no contract.
3. **Surface it on the fleet/status page** — "records failing since T" is a fact an operator needs and currently can
   only obtain by grepping a log.
4. **Leave ingest non-fatal, but make the failure visible in state** (a counter/flag the query API exposes). Preserves
   the deliberate "never fail the collector" design while removing the silence.

## What I am NOT claiming

- **Not** that a deployment is currently losing records. Constructed store, not a running fleet.
- **Not** that `retain` should fail ingest. The non-fatal choice is defensible and documented; the defects are the
  *volume* of the notification and the *false* reach of the "store is already correct" claim.
- **Not** that the alert path is broken — it fires correctly in the same test.
- **Not** measured under a real disk-full condition or a locked SQLite file; the failure was injected at the
  `Append` boundary, which is the same boundary the real errors cross.

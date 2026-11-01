# FINDING — 2026-11-01r: the SQLite trim keeps MaxRecords+1, and the write is three races

**Status:** source reads. **NOT CLAIMED as observed** — no toolchain (beat 642), and the races are reasoned from
SQLite's documented isolation, not watched. **NOT APPLIED.** **Instrument:** `scripts/sqlitetrimcensus.py`.

**One line:** `prune`'s cap delete is `DELETE ... WHERE id NOT IN (SELECT ... LIMIT ?)`, which runs *after* the row
is already inserted — so the store holds **MaxRecords+1** rows between statements, and every `Append` is three
separate autocommit transactions racing three other connections.

## What is genuinely good here, first

The header comment says SQLite "fixes all three" of the JSON FileStore's problems — append is WAL-cheap, it locks across
processes, and it is indexed. **I checked, and all three hold:**

- `idx_records_host_when ON records (host_id, when_unix)` (`:86`) — `Query`'s host+time filter is a range scan.
- `idx_records_sev ON records (severity_rank)` (`:88`).
- `journal_mode(WAL)` + `busy_timeout(5000)` in the DSN (`:47`) — real cross-process locking, unlike the JSON file.
- `modernc.org/sqlite` is **pure Go**, so no CGO, so "clone and build" works without a compiler. The stated reason is
  the actual reason.

That header is the best-written comment I have read in this tree. It states its claims and every one checks out.

## The cap is an off-by-one between statements

```go
func (s *SQLiteRecordStore) Append(ctx context.Context, rec Record) error {
    _, err := s.db.ExecContext(ctx, ins, ...)   // statement 1: INSERT, commits
    if err != nil { return ... }
    return s.prune(ctx)                          // statements 2 and 3, separate commits
}

const trim = `DELETE FROM records WHERE id NOT IN (
  SELECT id FROM records ORDER BY when_unix DESC, id DESC LIMIT ?
)`                                               // LIMIT = MaxRecords
```

`NOT IN (… LIMIT MaxRecords)` deletes every row **not among the newest MaxRecords** — it runs *after* the INSERT has
already committed, so at that instant there are `MaxRecords+1` rows and it deletes exactly one. The steady state is
therefore **MaxRecords**, reaching **MaxRecords+1** for the window between the two statements. Not a leak — it is
bounded — but "the store holds at most MaxRecords" is true only once pruning returns.

The Memory and File backends append and trim **inside one mutex-held call**, so they have no such window. Three
backends, three different notions of "when the cap applies."

## The bigger shape: one logical write, three transactions

`db.SetMaxOpenConns(4)` (`:56`) — *"a small pool avoids lock churn while still letting readers run concurrently under
WAL."* Under WAL, an autocommit statement takes the write lock for itself and releases it. With four connections and no
`BEGIN`:

1. **Two writers can interleave.** Writer A inserts, writer B inserts, A prunes, B prunes. Both prunes see both rows,
   both compute a trim to MaxRecords, both execute. The end state is still MaxRecords — the cap is **idempotent**, which
   is why this is survivable — but the work is doubled and the `NOT IN` subquery is evaluated against a table the other
   writer is mid-prune on.
2. **A failed prune leaves the row.** `Append` returns the error, but statement 1 already committed. The caller sees a
   failure and the record **is in the store** — the opposite of what a returned error implies. The Memory/File backends
   cannot do this: their prune and append share a lock and the failure paths are pure in-memory.
3. **The window pass and the cap pass are separately committed**, so a reader can observe the state between them —
   window enforced, cap not yet.

None of these corrupt data. All three mean `Append` does not behave as one write, and only the SQLite backend has that
property.

## Why this is worth a beat rather than a shrug

Same family as every finding since 730, and named precisely this time: **the guarantee is real at the boundary the
caller sees and loose inside.** `Append` returns nil only when everything succeeded *in a normal run*; a trim failure
returns an error for a record that was written. The store is correct when nothing goes wrong and its contract is
subtly wrong when something does — which is the state the tool exists to be in during an incident.

Fern's line (7375) is the standard I would hold this to: *"a fix that isn't in the record as a defect is a fix
somebody's going to have to find again."* The header comment documents the three wins and does not document this, so
this is the record.

## Options — listed, not picked

1. **Wrap Append in one transaction** — `BEGIN IMMEDIATE`, INSERT, both prunes, `COMMIT`. Fixes the error semantics
   (2), the interleaving (1), and the intermediate observation (3) together. `BEGIN IMMEDIATE` also takes the write lock
   up front, which is what `busy_timeout` is already there to absorb. Costs: one transaction per event instead of three
   statements; the busy timeout matters more.
2. **Over-limit target** — `LIMIT MaxRecords` → keep, but prune *before* insert, or target `MaxRecords-1`. Treats the
   off-by-one only; leaves the three-race shape.
3. **Prune on a timer, not per write** — batches the cost, but makes the store exceed the cap between sweeps. Wrong
   direction for a storm, which is what the cap is for.
4. **Document it** — say in the header that Append is not atomic and that the cap binds only on a completed prune.
   Cheapest honest step; leaves the error semantics wrong.

**The transaction change is a write-path change under load, which is Dad's call** (beat 724). Not applying it.

## What I am NOT claiming

- **Not** that data is lost or corrupted — the cap is idempotent and the window delete is indexed and unconditional.
- **Not observed** — I have not run this, and I am not claiming a specific interleaving has occurred.
- **Not** that SQLite is worse than the JSON file. It is better on every axis the header claims; that is why it is the
  default in the P2 docs.
- **Not** that `MaxRecords+1` matters operationally. It is one row. It is recorded because "at most N" is a promise
  three backends make and only two keep continuously.

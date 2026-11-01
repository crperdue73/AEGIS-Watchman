# FINDING — 2026-11-01p: the record cap trims by arrival order, its comment claims by time

**Status:** source reads. **NOT CLAIMED as live** — measured as latent (below). **NOT APPLIED.**
**Instrument:** `scripts/capordercensus.py`.

**One line:** `RecordRetention`'s "both are needed" claim is **true** — window and cap are both enforced on all three
backends. The gap is one layer in: **the cap decides what is "oldest" by slice position (arrival), not by `When`
(time)**, on the two backends that have a slice at all.

## First, the claim I came to test survives

```go
// eventstore.go:110-113
// RecordRetention bounds the store: records older than this are dropped on write, and no
// more than MaxRecords are kept. Both are needed — a time window keeps the view "recent"
// and a hard cap bounds memory/disk under a high-severity storm.
```

Memory (`pruneLocked` :184-200), File (`:288-298`) and SQLite (`prune` :159-178) all enforce both. Nothing here is
documentation-only. That was the question I closed beat 734 on, and the answer is clean.

## The gap is what "oldest" means

- **Window pass** — all three compare `rec.When` against a cutoff (`r.When.After(cutoff)`). Order-independent. Correct
  everywhere.
- **Cap pass** — Memory and File do:
  ```go
  m.recs = m.recs[len(m.recs)-m.retain.MaxRecords:]
  ```
  That keeps the **last N elements of the slice**. It is positional. It sorts nothing and compares nothing.
- **SQLite** does it by time: `DELETE ... WHERE id NOT IN (SELECT id FROM records ORDER BY when_unix DESC, id DESC
  LIMIT ?)`.

So the durable backend is right *structurally* — it has no arrival order to confuse with time order. The two slice
backends rest on a comment (`:128` `recs []Record // append order (oldest first)`), and `pruneLocked` says so out
loud:

> *"records are roughly sorted by append time; a mis-ordered clock only costs a stale record surviving, never data
> loss."*

**That sentence is true for the window pass and false for the cap pass directly below it.** The window filters by
`When`, so order genuinely can't hurt it. The cap slices by position, so when arrival diverges from time, it deletes
by the wrong key — and the failure is not "a stale record survives," it is **a recent record deleted because it
arrived late, while a stale one that arrived early is kept.** Exactly inverted.

## Why this is latent, and I am not going to dress it up

`retain` (`service.go:82-93`) stamps `When` from `s.nowTime()` **at ingest**:

```go
when := s.nowTime()
for _, ev := range events {
    rec := RecordFromEvent(host, agent, ev, when)
```

So in production append order *is* time order, by construction, and the positional cap is correct today. `Record.When`
is a plain exported field and `Append` validates nothing — no sort, no monotonicity check, no error — so the
correctness comes from a property established two files away, not from the store.

It goes live the day anything backdates a record: a replay/import path, a clock correction applied after ingest, or an
"ingest at event time" change. None of those exist now. **Latent, measured, not claimed as a bug you can hit today.**

## The part that makes it worth writing down

The cap exists for a severity storm. A storm is exactly when many records arrive fast — and a fast-arriving batch is
where divergence between arrival and time is most likely, and where the cap fires most often. **The cap's ordering
assumption is weakest precisely in the condition it was built for.** Same shape as 730 (silence→deletion), 731
(alarm→freshness), 733 (quota→census): a guard whose precondition is least reliable when the guard is most needed.

## Options — listed, not picked

1. **Sort before trimming** in the cap pass, or trim by scanning for the min `When`. Correct regardless of ingest
   order; costs an O(n log n) or O(n) pass on the write path at the moment the cap trips. Cheap and local.
2. **Guard on append** — reject or log when `Append` receives a `When` earlier than the newest already stored. Turns
   the latent assumption into a detected event; does not fix the cap.
3. **Make the store order-insensitive by construction** — SQLite already is; converting the slice backends to keep a
   sorted insert would close it but changes the write path in the hot loop.
4. **Fix the comment** to say the cap assumes monotone ingest, and name what breaks it. Zero risk, and it is the
   honest minimum: the code is correct today, and the sentence describing it is scoped to the wrong pass.

## What I am NOT claiming

- **Not** that records are being dropped incorrectly today — by construction they are not.
- **Not** that the window half is broken or unenforced — it is enforced on all three backends.
- **Not observed** on a running store (no toolchain, beat 642), and **not reachable** via any current code path.
- **Not** that SQLite has the same defect — it does not, and that is the strongest evidence the positional version is
  the accidental one.

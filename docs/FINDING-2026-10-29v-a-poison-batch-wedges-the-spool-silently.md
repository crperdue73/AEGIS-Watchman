# FINDING 2026-10-29v — the spool bound is a floor of one batch, a poison batch wedges the queue, and the wedge reports success

**Status:** Instrument added (`scripts/trimfloor.py`). Static analysis; no Go, no running collector.
**Instrument:** `scripts/trimfloor.py` · **Beat:** 671 · **Follows:** 670 (failed Append discards), 661/662 (1 MiB), 660 (413 only)

## Why this question follows beat 670

Beat 670 found that a failed spool `Append` discards the batch, and I proposed a retry buffer. **A retry buffer is only
worth building if the spool is a place that holds things.** So this beat I measured the spool's own bound: what is the
largest single item it will keep, and can central accept that item?

## Measured — the tree legs

**(1) The trim loop cannot drop the last record.** `Spool.trim` (spool.go:235-254):

```go
for len(recs) > 1 {
    size := 0
    for _, r := range recs { ... }
    if int64(size) <= s.maxBytes { break }
    recs = recs[1:]
}
log.Printf("collector: spool over %d bytes; trimming to %d batches", s.maxBytes, len(recs))
return s.rewrite(recs)
```

`len(recs) > 1` is the floor. **The last record is never dropped, whatever its size.** `maxBytes` bounds *the pile*, not
*any item in the pile*. With one oversized batch, trim rewrites the file unchanged, logs "trimming to 1 batches", and
returns **success**.

**(2) The numbers do not meet.** `DefaultSpoolMaxBytes = 16 << 20` (spool.go:41) — **16 MiB**. Central's batch limit is
**1 MiB** (beat 661, measured). Largest single record trim will keep ≈ 16 MiB = **16x** what central accepts.

**(3) Drain stops at the first failure — correct for outages, fatal for poison.** `Drain` (spool.go:100-138) `break`s on
the first submit error and rewrites `recs[idx:]`, retaining the failed record *and everything after it*. Beat 659
established this is exactly right for an unreachable central. But beat 660 measured that central's **only reachable
rejection is 413 on a >1 MiB batch** — a *permanent, deterministic* verdict on contents, with no transient 5xx path.

## The shape of the failure

An oversized batch at the front of the spool blocks every batch behind it, forever. The host keeps polling, matching,
appending and trimming — **it looks entirely healthy** — while central receives nothing from it.

## The worse thing found on the way: the wedge reports success

`Drain` ends with:

```go
if shipped == 0 {
    return 0, nil
}
```

When the **first** record fails, nothing shipped, so Drain returns **`nil` error**. The only caller (`BehaviorEngine.drain`,
behavior.go:443-449) logs the failure **only when `err != nil`**. So a spool wedged on its first record produces *no log
line at all* at the drain layer. The `log.Printf("...drain paused at %d/%d...")` inside the loop is the only trace, and it
is phrased as a pause — which is what it is for an outage, and a lie for a poison record.

That is beat 667's pattern again, one layer down: **the thing that would report the problem is the thing that is
returning success.**

## Credit where it is earned

- Trim's drop direction is right: `recs = recs[1:]` drops the **oldest**, not the newest.
- `rewrite` is crash-safe: tmp file → flush → `Sync` → `Close` → `Rename`, every error returned.
- `Drain` retaining `recs[idx:]` rather than discarding the failed batch is correct engineering.
- Beat 659's finding stands: a *stopped central* is bounded, self-healing loss. This is a different failure — a central
  that is **up and answering**.

## Claim / not claimed

**Claimed:** the trim floor is `len(recs) > 1` so the last record is never dropped; `DefaultSpoolMaxBytes` is 16 MiB vs a
1 MiB central limit; a single record can exceed central's limit and is retained and shipped whole; Drain stops at the
first failure; Drain returns `nil` when it ships nothing; the only caller logs only on a non-nil error.

**Not claimed:** that this is happening anywhere now, or that 16 MiB is easy to reach in one poll. Beat 661 already
measured `schema.Event` at 416 bytes → 1 MiB ≈ 2520 matches in one record; that is the **runaway-agent** case, not the
every-day case, and I am not going to inflate it into one. **Not claimed:** the exact serialized size of a real spool
record — I did not run the marshaler. The **16x is arithmetic on two configured constants**, not a measurement of a file.

## Fix (Dad's call — and the order matters)

1. **Split a spool record at `SubmitActions` time when it exceeds the central limit.** (Beat 662 already proposed this
   for alerts.)
2. **Treat 413 as a permanent content verdict** — quarantine or drop that record, **count it**, continue. One bad batch
   must not stop the queue. (Beat 660's rule.)
3. **Return an error when Drain makes no progress** against a record it already failed on before, so the wedge is loud.

Order matters: splitting without (2) still stalls on any non-413 rejection; (2) without splitting throws away recoverable
events. **Do both, or neither.** Only after (2) is trim's floor safe — a record that cannot ship stops being a reason to
stall everything behind it.

## Mutation proof

| mutation | result |
|---|---|
| trim floor `len(recs) > 1` → `> 0` | **FAIL**, "trim cannot drop the last record" · rc=1 |
| `return 0, nil` → `return 0, err` when shipped==0 (**proposed fix 3**) | **FAIL** ×2, "wedged drain reports no error" · rc=1 |
| control | 12 rows green · rc=0 |

Also caught mid-work: my first Drain assertion was an inline `A and B if C else D` chain — the same `A and B or C`
precedence trap as beat 667 — so it returned **FAIL** on correct code. Split into independent assertions, then it was
green for the right reason, and then mutant 2 made it red for the right reason too.

## Gate

**84 suites / 1640 checks / 0 failures.** Baseline rewritten (`trimfloor.py 12`).

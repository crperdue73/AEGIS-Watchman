# FINDING 2026-10-29u — "at-least-once by design" is true until the disk write fails, and then the batch is gone

**Status:** Instrument added (`scripts/durabilitygap.py`). Static analysis; no Go, no running collector.
**Instrument:** `scripts/durabilitygap.py` · **Beat:** 670 · **Follows:** 668 (split record lost), 669 (wire can't report loss)

## I went looking for a guarantee to test, not another hole

Beats 668 and 669 ended somewhere grim — an event can be lost before the spool, and the wire has no field to report it.
So this beat I stopped hunting holes and looked for a **claim** worth auditing. `docs/R2-EBPF-DESIGN.md:55`, in D1's
rationale:

> "matches are idempotent by rule id + action, and **the spool is at-least-once by design**."

## Credit first: most of that claim is genuinely earned

- `Spool.Append` (spool.go:60-85) marshals, writes, and **checks `f.Sync()`** — a real `fsync`, error returned, not a
  buffered write that claims success.
- `Append` propagates write errors rather than swallowing them.
- `Drain` stops at the first submit failure, so a central being down does not consume the backlog.

Beat 659 already measured that the *stopped-central* case is bounded, self-healing loss. None of that is disputed here.
**The spool is fine.** The finding is about the caller's failure path, and it would be wrong to state it as "the spool is
not durable."

## The hole: the branch that handles a failed write

`BehaviorEngine.Poll` (behavior.go:406-436), read literally:

```go
if b.spool.Enabled() {
    if err := b.spool.Append(b.host, b.agent, events); err != nil {
        // A spool failure is loud: it means the durability guarantee is broken.
        log.Printf("collector: spool append (%d events): %v", len(events), err)
    }
} else if b.submitter != nil {
    ... best-effort submit, error logged ...
}
b.drain()
```

If `Append` fails, the events are **not on disk**. The only action taken is `log.Printf`. `Poll` keeps no copy, does not
requeue, does not retry — and `drain()` reads **only the spool**, which by definition does not contain this batch.

The comment names the consequence exactly right — "the durability guarantee is broken" — and then the code proceeds as
though it were not. That is the finding in one sentence: **the code knows, writes it down, and discards the data.**

Verified there is no second chance: `BehaviorEngine`'s struct body is exactly
`eng, submitter, host, agent, timeline, spool, watch` — **no pending, retry, backlog, or buffer field.**

## Why this is the ordinary case, not an edge

`Append` returns an error for mkdir, open, marshal, write, **and sync**. Sync is the one that fails in production:
**ENOSPC, EIO, EDQUOT, or a read-only remount**. A full disk is the ordinary end-state of a chatty host — and it is
precisely the condition under which that host's behaviour is most interesting. So the detector's durable path
degrades to silence under exactly the pressure the detector exists to observe.

The asymmetry is what makes it a design defect rather than a bug: the `else` branch documents that shipping *without*
durability is "best-effort"; the spool branch — the one that is supposed to be the durable one — degrades just as
easily, with a log line and nothing else.

## Claim / not claimed

**Claimed:** `Append` checks `Sync`; the spool-error branch consists solely of a log call; `Poll` retains no copy of
`events`; `drain` reads only the spool; `BehaviorEngine` has no retry buffer; a failed `Sync` loses the batch
permanently.

**Not claimed:** that `Append` fails often, or that I observed it — no toolchain and no running collector, this is read
from source. **Not claimed: that beat 659 was wrong.** The spool *is* at-least-once for events that reach it. This claim
is narrower and stacks on top: **events that fail to reach the spool are at-most-once, and nothing counts them.** That
distinction is the whole finding, so it must not be restated as "the spool is not durable."

## Fix (Dad's call)

1. On `Append` error, keep the batch in a bounded in-memory `pending` buffer on `BehaviorEngine` and retry it at the
   **next Poll, ahead of new events**.
2. Bound it explicitly — drop oldest past a cap **and count the drops**, so the cap is a number an operator can read
   rather than a silence. (Same rule as beat 662: a cap with no counter is just a nicer silence.)
3. While the spool is unwritable, ship `pending` best-effort. A degraded copy beats a discarded one.

## Mutation proof

| mutation | result |
|---|---|
| `pending []schema.Event` on `BehaviorEngine` (**proposed fix**) | **FAIL**, "no pending/retry/backlog field" · rc=1 |
| `f.Sync()` error ignored (`_ = err`) | **FAIL**, "Append fails on write OR sync" · rc=1 |
| control | 10 rows green · rc=0 |

**The first mutant didn't apply** — my anchor was `\tspool   *Spool` but the struct is aligned as `\tspool     *Spool`,
so `assert count == 1` came back **0** and the run *passed*. Had I not asserted the count, a no-op edit and a proven
mutation would have looked identical, and I would have written "mutation-proven" about a file I never changed. Caught,
re-applied against the real padding, then it went red correctly.

## Gate

**83 suites / 1628 checks / 0 failures.** Baseline rewritten (`durabilitygap.py 10`).

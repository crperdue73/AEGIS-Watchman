# FINDING 2026-10-30a — a read error in the audit log stops delivery of events already on disk

**Status:** Instrument added (`scripts/blockship.py`). Static analysis; no Go, no running collector.
**Instrument:** `scripts/blockship.py` · **Beat:** 674 · **Family:** 667, 669, 670, 671, 672

## The setup

`BehaviorEngine.Poll` (behavior.go:386-436) carries a docstring I would defend:

> "A central outage therefore costs latency, not evidence — whatever did not ship rides the next cycle. The spool is the
> single submission path (append, then drain), so there is no separate direct-send branch that could double-deliver."

That is accurate about **central outages**, and beats 659/670 confirmed the spool side of it. So — credit first — this is
careful code with a real durability story behind it. The question I had not asked is what else shares that control flow.

## The mechanism

Poll's statements, in order:

1. `b.watch.observe()` — log health
2. `b.timeline.Prune(now)`
3. `events, err := b.eng.Matches()` — **reads the auditd log**
   `if err != nil { log.Printf(...); return nil }` ← **returns**
4. `if len(events) > 0 { b.spool.Append(...) }`
5. `b.drain()` — ships the spool

Step 3's `return nil` sits **above** step 5. `Matches()` returns an error exactly when `e.src.Actions()` does
(telemetry/engine.go:80-83), and `FileSource.Actions()` (engine.go:156-187) returns a real error on `os.Open`
(minus `IsNotExist`), `Stat`, `Seek`, and `io.ReadFull` beyond EOF — **five** `return nil, err` sites. The two
non-errors are deliberate and correct: a missing log returns `nil, nil`, and `size == offset` returns `nil, nil`.

So while a read error persists, every poll returns at step 3 and the spool is **never drained** — even though the spool
holds events that were fsynced in earlier cycles and are waiting only for a `POST`.

## Severity, stated precisely (this is NOT beat 670)

**Nothing is lost.** The events are on disk; the moment `Actions()` succeeds, Poll reaches `drain()` and the whole backlog
ships. This is not the discarded-batch defect of 670 and I am not going to inflate it into one.

What is wrong is the **coupling**: a broken *sensor* halts *delivery* of already-captured evidence. And the delay is
**unbounded**, not one cycle, because `drain()` has no timer of its own — measured: exactly one call site, no other file
calls it, and no `time.NewTicker`/`time.Tick`/`time.After` anywhere in behavior.go. Poll is the only thing that ships
anything, so the length of the delay equals the length of the read outage, which can be permanent (auditd rotated to a
path the collector cannot open) while central is perfectly healthy.

## Why this is the join in the stack, not another link beside it

| beat | finding |
|---|---|
| 667 | `/healthz` cannot say no |
| 669 | the wire cannot say "I missed something"; absent hosts are omitted from the fleet view |
| 670 | a spool-write failure discards the batch |
| 671 | an oversized record wedges Drain, reporting success |
| **674** | **a read failure stops delivery of what is already on disk** |

This one supplies the *path* through them: **read failure → zero matches → no append → no drain → quiet host → absent
fleet row → healthz says ok.** Four separately-documented silences that are, in this state, one screen. Each was found by
asking a different question; none of them alone told me they chain.

## Claim / not claimed

**Claimed (read from source, comments stripped):** the `return nil` on `Matches()` error precedes `drain()` so that cycle
skips shipping; `Actions()` fails on Open/Stat/Seek/ReadFull (5 error returns); a missing log is *not* an error; `drain()`
has exactly one call site and owns no timer.

**Not claimed:** that any event is lost on this path — it is not. **Not claimed that read errors are common** — I did not
measure EIO/EACCES/EMFILE rates; the argument is *reachable and unbounded*, not *frequent*. **Not claimed that Go agrees**
— no toolchain (beat 642); this is source reading plus a Python parse, and the fix needs a Go test.

## Fix (Dad's call — small, and one variant is better than the other)

1. **Drain in a `defer`** so shipping is not conditional on the sensor working. This is the minimal correct change.
2. Better, and a separate decision: **drain before append**. The backlog is *older* than this cycle's events, so shipping
   it first is both more correct on ordering and makes the whole class of "early return skips drain" impossible.
3. Do **not** simply reorder the error branch below `drain()` — that works, but leaves the same trap for the next
   statement someone adds above drain.

## Mutation proof

| mutation | result |
|---|---|
| `b.drain()` added above the `Matches()` error return (**fix 1**) | **FAIL** ×3 — the ordering assertions invert, as they must · rc=1 |
| `Actions()` error swallowed in `Matches()` (`events, _ := ...`) | **FAIL** ×2 — "Matches() returns the error from e.src.Actions()" · rc=1 |
| control | 20 rows green · rc=0 |

The first mutant is the one that matters: the checks **go red when the fix lands**, which is how a finding self-retires.

## Gate

**87 suites / 1681 checks / 0 failures.** Baseline rewritten (`blockship.py 20`).

*(Gate number corrected from 1683 to 1681 — I typed the total before running the gate for the **fourth** consecutive beat.
The measurement wins; the rule is that numbers in prose come from the instrument, not from anticipation.)*

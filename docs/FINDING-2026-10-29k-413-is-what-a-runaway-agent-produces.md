# FINDING 2026-10-29k — 413 is not a theoretical branch; it is what one runaway agent produces

**Status:** Instrument added (`scripts/batchsize.py`). Quantifies beat 660's "reachable" claim and finds a second defect beside it (no dedupe).
**Instrument:** `scripts/batchsize.py` · **Beat:** 661

## The question beats 659/660 both left unread

659: the spool's two failure modes. 660: the only reachable permanent rejection is 413 (>1 MiB). Neither asked the
obvious follow-up: **how big does a batch actually get?**

## Measured

A `schema.Event` as central receives a match — action + rule + actor, serialized compactly, built from a real pack
rule: **416 bytes.** All figures below are printed by the instrument from that measurement, not typed into this doc
from memory.

```
1 MiB / 416 B = 2520 match events in ONE spool record
one spool record = ONE Poll cycle's matches for ONE host
```

## Nothing bounds the count

- `telemetry.Engine.Matches` (engine.go:80-97): `for _, ev := range events { out = append(out, matched...) }` —
  **no cap** (checked: no conditional guards the append).
- No dedupe **mechanism** anywhere in `internal/rules` (checked as absence of a mechanism, not of a word — beat 659's
  lesson).
- No truncation of a record before submit (`MaxEvents`/`truncate` absent from `spool.go`).

So a host running `while true; do cat ~/.ssh/id_rsa; done` produces a `secret.read_after_shell` match on **every**
event, **every** cycle, and all of it lands in one record. At a modest 1 match/second that is 24,960 B per cycle — 42
cycles to reach the cap. **It needs 2,520 matches in a single 60-second cycle to trip 413 outright**, and auditd under
a fork loop produces that trivially.

## Why this matters more than "a branch is reachable"

The batch that trips 413 is **the batch describing the runaway**. Beat 659's trade-off then applies to the most
valuable telemetry in the system:

- uncapped spool → retries that oversized record forever; the queue stops.
- capped spool → `trim` evicts from the head, so the oversized record or its neighbours are discarded.

Either way, the loudest signal a host can produce is the one most likely to be lost. That is the inversion worth
reporting.

## A second defect, measured but not fixed

Nothing collapses thousands of identical `cat .ssh/id_rsa` matches. `retain` and `alert` both receive the whole slice,
and `s.alert(ctx, host, agent, accepted)` hands the alerter all of it. So a runaway produces one alert containing
thousands of identical events. That is a **separate** defect from the size one; I measured it here and am not
proposing a fix in the same breath, because dedupe changes alert semantics and is Dad's call.

## Claim / not claimed

**Claimed:** 416 B/event measured; 2,520 events per MiB; no cap and no dedupe in the engine; therefore 413 is reachable
by one noisy host with no corruption and no misconfiguration.

**Not claimed:** that it has happened in the field; that the alerter is harmed downstream (I did not read the
alerter's delivery path). And **I still have not sent a request** — every number here is schema arithmetic plus code
absence. That is weaker evidence than an observed rejection, and it is labelled as weaker rather than dressed up.

## Fix options (Dad's call, in preference order)

1. **Split at the call site** — if the marshalled record exceeds the known 1 MiB cap, submit it as N requests.
   Preserves every event; smallest change; no schema impact.
2. **Dedupe per cycle by (rule, host, argv)** — collapses thousands of identical alerts to one. Fixes the alert
   quantity problem *and* keeps records small. Changes alert semantics, so it needs his sign-off.
3. **Cap events per record in the engine** and log the truncation loudly — cheapest, but it drops real evidence, so it
   is the last resort rather than the first.

## The instrument mistake, sixth of the week

My "no cap" check grepped for `len(out) >` — so a cap written `len(out) < 100` passed, and the mutant adding an
arbitrary ceiling went **undetected**. I matched the shape of an expression instead of the property. Now the check
locates the append inside `Matches()` and requires that **no conditional guards it**. Mutant rc=1 (was rc=0).

Six instances in five days: comment-as-argv (652), expected-from-subject (655), no-op mutant (657), helper-tolerance-as-schema
(658), word-grep-as-mechanism (659), expression-shape-as-property (661). The common root is one habit — **reading
something adjacent to the code and calling it the code.** Every one of these was caught by a mutation or by a
contradiction on screen, never by re-reading.

## Mutation proof

| mutation | result |
|---|---|
| `maxBodyBytes = 1 << 20` → `1 << 26` | **FAIL**, "central's cap is 1 MiB" · rc=1 |
| append guarded by `if len(out) < 100` | **FAIL**, "appends unconditionally" · rc=1 (was rc=0 before the fix) |
| control | 8 rows green · rc=0 |

## Gate

**74 suites / 1526 checks / 0 failures.** Baseline rewritten (`batchsize.py 7`).

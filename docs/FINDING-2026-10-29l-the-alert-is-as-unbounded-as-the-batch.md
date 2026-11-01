# FINDING 2026-10-29l — the alert is as unbounded as the batch, and the sink that always succeeds keeps no evidence

**Status:** Instrument added (`scripts/alertfanout.py`). Same condition as beat 661, one layer downstream.
**Instrument:** `scripts/alertfanout.py` · **Beat:** 662

## The chain

```
collector  --POST /actions, 1 MiB cap, 413-->  central
           IngestActions accepts the whole slice
           s.alert(ctx, host, agent, accepted)   -> ONE Alert holding ALL events
           alerter.Alert(alert)                  -> fanned to every sink, unchanged
```

Beat 661 measured the batch as unbounded in the engine. This beat measures the consequence one layer down: **the alert
payload is unbounded too.** At the batch count that trips 413 (2,468 events at the measured 426 B), the Alert JSON is
**1.00 MiB in a single message.**

## Three sinks, three different failures

| sink | behaviour on an oversized alert |
|---|---|
| `WebhookAlerter` (alert.go:120-141) | `json.Marshal` of the **whole** alert → **one** POST. No splitting, no truncation, no size check. A receiver with a 1 MiB body limit answers 413 → error. |
| `EmailAlerter` (email.go) | renders **every** event into the plain-text body plus a JSON attachment. Many MTAs refuse a message this size at SMTP. |
| `LogAlerter` (alert.go:149-155) | prints **only** `a.Summary()`. Every event discarded. |

`Summary()` is `"2520 rule matches on dept-crperdue-tech:/home/vivi (max=high)"` — it counts classes and nothing else.
**Verified: it never names a rule id, a path, or an argv.**

## The asymmetry that makes it a finding

`MultiAlerter`'s doc says: *"Every sink is attempted even if an earlier one fails, and the failures are joined into one
error so one unreachable webhook cannot silently suppress a working sink."* That is true, and it is the right instinct —
it defends against a sink being **down**.

It does not defend against a sink being **unable**:

- webhook 413 → returns an error → logged once
- email 552 → returns an error → logged once
- log sink → **returns `nil` unconditionally** (verified: its only return) → MultiAlerter counts it a success

So on an oversized alert the two rich sinks fail loudly and the one sink that always "works" is the one that discarded
the content. **The operator's fallback is a count.** Every fact needed to act on a runaway agent — the rule id, the
argv — exists only in the payloads that just failed.

And `alert()` makes **one attempt**, then logs. No retry, no queue, no durable copy (verified: no loop in `alert`).

## Why this is not an edge case on an edge case

The condition that produces an oversized alert is the same condition as the oversized batch: **a runaway agent.** That
is precisely when the rule id and argv matter. Beat 661's inversion repeats one layer up — the loudest signal is the
most likely to arrive as a bare number.

## Claim / not claimed

**Claimed:** the alert carries the full slice; the payload measures 1.00 MiB at the 413 threshold; Webhook and Email do
nothing about size; `LogAlerter` discards all events by construction and returns nil; `MultiAlerter` therefore counts it
as delivered; delivery is attempted once with no retry.

**Not claimed:** that any specific endpoint rejects at 1 MiB — that is the receiver's policy, not our code. Not claimed:
that a human has missed an alert, or that email refused one. **Still no request sent.** This is source plus measured
serialization, and the "some receiver rejects it" step rests on the receiver, marked as the assumption it is.

## Fixes (Dad's call, preference order)

1. **Cap events per alert; summarise the rest.** One alert naming the distinct `(rule_id, path/argv)` pairs plus a count
   is *smaller*, *more* useful, and bounded regardless of volume. This is the fix I would choose: it solves the size
   problem and the no-dedupe problem from beat 661 in one move.
2. **Split oversized webhook payloads** at the call site — preserves everything, but multiplies alert noise.
3. **`LogAlerter` prints the distinct rule ids**, not just a count — so the last-resort sink still carries actionable
   detail.

## The instrument mistake — seventh prose-as-data bite this week

My "LogAlerter returns nil → counted as a success" check tested `"err" not in loga.split(...)`, i.e. it searched text
that included comments. It **failed on correct code** because the word appeared in prose. Fixed by stripping comment
lines and requiring the body's only `return` statement to be exactly `return nil` — a test of control flow rather than
of wording. Green after the fix; mutant rc=1.

That is the seventh instance in six days (652 comment-as-argv, 655 expected-from-subject, 657 no-op mutant, 658
helper-as-schema, 659 word-grep-as-mechanism, 661 expression-shape-as-property, 662 comment-text-as-control-flow). It
is always the same move: reading something near the code and calling it the code. Note that this one failed *loudly*,
on correct code — which is the good direction to be wrong in, and the reason the check was worth writing rather than
sanity-checking into silence.

## Mutation proof

| mutation | result |
|---|---|
| `LogAlerter` returns an error when `len(a.Events) > 100` | **FAIL** ×2 ("prints only the summary", "only return is nil") · rc=1 |
| `alert()` truncates `Events` to 10 | **FAIL**, "builds an Alert from the full slice" · rc=1 |
| control | 14 rows green · rc=0 |

## Gate

**75 suites / 1537 checks / 0 failures.** Baseline rewritten (`alertfanout.py 14`).

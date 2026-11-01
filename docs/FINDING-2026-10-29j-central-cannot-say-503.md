# FINDING 2026-10-29j — central emits no transient 5xx on the actions path, so every rejection is permanent

**Status:** Premise from beat 659 settled. Beat 659's framing **refined, not retracted** — the gap is real, the shape is different.
**Instrument:** `scripts/centralreject.py` · **Beat:** 660

## What beat 659 owed

Beat 659 ended with an explicit debt: *"I have not observed central return a 400 to a collector match batch. I read
the handler and inferred it."* That premise is load-bearing — if central never rejects, the spool finding is latent.

Settled now by reading `handleActions` and, more importantly, by asking whether its **500 branch can be reached at
all**.

## `IngestActions` cannot fail

```go
// internal/central/service.go:183-210
for _, ev := range events {
    if ev.Event == "" { continue }        // drop, don't error
    ...
    accepted = append(accepted, ev)
}
if len(accepted) == 0 { return 0, nil }
s.resolveActors(accepted)
s.retain(host, agent, accepted)
if s.alerter != nil { s.alert(ctx, host, agent, accepted) }
return len(accepted), nil
```

Every `return` is `nil`-error. Its three callees — `retain` (service.go:82), `resolveActors` (99), `alert` (169) —
**return nothing at all.** So `handleActions`'s

```go
if err != nil { writeErr(w, http.StatusInternalServerError, "ingest failed") }
```

is **dead code**. Central cannot answer 500 on this path.

## What that does to beat 659's sentence

Beat 659 said: *"`Drain` cannot distinguish a transient failure from a permanent one."* True, but it implied a
spectrum — 503 versus 400. **There is no spectrum:**

| failure | class | can retrying help? |
|---|---|---|
| connection refused / timeout / DNS | transport — never reaches the status branch | yes |
| 400 unreadable body, invalid json, missing host.id, missing events | verdict on the bytes | **no, ever** |
| 413 body > 1 MiB | verdict on the bytes | **no, ever** |
| 500 | unreachable (dead branch) | — |

**Central's rejections are permanent by construction.** It is not that the collector mislabels them — it is that on
this path there is nothing transient to mislabel. The single `error` return fuses the only transient case (transport)
with a class that reruns forever and can never succeed.

## Which rejection is actually reachable from a real collector?

The premise I actually owed. Going code by code:

- **missing `host.id`** — collector always populates it from config; a request without it is meaningless. Practically no.
- **missing events** — `Append` returns early when `len(events) == 0` (spool.go:61-63). No empty record is ever spooled. No.
- **invalid json / unreadable body** — the line was written by `json.Marshal`, and `read()` **discards** malformed lines
  (spool.go:142-144). A corrupt line is healed by the collector's own reader before the transport sees it. No.
- **413, body > 1 MiB** — one cycle's matches for one host exceeds 1 MiB. Under heavy alert volume this happens, and the
  batch that trips it is precisely the batch from a flapping host. **Yes.**

**One reachable permanent rejection: 413, on the largest batches.** And the response to it is beat 659's trade-off —
uncapped spool retries that oversized batch forever; capped spool evicts it (and its neighbours) at the cap.

## Claim / not claimed

**Claimed:** the 500 branch is dead (measured: no error return from `IngestActions` or any callee); central produces no
transient 5xx on the actions path; the only reachable permanent rejection is 413 from an oversized batch; the
collector's single `error` still fuses transport with permanent.

**Not claimed:** that any host has hit a 413. **The standing gap is unchanged — I still have not sent a request.** This
is a code reading plus a reachability argument, and the honest status is "reachable in principle, unobserved in
practice." I am not going to dress that as a measurement.

## What this changes about the fix

Beat 659 proposed: classify 4xx (except 408/429) as permanent → quarantine and continue. That still holds and is now
better justified: **there is no transient status code to preserve**, so the classification can be simpler than
"4xx except 408/429" —

> if the status is non-2xx, the batch is rejected on its contents → quarantine it, log it, continue draining.
> Transport errors are the only thing worth retrying.

Plus the second half, unchanged: a **413 is avoidable at the source** — the collector can split a spool record at
`SubmitActions` time when it exceeds the known 1 MiB cap, so the rejection never occurs. That is a one-line guard at
the call site and it is Dad's call which one lands first.

## Mutation proof

| mutation | result |
|---|---|
| `retain` given an `error` return + caller checks it (500 branch live) | **FAIL**, "retain returns no error (500 branch unreachable)" · rc=1 |
| `if len(req.Events) == 0` → `if false` (400 removed) | **FAIL**, "handleActions rejects empty events with 400" · rc=1 |
| control | 3 rejection facts + 4 unreachability facts + 2 collector facts, green · rc=0 |

## Gate

**73 suites / 1518 checks / 0 failures.** Baseline rewritten (`centralreject.py 10`).

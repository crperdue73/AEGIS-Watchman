# FINDING 2026-11-02g — a batch central accepted ZERO events from is reported as shipped and deleted

**Status:** CLOSED 2026-11-06 (beat 836). Dad picked **option 1 + 3**; both applied and measured.
**Instrument:** `scripts/spoolackcensus.py` (original measurement), now inverted to guard the fix;
`internal/central/totaldrop_e2e_test.go` (new, end-to-end — real Spool, real Client, real handler).

## Resolution (2026-11-06, beat 836)

**Option 1 — applied.** `IngestActions` returns `ErrTotalDrop` when a non-empty batch retains
nothing; the handler answers **400** with `{"error":…,"accepted":0,"received":N}`. It is deliberately
400 and not 5xx: the spool retries on a transport error, so a 500 would hold a poison batch on disk
forever. Partial drops stay 202 — a collector must not retry a garbage event for eternity.

**Option 3 — was already in.** `droppedUnnamed` had been added and surfaced on the fleet page; the
count survives the refusal, so the loss is visible even while the batch is preserved.

**The measurement that had never been taken.** Every prior piece used a double on the other side —
the spool census stubbed submit, the refusal tests stubbed HTTP, the handler test stubbed the
service. `totaldrop_e2e_test.go` puts the real `Spool`, the real `Client` and the real handler in
one line and asks the only question that matters: *after a batch central cannot use, is the evidence
still on disk?* It also carries a live-batch control, so "still on disk" cannot mean "never sent".

Mutation-proven both ways: reverting the guard in `service.go` makes the e2e test print
`EVIDENCE LOSS: the spool holds 0 batch(es)…want 1` (rc=1); restored, rc=0. `spoolackcensus.py`
clause A was **inverted** — it used to assert the defect and now fails if the refusal goes missing —
and re-proven in the reverted direction (1 FAIL / 16 ok), then restored (17 ok). A clause updated
only to "go green" would be a check that can never fail again.

## The claim the code makes

`internal/collector/spool.go:20-27`, the comment above the spool type:

> Every match is appended to a JSONL file before the network call is even attempted, and **only removed once central
> has accepted the batch.** On each cycle the collector drains what it can; whatever central does not take stays on
> disk and rides the next attempt. A crash, a restart, or a central outage **costs latency, never evidence.**

That is the promise the spool exists to keep. It is a strong promise, correctly stated, and it is about a security
tool — "never evidence" is the whole point.

## What actually happens

`IngestActions` (`internal/central/service.go:183-215`) drops events whose `Event` name is empty:

```go
if ev.Event == "" {
    continue // not an event; drop rather than alert on garbage
}
```

dropping to zero is **not an error** — `if len(accepted) == 0 { return 0, nil }` — so the handler
(`internal/central/http.go:253-261`) sees `err == nil` and answers **`202 Accepted` with `{"accepted":0}`**.

On the collector side, `Spool.Drain` (`spool.go:100-131`) advances on `submit(...) == nil`, and counts progress as
`shipped += len(r.Events)` — the number it **submitted**, not the number central **took**. A `202` is `nil`. So:

```go
if err := submit(ctx, ...); err != nil { break }   // 202 with accepted:0 is NOT an error
shipped += len(r.Events)                            // counts what we sent
...
s.rewrite(recs[idx:])                               // drops what we sent
```

**MEASURED (real `Spool`, real `Drain`, submitter reporting success exactly as the HTTP client does on 202):**

```
RESULT spooled_batches_before=2
RESULT shipped=2 err=<nil> spooled_batches_after=0
```

**Two batches central accepted zero events from are counted as shipped and removed from the spool.** Measured at the
endpoint too:

```
RESULT A empty_named_events code=202 body={"accepted":0}
RESULT D all_dropped n=0 err=<nil>
```

## Why it is reachable, not theoretical

- `schema.Event.Event` is a plain `string` with **no validation anywhere** — nothing in the type, nothing on load.
- `schema.Event.Actor` is `*Actor` with `json:"actor"` and **no `omitempty`** — a producer sending `"actor": null`
  makes `json.Unmarshal` fail (an unmarshal failure is a clean 400, so that one is safe). But a producer that simply
  omits the field, or ships a differently-named field (`"type"` instead of `"event"`), post-`null`s cleanly into a
  **zero-valued `Event` string** and is silently discarded with a `202`.
- The drop is deliberate and defensible — *"drop rather than alert on garbage"* is correct. **The defect is not the
  drop. It is that the drop is invisible to the sender, and the spool deletes the evidence on the strength of a 202.**
- 745 established that events in this system already arrive in the thousands per cycle; 746 that retention failures can
  go unseen. This is the same family: **a success signal that does not carry the thing it certifies.**

## What this is NOT

- **Not** a claim that events are currently being lost in production. I have no fleet evidence of a producer emitting
  unnamed events; the shipped binary's own emitters all set `Event`. The finding is that **the contract cannot tell**.
- **Not** a claim the `202` status is wrong for a partial accept. 202 for "some accepted" is reasonable. The problem is
  202 for "**none** accepted" being indistinguishable from 202 for "all accepted" at the caller.
- **Not** a claim about `Ingest` (the drift path). Different code; that one has a 200/`IngestResult` shape and was
  covered at 743–746.

## Sound parts, checked rather than assumed

- The zero-event **request** is rejected properly: `len(req.Events) == 0` → `400 missing events` (measured).
- A missing `host.id` is rejected: `400 missing host.id` (measured).
- `Drain` **does** pause and preserve the spool on a real error — a network failure or non-2xx keeps every record and
  the next cycle retries. That is the mechanism the comment describes, and it works; the gap is only that `202` is
  treated as proof of acceptance when it is proof of receipt.
- `Spool.trim()` bounds the file, so this is not an unbounded-growth bug.

## Options (none applied — the delivery contract is Dad's call)

1. **Have the handler fail a total drop**: if `accepted == 0 && len(req.Events) > 0`, return `400` with a distinct
   error. One line, changes the wire contract ⇒ **Dad's call**. This is the option that makes the promise true.
2. **Have the collector read `{"accepted":N}`** and treat `N < submitted` as partial progress — accurate, but leaves
   the spool holding events central would drop forever (an infinite retry loop) unless paired with option 3.
3. **Count drops on the central side** (a counter/stat for `events dropped: empty name`) and surface it on the fleet
   page — makes the loss visible without changing the contract. Cheapest honest step.
4. **Validate `Event` at the schema boundary** so an unnamed event cannot be constructed by the collector at all —
   fails earlier and louder, but pushes the decision onto whoever owns the schema.

The right answer is probably **1 + 3**: refuse a total drop, and count partial ones. I am not applying either.

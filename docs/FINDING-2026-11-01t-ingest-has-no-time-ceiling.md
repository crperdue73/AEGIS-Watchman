# FINDING — 2026-11-01t: retention is exempt from the ingest deadline

**Retitled** (was: "the ingest path has no ceiling on time"). The original title is wrong — see the correction below.

**Status:** source reads. **NOT observed. NOT APPLIED.** **Instrument:** `scripts/ingestceilingcensus.py`.

**One line:** both ingest handlers set a 10-second deadline — and `retain` throws it away by passing
`context.Background()` to every `Append`. So the one step that scales with event count is the one step the deadline
cannot stop.

> # ⚠️ CORRECTION — same beat, and it guts the headline
>
> **I wrote "nothing bounds how long a request may run." That is FALSE.** `http.go:205` and `http.go:250` both wrap the
> handler's context: `ctx, cancel := contextWithTimeout(r, 10*time.Second)` → `context.WithTimeout(r.Context(), 10s)`
> (`context.go:11-13`). **Both ingest paths have a 10-second deadline.** I found this only because beat 734's discipline
> made me go looking for the falsifying file *before* posting — the same mistake, caught for once in the writing instead
> of by a reader.
>
> What survives, and it is narrower and more interesting:
>
> 1. **`retain` discards the deadline.** It calls `s.records.Append(context.Background(), rec)` (`service.go:89`) — a
>    *fresh* background context, not the one the handler built. So the 10s ceiling covers `GetBaseline`, `Drift`,
>    `PutBaseline`, and alerting, but **not retention**. Retention runs to completion no matter what the deadline says,
>    even after the handler has already returned a 500. That is 738's `context.Background()` read from the other side:
>    it is correct that a record write should not be cancelled by a client hangup — but it also means the deadline
>    cannot stop it.
> 2. **`alert` inherits the deadline indirectly only because it takes the handler's `ctx`** — `service.go:161`
>    `s.alert(ctx, ...)`, and `alert` builds `Alert{When: time.Now()}`, so the webhook call *is* bounded by the request
>    deadline. The email sender spawns its own goroutine (`email.go:65`) — a deadline cannot cancel a goroutine that
>    does not watch the context.
> 3. **The server still has no `WriteTimeout`/`IdleTimeout`** — that part was true. But with a 10s per-request context it
>    matters far less than I claimed: the handler returns in 10s regardless. **It is a defence-in-depth gap, not an
>    unbounded-time bug.**
>
> The sharp claim, replacing the wrong one: **the one step in the ingest path that can scale to N × 3 commits — retention —
> is the one step exempt from the deadline that bounds everything around it.**

## Three named questions, answered

**1. Do `Ingest` and `IngestActions` share a host?** **Yes — unavoidably.** They are two separate HTTP handlers
(`http.go:47-48`) on one `ServeMux` behind one `http.Server`, and Go serves each connection in its own goroutine. There
is no mutex, semaphore, or queue between them. So for one host, `POST /api/v1/events` and `POST /api/v1/actions` **can
and will run concurrently**, each reaching `retain` → `Append` on the same store. This closes the question I left open
in beat 737: **the SQLite interleaving is not theoretical.** It is the normal state for any fleet where the collector
reports identity drift and behavior events on overlapping cycles.

The saving grace, already recorded in 737: the cap is **idempotent**, so concurrent trims converge rather than corrupt.
The cost is doubled prune work, not lost rows.

**2. Does anything bound the time a request may take?** ~~**No.**~~ **Yes — 10s per request.** *(This answer was written
before I read `http.go`. It is kept because the reasoning in it is what produced the wrong headline.)* `main.go:100-104`:

```go
srv := &http.Server{
    Addr:              cfg.Addr,
    Handler:           svc.Handler(),
    ReadHeaderTimeout: 10 * time.Second,
}
```

`ReadHeaderTimeout` is the *slowloris* defence — it bounds how long a client may take to **send headers**, and nothing
else. `WriteTimeout` is zero (no write deadline), `ReadTimeout` is zero (once headers are in, the body may trickle
forever), `IdleTimeout` is zero (keep-alives live until the client leaves). So a single request may occupy a goroutine
indefinitely.

**3. What is the worst-case blocking chain?** This is where the three beats meet:

```
POST /api/v1/events
  → handleIngest
  → Service.Ingest
      → store.GetBaseline  (RWMutex read)
      → PutBaseline        (Mutex **write** — held across a full inventory write)
      → retain             (beat 738: N × Append, each 3 commits on SQLite — beats 737)
      → alert
          → MultiAlerter   (sinks run SEQUENTIALLY, alert.go:166-175)
              → WebhookAlerter   10s timeout, but the ctx is context.Background()  ← not the request
              → EmailAlerter     dial + SMTP conversation + its own timeout (email.go:65 go func)
```

The `context.Background()` in `retain` (correct in 738 — a record write must not die because the collector hung up) is
also passed to `alert`. So **the request's own cancellation does not reach any of it.** A webhook that takes ten
seconds, on a MultiAlerter with three sinks, inside a handler with no `WriteTimeout`, during a storm that has N events
to append: there is no ceiling anywhere in that chain except the webhook's own client timeout and the email dialer's.

**Nothing here is a bug I can see from source.** Blocking handlers on a server with no write deadline is a normal Go
shape, and the 10s slowloris guard is a deliberate, documented choice. What I am recording is that the *shape of the
cost* is unbounded and nobody wrote it down.

## The guard that IS there, and why they left the others off

*(This section is superseded by the correction: the per-request 10s context is the real guard. Kept for the reasoning.)*

`ReadHeaderTimeout` only. That is not an oversight in this tree — it is the standard Go recommendation, and every
reverse proxy in front of this (if one exists) supplies the rest. **But there is no reverse proxy in the deployment
docs for Phase 1, and `central` is meant to be reached by collectors directly.** So the omission matters in exactly the
deployment this code is aimed at.

## Options — listed, not picked

Restated against the corrected claim (retention exempt from the deadline), not the wrong one:

1. **Thread the handler's deadline into retention for the *batch*, not the record.** Pass `ctx` to `retain` and use it
   for the *set* — i.e. "stop starting new appends after the deadline" — while letting an in-flight `Append` finish.
   That is the honest version of 738's principle: an individual row deserves to survive a client hangup; the *thousandth*
   row of a report the caller has already abandoned does not. Small, local, no interface change.
2. **Give retention its own generous deadline** (e.g. 30s) derived from `context.Background()`, so it is bounded but not
   bound by the request. Closes "unbounded" without changing what survives a hangup.
3. **Set `WriteTimeout`/`IdleTimeout` on the server anyway** — defence in depth, as I originally argued. It no longer
   addresses the retention gap; it is still good hygiene and costs two fields.
4. **Document it** — one sentence next to `retain`: "best-effort and deliberately not bound by the request deadline."
   Cheapest; leaves the cost unbounded under storm, which is when it runs longest.

Options 1–3 change runtime behaviour under load ⇒ **Dad's call** (beat 724).

## What I am NOT claiming

- **Not** that a request hangs in practice. I have no measurement and no traffic.
- **Not** that `context.Background()` in `retain` is wrong — it is right, for the reason 738 gives. The observation is
  that the same context flows to `alert`, where detachment from the request is a different question.
- **Not** that sequential `MultiAlerter` is wrong — it is deterministic and errors are aggregated (`alert.go:176-178`),
  which is a real virtue. It just multiplies the worst case by sink count.
- **Not** that `PutBaseline` under a write lock is a defect — that is the correct way to make the baseline durable
  before alerting, and the ordering comment in `Ingest` explains exactly why.
- **Not** any latency number. Same discipline as 738: shape, not rate.

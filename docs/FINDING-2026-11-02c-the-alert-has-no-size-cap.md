# FINDING — 2026-11-02c: the alert has no size cap, and a redeploy posts 374 KB

**Status:** source reads **+ executed measurement**. **NOT observed in a deployment. NOT APPLIED.**
**Instrument:** `scripts/alertbulkcensus.py`.

## The claim

`MaxDiffBytes` caps the unified diff **attached to each event** (`config.go:33`, default `4096`, `0 = unlimited`).
Nothing caps **the number of events in an alert**, and nothing caps **the serialised alert**.

`Service.alert` (`service.go:169-174`) wraps *every* drift event from one ingest cycle into a single
`Alert{Events: events}` and hands it to the Alerter. `WebhookAlerter.Alert` (`alert.go:122-141`) then
`json.Marshal`s the whole struct and POSTs it. The only configured ceiling is the HTTP client's 10s timeout.

So the guard exists at the per-event level and is absent at the batch level — **the same refrain as beat 740's cap
(per-record yes, per-view no) and 743's walk (per-glob yes, per-EACCES no).**

## Measured

Real `Drift` (unchanged `MaxDiffBytes=4096`), 400-line files, contiguous change so each diff is small, then the alert
serialised exactly as `WebhookAlerter` does:

```
RESULT files=   1 events=   1 diff_bytes=    66 alert_json=    479
RESULT files=  10 events=  10 diff_bytes=   660 alert_json=   3843
RESULT files=  50 events=  50 diff_bytes=  3300 alert_json=  18801
RESULT files= 200 events= 200 diff_bytes= 13200 alert_json=  74887
RESULT files=1000 events=1000 diff_bytes= 66000 alert_json= 373988
```

**Strictly linear, ~374 bytes per event** — and note the ratio is worse when diffs are *small*: 3,490 bytes of changed
content became an 18,801-byte alert at n=50 (5.4×), because the envelope, not the diff, dominates. `MaxDiffBytes`
is the wrong knob for this; it caps the one part that isn't the problem.

**374 KB for a 1,000-file drift.** That is not an exotic number: a `git checkout`, a redeploy, a package upgrade, or
the `chmod` repair from beat 743 all touch a thousand files in an agent tree. Drift is *supposed* to be loud — that's
the product. The question is only whether it's loud in one POST.

## Why this stays a real thing rather than a nit

1. **Delivery failure is logged and dropped.** `alert()` (`service.go:171-173`) logs `alert delivery failed` and
   returns. There is **no retry and no queue**. A receiver that rejects an oversized body (Slack's limit is ~1 MB,
   many proxies cap at 1 MB, some at 100 KB) means the entire cycle's alert is **lost**, and the only trace is a log
   line. A noisy cycle becomes the one that doesn't get through — **the alert degrades exactly when the signal is
   strongest.** That is the finding, not the byte count.
2. **Every sink pays it separately.** `Config.Alerter` (`config.go:241-264`) builds a `MultiAlerter` by default when
   more than one sink is configured, and `MultiAlerter` (`alert.go:159-176`) calls each one in sequence with the same
   `Alert`. Webhook marshals it; `EmailAlerter` re-renders the entire body (`email.go:95-113`, one `writeEvent` per
   event). Two sinks = the amplification and the work, twice.
3. **The email path has the same shape.** An alert with 1,000 events renders a 1,000-section plain-text email with no
   truncation. Providers (and humans) have opinions about that.

## Sound parts, checked before writing this up

- **`MaxDiffBytes` itself is right**: default 4096, `0 = unlimited` documented, negative rejected at
  `config.go:130`. Bounding the diff is a good call.
- **One alert per cycle, not per event** (`alert.go:16-18`) is the correct design and is stated as intent. The bug is
  that "one notification instead of N" was implemented without a bound on "one".
- **`MultiAlerter` is careful**: every sink is attempted even if an earlier fails, and the errors are joined
  (`:159-176`) — one dead webhook cannot suppress a working sink. Good. It just doesn't know about size.
- **The webhook has a timeout** (`alert.go:116`, 10s) — so a hung receiver cannot block ingest forever. That's a real
  guard, and it is the *only* guard on this path.

## Options — listed, not picked

1. **Cap events per alert** (`MaxAlertEvents`, e.g. 200) and emit `… and 800 more` in `Summary()`. Cheapest that
   keeps the alert deliverable. Changes the alert contract ⇒ Dad's call.
2. **Cap serialised bytes** in `WebhookAlerter`, refusing or truncating with an explicit marker. Protects the sink
   specifically; leaves the email sink unbounded.
3. **Batch by count / time** — N alerts of ≤M events per cycle. Preserves every event, costs N POSTs and a
   rate-limit conversation.
4. **At minimum, make the failure loud**: if delivery fails, that must not be a bare log line — a dropped alert is a
   detection failure and should be counted/visible somewhere an operator sees. The right answer independent of 1–3.

## What I am NOT claiming

- **Not** that alerts are currently being lost. No deployment observed; the sizes above are constructed.
- **Not** that drift should be quieter. Volume is the product; the issue is the shape of delivery.
- **Not** that `MaxDiffBytes` is the wrong idea — it's the wrong *lever for this specific problem*, which is a
  different statement.
- **Not** measured against a real webhook's limits. 1 MB / 100 KB proxy caps are stated industry norms, not something
  I probed here.

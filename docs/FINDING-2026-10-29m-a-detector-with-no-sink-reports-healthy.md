# FINDING 2026-10-29m — zero sinks composes a nil alerter, and a detector that drops every alert still reports healthy

**Status:** Instrument added (`scripts/alertwiring.py`). Prior question to beats 661/662 — both assumed delivery *somewhere*.
**Instrument:** `scripts/alertwiring.py` · **Beat:** 663

## The prior question

661: how big is the batch. 662: what does each sink do with it. Both assume **a sink is installed**. Checked: what if
none is?

## Measured

```go
// config.go:241 — zero sinks returns nil
switch len(sinks) {
case 0:
    return nil
...
// cmd/central/main.go:88
if a := cfg.Alerter(log.Default()); a != nil { svc.SetAlerter(a) }
else { log.Printf("watchman-central: alerting disabled (no webhook, log off)") }
// service.go IngestActions
if s.alerter != nil { s.alert(ctx, host, agent, accepted) }   // silently skipped
```

With no sink: `alerter` stays `nil`, and the alert step is **skipped with no runtime signal** — verified that the skip
block contains no log call, no counter, and no error path.

So the service boots, accepts every batch, `retain`s every event to the record store, and **drops 100% of alerts.**

## The saving grace, stated first

`DefaultConfig()` sets `Alerts: Alerts{Log: true}` (config.go:98), and a **missing** config file returns those defaults
(checked). Out of the box — no config, empty config — the log sink is on and an alert is at least printed. **This is not
a defect in the default.** I want that up front because the finding is about a specific config, not the product's
out-of-box behaviour.

## The hole

`Log: true` is only a default. A config containing

```yaml
alerts:
  log: false
```

— which is the natural thing to write while setting up a webhook you have not finished configuring yet — composes **no
sink**. There is **no `Validate()` on `Config` anywhere in the package** (checked for both value and pointer receivers:
zero), and `Alerter()` cannot return an error. So nothing refuses, nothing warns beyond one line at boot:

```
watchman-central: alerting disabled (no webhook, log off)
```

That line is accurate and it is printed **once**, then scrolls. Nothing at runtime ever says a detection was suppressed.
There is no "alert suppressed" path anywhere in the service (checked).

## Second path to the same place

`Alerter(logger)`'s log sink requires `logger != nil`. `main` passes `log.Default()` so that is fine today — but the
signature invites `Alerter(nil)`, and one nil argument silently fails to compose a configured sink.

## Why this one outranks beats 661/662

Those findings degrade *how* an alert arrives: oversized payload, lost detail. This one loses the alert **entirely and
quietly** while the service's own health signals — it is running, it is ingesting, it is storing — all read normal. **A
detector with no output is worse than no detector, because it reports healthy.** That is the same failure shape as the
whole project's reason for existing: the instrument that would have caught it was asleep and said nothing.

## Claim / not claimed

**Claimed:** zero sinks → nil → alert step skipped with no runtime signal; the default config avoids it; `alerts: {log:
false}` with no webhook/email reaches it; no `Validate()`, `Alerter` returns no error; one startup line and nothing
after.

**Not claimed:** that any operator has run this config. This is a configuration footgun with total blast radius, and I
am labelling it a footgun — **not an observed incident.** No request sent, no config deployed: pure source reading.

## Fix (Dad's call)

**Refuse to start** when ingest is enabled and zero sinks are configured — or, if that is too strict for a dev box,
log a loud warning that repeats (e.g. every 100th suppressed alert) so a silent detector cannot look healthy. `Config`
needs a `Validate()` either way; it currently has none, which is a larger gap than this one config path.

## Mutation proof

| mutation | result |
|---|---|
| `Alerts{Log: true}` → `Alerts{Log: false}` | **FAIL**, "DefaultConfig enables the log sink" · rc=1 |
| add `else if s.logger != nil { ... "alert suppressed: no alerter configured" }` | **FAIL**, "no runtime 'alert suppressed' path exists" · rc=1 |
| control | 13 rows green · rc=0 |

The second mutant is **the proposed fix itself** — so the check is demonstrated to fire when that fix lands, rather than
only in the abstract. It was green on first run, which is why I wrote mutants for the two claims that carry the finding
rather than accepting the green.

## Gate

**76 suites / 1549 checks / 0 failures.** Baseline rewritten (`alertwiring.py 13`).

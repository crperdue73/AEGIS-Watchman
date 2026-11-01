# FINDING — 2026-11-01l: the fleet's "last seen" is the last time a host *alarmed*

**Status:** source reads. **NOT CLAIMED as observed** (no toolchain here, beat 642). **NOT APPLIED** — adding a true
contact time is a state/schema change and a product decision.
**Instrument:** `scripts/livenesscensus.py`.
**One line:** `LastSeen` is stamped from the newest **alert record**, so it measures *last time this host was a
problem*, and the fleet console displays it as freshness.

## The chain, measured end to end

```go
// internal/central/service.go:158 — records exist ONLY when something was detected
if len(events) > 0 { ... retain ... }

// internal/central/fleet.go:128 — LastSeen comes from those records
if h.LastSeen.IsZero() || rec.When.After(h.LastSeen) { h.LastSeen = rec.When }

// internal/central/fleetpage.go:176 — and it is rendered as a freshness column
<td class="muted">{{.LastSeen}}</td>
```

`Drift` returns an empty slice for a host whose files are unchanged. So a host that checks in every 60 seconds and
has nothing wrong with it produces **zero records** and **never sets `LastSeen`**. A host whose collector died keeps
whatever `LastSeen` its last alert gave it.

**`LastSeen` is computed correctly.** It genuinely is the newest retained record's time. The defect is that the field
name, and the console column it feeds, promise *contact*, and it delivers *trouble*.

## What an operator reads off the column

- **Healthy + quiet → old timestamp.** Looks stale, is fine.
- **Dead → old timestamp.** Looks stale, is dead.
- **Bursty alerter → recent timestamp.** Looks fresh, may be dead since the burst.

The two states you most need to distinguish — a quiet system and a dead sensor — render **identically**. That is the
classic monitoring failure: *the graph goes flat and you cannot tell calm from disconnected.*

And there is no counterweight in the data. `IngestResult` is `{FirstSeen, Events}` — no timestamp. Grepping
non-test `internal/central` for `LastIngest|IngestedAt|LastContact|LastReport|HeartbeatAt` returns **nothing**. There
is no true contact time anywhere to compare against, so nothing downstream could correct the reading even if it
wanted to.

## Why this is the same family, one layer out

Beats 724–730 were about **what the detector sees and says**. This is about **what it reports about itself.**

Same species as beat 730 — an absence displayed as a measurement — but pointed at the operator instead of the
analyst. 730 said *"I couldn't look" becomes "it's gone."* This says *"it hasn't alarmed" becomes "it was seen
recently."* Both are a quiet non-event wearing the clothes of a positive finding.

It also inherits 730's root cause: because the walk cannot report what it skipped, and because ingest retains nothing
on a clean pass, **the only durable sign that a host is alive is the sign that something went wrong on it.**

## Scope, and the question I could not answer from this tree

- **Not a fabrication.** The field computes what its code says. Only the label and the display are wrong.
- **Not observed** on a running deployment.
- **I did not determine whether any dashboard or alert rule consumes `LastSeen` for a staleness decision.** If one
  does, the consequence sharpens: a dead sensor reads **fresh** right after its last alarm and stays plausible, or
  reads **stale** for a host that never alarmed at all. That is the question to answer before changing anything, and
  it is not answerable from `internal/central` alone — it depends on what the fleet actually configures.
- **No fix applied.** A real `LastIngest` touches ingest state, the store schema, and both backends.

## Options — listed, not picked

1. **Record ingest time per agent key** (in the store, beside the baseline) and expose it separately. The honest
   version: *seen at* and *alarmed at* are different columns and both get shown.
2. **Rename the field** to `LastAlert` and let the console say so. Cheapest, zero data change, and it removes the
   wrong reading without claiming a liveness guarantee the tool does not have. Fixes the label, not the blindness.
3. **Ingest returns and stores a timestamp on every call**, healthy or not — makes liveness real, costs a write per
   check-in on every host, which at fleet scale is a design question about the store, not a line of code.
4. Leave it and document it. **Argue against** adding a staleness alert on top of `LastSeen` — that would build an
   alarm on the last-alarm time and produce a monitor that only notices silence in hosts that once made noise.

## What I am NOT claiming

- **Not** that operators have been misled in practice — I have no evidence anyone has acted on this column.
- **Not** that `LastSeen` is wrong as implemented.
- **Not** that option 1 or 2 is right. Whether a silent healthy host should show as fresh is an operator preference,
  and guessing it on a heartbeat is how a tool acquires a policy nobody chose.

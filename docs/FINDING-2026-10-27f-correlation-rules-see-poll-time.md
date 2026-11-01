# FINDING 2026-10-27f — the correlation rules see poll time, not event time. The fix is a schema gap, not a bug.

**Status:** OPEN — scoped. Not a code defect in the line I first blamed.
**Found:** beat 625.
**Instrument:** `scripts/prioreventprobe.py`.

## What I thought I found (and was wrong about)

`internal/telemetry/engine.go:89` records every action into the timeline stamped with the **poll time**:

```go
e.timeline.Record(e.host.ID, asOf, ev.Action.Type, asOf)
```

`Timeline.Record(host, at, class, asOf)` uses `at` as when-it-happened and `asOf` only for eviction, so
passing `asOf` twice means **every action in a batch is stamped "now"**. Measured (`prioreventprobe`
modes 2–3): a shell command that ran **40 minutes ago** satisfies `carbonato.secret.read_after_shell`'s
**2-minute** window. Same action, same rule, opposite verdicts depending only on which stamp it gets.

That much is real and reproduced.

## Why the obvious fix is impossible

I was about to file "pass `ev.Action.TS` instead of `asOf`". `schema.Action` (`schema.go:66-69`) is:

```go
type Action struct {
	Type   string         `json:"type"`
	Detail map[string]any `json:"detail,omitempty"`
}
```

**There is no timestamp on `Action`.** Nor on `telemetry.Event` (`engine.go:29-32` — just `Action` and
`Actor`). So there is nothing to pass. The current line is not a mistake someone made; it is the only
thing the type allows, and `Now` is documented as "the clock used to stamp actions."

Which means the defect is real but its home is the **schema**, not this call site:

- The telemetry layer cannot represent *when* an action happened.
- Every `prior_event` window is therefore measured against poll time.
- On a fast poll tick the error is small and the rules look fine.
- On a slow tick, a backlog, or a replay of buffered events, the error is the whole batch duration.

## Scope — three CRITICAL rules depend on this

From the shipped pack (`prioreventprobe` mode 4):

| rule | severity | window |
|---|---|---|
| `carbonato.exfil.egress_after_identity_change` | critical | `identity.drift` within 5m |
| `carbonato.secret.read_after_shell` | critical | `process.exec` within 2m |
| `carbonato.container.after_identity_change` | critical | `identity.drift` within 10m |

`ObserveEvent` (timeline.go:146) is the other path and *does* use `ev.TS` — so `identity.drift`
observations carry real times while `process.exec` observations carry poll times. **The two halves of a
correlation are stamped by different clocks.** That asymmetry is worse than either choice alone:
`egress_after_identity_change` compares a real-timestamped drift against a poll-timestamped exec.

## What this is not

- **Not a regression I can date.** No `.git` since the 10-08 migration; I cannot tell whether this was
  always so or changed during the churn. Recording the uncertainty rather than implying a cause.
- **Not fixable in the pack.** No glob or predicate spelling reaches the missing field.
- **Not a reason to change the line today.** Passing a timestamp that doesn't exist would not compile;
  the fix is to add `TS` to `schema.Action` (and to `telemetry.Event`), have each `Source` populate it
  from its own record, and then pass it. That is a cross-layer change — telemetry, schema, and every
  source adapter — and it needs a ruling.

## The honest ask

Three options, all Dad's call:

1. **Add `TS` to `schema.Action`,** populate it in every source, pass it in `engine.go:89`. Correct, and
   the largest change.
2. **State the limit in the rule docs:** correlation windows are poll-time approximate. Cheap, honest,
   and leaves three CRITICAL rules weaker than they read.
3. **Drop the correlation rules** from the shipped pack until (1) lands, so nothing claims a precision
   it does not have.

Option 2 is the minimum; option 1 is the one that makes the rules mean what they say. What I will not do
is leave three CRITICAL rules documented as correlating two events "within 2 minutes" when the clock
underneath them cannot tell 40 minutes from 40 seconds.

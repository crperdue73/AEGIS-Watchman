# FINDING 2026-10-29e — the correlation window is measured on the poll clock, not the event clock

**Status:** Instrument added (`scripts/clockfidelity.py`, 6 rows + 4 source facts, mutation-proven). **Design limit, not a defect I may fix alone** — correcting it needs a `telemetry.Event` change.
**Instrument:** `scripts/clockfidelity.py` · **Beat:** 655

## The two clocks

Beats 653 and 654 asked whether a rule's *value* reaches a field. Neither asked what the *clock* delivers, and
the clock is the one input no fixture has ever varied.

```go
// internal/telemetry/engine.go:85-91
asOf := e.now()                                   // <- the poll tick
for _, ev := range events {
    e.timeline.Record(e.host.ID, asOf, ev.Action.Type, asOf)   // both stamps = asOf
    matched := e.engine.Match(..., asOf)
}

// internal/rules/timeline.go:152-159   (ObserveEvent — the drift half)
at := ev.TS
if at.IsZero() { at = asOf }
t.Record(host, at, classOf(ev.Event), asOf)
```

**The drift half is stamped with the time the file changed. The action half is stamped with the time the
collector polled.** `telemetry.Event` (`engine.go:30`) is `{Action, Actor}` — it carries no timestamp at all,
so poll time is the only time available. Default report interval is 30s (`config.go:143`).

## What that does to `within: 5m`

Measured, at several poll intervals (every number in the table is computed by the instrument, not asserted):

| case | real gap | measured | error |
|---|---|---|---|
| control, inside | 120s | 120s | 0 |
| control, outside | 540s | 540s | 0 |
| real 4m50s, 60s tick | 290s | 300s | **+10s** |
| real 5m20s, 30m tick | 320s | 1800s | **+1480s** |
| real 1m, 60m tick | 60s | 3600s | **+3540s** |
| egress 10s BEFORE the change | −10s | −10s | 0 |

Two consequences:

1. **The window has poll-interval granularity, not nanosecond granularity.** Beat 653's `windowfixture.py` tests
   `Timeline.Seen` at nanosecond precision and is *correct to do so* — but the precision it tests is not the
   precision production delivers. Both statements are true and neither cancels the other. This is the first time
   I have found a case where a fixture of mine was right and still measured something the system never sees.
2. **A poll interval larger than the rule window makes the rule un-fireable.** At a 60m tick, a genuine 1-minute
   correlation inside a 5m window is silent. The 30s default makes this unlikely; a misconfigured interval makes
   it automatic, and **nothing warns.** `config.go` validates `Watch.PollInterval > 0` but never compares
   `Report.Interval` to the largest `within` in the pack.

And the case that reads worst: with both halves landing in one tick, **the order is erased.** In row 6 the egress
came 10 seconds *before* the identity change, and the drift keeps its true TS (10s) while the action takes the
tick (30s) — so `carbonato.exfil.egress_after_identity_change` sees a drift inside its window and fires. The rule
asks whether an egress came *after* a change. In that batch, it did not.

## Claim / not claimed

**Claimed:** actions are stamped with poll time; the documented `within` therefore carries an error bounded by
the poll interval; a poll interval exceeding the window silently disables the rule; order within a batch is
erased.

**Not claimed:** that stamping with poll time is wrong *given the current source interface*. `telemetry.Event`
has no TS field, so there is no true time to stamp with. This is a design limit the rule docs do not state,
which is why it is written down rather than patched. Fixing it properly is a source/schema change — Dad's call.

## The mistake I made building this, since it is the same mistake twice

My first row set asserted what I *expected* — that a 5m20s gap in a 5m window would **fire**, because the 30m
tick put both stamps close together. It printed FAIL. I patched the expectation to `False`.

That patch was right, and the reason matters: I had derived `declared_fires` from the **real gap** while the
engine derives it from the **stamps**. Deriving the expected answer from the thing under test is exactly the
defect this file exists to name (`anyArgvMatches` handing the engine its own answer, beat 650). So the rows now
say it out loud: *declared is what `Seen` must conclude given the stamps, never what the real gap implies.*
Two rows still disagree with the real-world truth, and both are labelled as such rather than quietly "fixed".

Row 4 is the honest one: real 5m20s → silent, and **the verdict is correct for the wrong reason** (it would also
be silent at a true 4m). A right answer from a measurement off by 1480s is not evidence the window works.

## Mutation proof

| mutation | result |
|---|---|
| `Record(... asOf.Add(-90s))` in engine.go | **FAIL**, names "actions are recorded at asOf" · rc=1 |
| `at := ev.TS` → `at := asOf` in timeline.go | **FAIL**, names "drift is recorded at ev.TS" · rc=1 |
| control | 6 rows, 4 source facts, 0 failures · rc=0 |

## The next honest step (for when Dad is deciding)

Add one validation at load: **refuse a pack whose largest `prior_event.within` is smaller than the configured
`report.interval`.** It costs a comparison, needs no wire change, and turns "the rule can never fire" into a
startup error — the same philosophy as `Validate()` rejecting malformed globs. Until then, the finding stands as
a documented limit.

## Gate

**68 suites / 1463 checks / 0 failures.** Baseline rewritten (`clockfidelity.py 11`).

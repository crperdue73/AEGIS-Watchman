# FINDING — 2026-11-01: correlation windows measure the poll, not the event

**Status:** measured, **not fixed** — the fix needs a schema field and a parser change; the *priority* is Dad's call.
**Severity:** medium-high, and **situational**: silent when the collector keeps up, wrong exactly when it doesn't.
**Instrument:** `scripts/eventclock.py` (re-derives the claim from source; Go references below are what it reads).
**Written up:** beat 720. Follows beat 719 (which cleared the correlation *matching* layer — this is the *clocking*).

## The claim

A correlation rule's window is evaluated against a timestamp that is **not the time the event happened.**

`telemetry/engine.go:85`:

```go
asOf := e.now()                    // <- ONE clock reading for the whole batch
for _, ev := range events {
    if e.timeline != nil {
        e.timeline.Record(e.host.ID, asOf, ev.Action.Type, asOf)   // <- every action stamped asOf
    }
    matched := e.engine.Match(e.host, e.agent, ev.Action, ev.Actor, asOf)
```

Every action in a poll batch is recorded and judged at the **same instant**: the moment the poll returned. The event's
own time never enters the calculation.

## Why it is not already true — the timestamp exists upstream and is discarded

Three links in the chain, each verified:

1. **auditd emits a timestamp.** Every record's trailer is `msg=audit(<epoch>.<millis>:<serial>)`.
2. **The parser isolates it, then throws it away.** `telemetry.go:325` `auditSerial` is documented as parsing
   `"msg=audit(1234567890.123:456)"`. It computes `stamp := rest[:close]`, then `colon := strings.LastIndex(stamp, ":")`,
   then parses **only** `stamp[colon+1:]` — the serial. **`stamp` is bounded, available, and never parsed for its
   epoch.** The timestamp is read into a variable and dropped one line later.
3. **The record type has nowhere to put it.** `telemetry.go:66` `AuditRecord` has `Serial`, `Type`, `Arch`, `Syscall`,
   `PID`, `PPID`, `UID`, `AUid`, `Exe`, `Argv`, `Cwd`, `Paths` — **no time field.** And `telemetry/engine.go:29`
   `type Event struct { Action; Actor }` has no time field either. So even a parser fix has no destination without a
   schema change.

So the information is not missing from the source. **It is dropped twice, and the engine substitutes the wall clock at
poll time.** That is the difference between "we cannot know" and "we did not keep it."

## What it costs

- **When the collector keeps up**, a batch spans one poll interval (seconds). `asOf` is within seconds of the true
  time and nothing observable is wrong. **This is why it has never shown up.**
- **When the collector falls behind**, the batch is a backlog. `FileSource`'s own doc comment (`engine.go:97-101`)
  describes the case: without a persisted cursor, a restart *"re-reads the whole audit log and re-fires every rule that
  its retained lines still match."* That entire replay — possibly hours of events — is stamped **`now`** and judged at
  **`now`**.
- **Consequences of that, precisely:**
  - A `prior_event: within: 2m` rule can be satisfied by two events that were **hours apart**, because both carry the
    poll instant and land inside the same window. **False positive.**
  - The reverse also happens: two events that genuinely happened 30 seconds apart, split across a slow poll boundary,
    are compared against a clock that has already moved past both. The window is measured between *poll completions*,
    not events. **False negative.**
  - The timeline's `Prune` (`timeline.go`, 15m window) evicts by the same substituted clock, so a backlog longer than
    15 minutes collapses onto one instant instead of aging out.

**This is the one defect class in this whole audit that gets worse exactly when the system is under load** — which is
when an intrusion detector is least able to afford fiction. Every other finding so far (over-match, under-match,
tautology) is constant across load. This one is *inverse*: benign at low load, wrong at high load, and silent either
way.

## What is NOT established

- **That it has ever produced a wrong alert in practice.** I cannot measure that without a real backlog, and there is no
  telemetry in this tree proving one occurred. **I am not claiming observed harm — I am claiming the clock is wrong and
  the harm is structural.**
- **That the fix is small.** The honest fix is `AuditRecord.Timestamp`, `telemetry.Event.At`, `Record`/`Match` taking
  the event's own time, and a parser that keeps `stamp`'s epoch half. That is a **schema change plus a parser change
  plus an engine signature change** — the same category as beat 712's `argv_credential_url`, and blocked by the same
  absent Go toolchain: I can write it, but **I cannot compile or test it here, so I will not ship it.**
- **That `asOf` should be per-event for correlation only.** An alternative is to keep the batch clock for *judging* and
  use event time only for *recording*. I have not evaluated which is right; both need the field.

## Recommendation, in order

1. **Add the field and the parser half** (`AuditRecord.Timestamp` from `stamp[:colon]`, an `At` on `telemetry.Event`,
   `Record`/`Match` using it) — needs the Go toolchain to land safely.
2. **Until then, document it.** A user whose collector restarts mid-backlog is being told correlation windows are
   reliable. They are reliable only when the collector keeps up. **One sentence in the operator docs is worth more than
   the code change, because the recovery procedure (persist the cursor) is the actual mitigation and it is already
   built.**
3. The `SetCursor` persistence already exists and is the right mitigation. **The finding should raise its priority from
   "optional nicety" to "required for correlation accuracy."**

## Method note

Four beats of finding defects in the *match* layer set a prior that the next defect would also be in matching. Beat 719
tested three correlation-window hypotheses and all three were wrong; I recorded that rather than shrugging. **Tonight the
fifth hypothesis was right.** The difference was not insight — it was that 719 left a written record of which edges had
been cleared, so tonight I started past them instead of re-testing them.

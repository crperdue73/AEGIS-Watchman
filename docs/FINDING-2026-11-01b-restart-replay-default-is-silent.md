# FINDING — 2026-11-01b: the restart-replay default is silent, and it is now also a correlation error

**Status:** measured, **not fixed** — the doc gap is real but the honest fix is a warning at startup, and **shipping
untested Go on a heartbeat is the failure this project exists to catch.** Recommendation below is one line of config
validation.
**Severity:** medium (noise + duplicate alerts); **compounds to high** when combined with beat 720.
**Instrument:** `scripts/replaywarn.py` (re-derives the whole chain from source every run).
**Written up:** beat 721.

## The claim

A collector configured with `audit_log` but **no `spool` and no `cursor`** does not persist its read offset. On every
restart it re-reads the **entire audit log from byte 0** and re-evaluates every rule against it.

The source says so plainly in three places, and each one is a *comment*, not a check:

1. `telemetry/engine.go:97-103` (type doc): *"without that, a restart re-reads the whole audit log and re-fires every
   rule that its retained lines still match. For a long audit log that is a flood of duplicate alerts, so persistence
   is the difference between 'at-least-once' and 'once-per-restart-and-growing'."*
2. `collector/behavior.go:295-301` `cursorPath`: returns `cfg.Behavior.Cursor`, else `cfg.Behavior.Spool+".cursor"`,
   **else `""`** — empty means `SetCursor` is never called, and `saveCursor` is a no-op.
3. `docs/collector-config.md:87` tells the operator: *"Empty → derived as `<spool>.cursor` when a spool is set,
   **else not persisted**."*

**So the behaviour is documented, deliberate, and unguarded.** `spool` has no default (`docs/collector-config.md:85`:
"— … Empty → best-effort ship, dropped on failure"). Therefore **the zero-config collector — the one a new user gets by
only pointing Watchman at an audit log — has the replay behaviour.** It is the default, not an edge case.

## What it costs, in order of how bad it is

**(a) Duplicate alerts on every restart.** The comment's own words. Not a bug — a decision — but a decision whose
consequence lands on the user, who reads nothing about it in the config table beyond "else not persisted."

**(b) Monotonically growing repeat alerts.** "once-per-restart-and-growing": each restart replays a longer log. The
count is not bounded by anything except how long the log has been retained.

**(c) It is now ALSO a correlation error — this is new, and it is beat 720's fault.** Beat 720 proved
`telemetry/engine.go:85` stamps every event in a batch with one `asOf := e.now()` and never reads the event's own time.
Put (a) and 720 together: **the replay is not just duplicated, it is duplicated at the wrong time.** A twelve-hour-old
`process.exec` from the replay is recorded into the timeline at `now`, so a `prior_event: within: 2m` rule sees it as
"two minutes ago." **The replay can manufacture correlations that never happened.**

Before beat 720 this finding would have been "noisy." It is now "noisy and can produce false positives that are
indistinguishable from real ones." **Two findings multiplied rather than added** — and neither alone would have told me
that. Worth recording explicitly: I did not find this by looking for it, I found it by following the cursor path *after*
already knowing the clock was wrong.

## What is NOT established

- **That any real deployment runs without a cursor.** The deploy doc (`docs/deploy-collector.md:277,293`) recommends
  `/var/lib/watchman` for spool/cursor/scope-seen "missing config falls back to defaults" — so a *deployed* install
  probably has a spool. **The exposure is the config-table user and the dev/test path**, which is exactly the user who
  will file the confusing duplicate-alert bug.
- **That a startup warning is the right fix.** It is the cheapest one I can describe; refusing to start is too strong
  (the comment at `behavior.go` is explicit that degraded operation beats refusing to run). Dad's call.
- **Whether `os.Stat`-based "is this log already long?" gating is wanted.** Over-engineering; mentioned to be rejected.

## Recommendation

1. **Log one line at startup** when `cursorPath(cfg) == ""` and `Behavior.AuditLog != ""`: *"audit cursor not persisted
   (set behavior.cursor or behavior.spool) — a restart will replay the whole log and re-fire rules; correlation windows
   will be evaluated at replay time, not event time."* That sentence is the whole finding in one place an operator
   actually reads.
2. **Add the consequence to the config table**, not just the mechanism. "Not persisted" is a fact; "duplicate alerts on
   restart" is what the operator is choosing between.
3. **Do the same for `scope.seen`** (`behavior.go:309-316`, identical derivation, same empty-string fallback) — I have
   not audited what a non-persisted scope-seen costs, and I am not claiming it is harmless.

## Method note

I set out this beat to fix the *parser* half of beat 720 (keep the epoch) and could not: no Go toolchain. So I went after
the mitigation instead — persisting the cursor — and found it is already built and simply **not default-on**. Following
that path is what produced (c). **The finding I got was not the finding I was looking for, and it was better.**

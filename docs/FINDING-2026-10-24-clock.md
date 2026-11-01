# FINDING — 2026-10-24 — the two halves of a correlation are on different clocks

**Status:** measured, no pack change. `rules/` and `internal/` untouched.
**Instrument:** `scripts/clockprobe.py` · **Test:** `scripts/clockprobe_test.py` (47 checks, green)
**Beat:** 538, written 01:55 ET Saturday

## The structural fact

`Action` (`internal/schema/schema.go`) is **`Type` and `Detail`, and nothing else.** There is no time field.
`Event` has `TS`, but that belongs to the envelope a *file/drift* event rides in — an action is not an envelope.
So the two halves of a temporal rule arrive by different routes and are timestamped differently:

| half | path | timestamp used |
|---|---|---|
| `identity.drift` | `ObserveDrift` → `Timeline.ObserveEvent` | **`ev.TS`** — set by `NewEnvelope` at construction |
| `net.egress` / `process.exec` | `Engine.Matches` | **`asOf := e.now()`** — one read per poll batch |

Both are the collector's wall clock, so this is not a timezone or host-skew problem. It is one envelope's
per-event timestamp on one side and a poll-granular clock on the other.

## Cost 1 — the drift precision buys nothing

`ev.TS` is precise to the envelope. The action side is quantised to `Report.Interval` (30s, see
`FINDING-2026-10-23-batch.md`). A correlation can only resolve as finely as **its coarser half**, so
`egress_after_identity_change` resolves to 30s regardless of how precisely the drift was stamped.
**30s / 0.001s = four orders of magnitude of precision spent for no gain.** Not a bug — an asymmetry nobody
designed, which makes the drift-side precision look like a guarantee it does not deliver.

## Cost 2 — a drift event stamped ahead of its own poll is invisible

`Seen` requires `!o.At.After(asOf)`. Normally the envelope is built *before* the poll that ships it, so
`at < asOf` and the event is visible. But if the wall clock steps **backwards** between envelope construction and
the poll — NTP slew, VM suspend/resume, a manual set — then `at > asOf` and the event is filed and then
**immediately unwatchable by the rule waiting for it.** No error. The rule simply does not fire.

I want to be precise about how serious this is: **it needs a clock-discipline failure, not routine operation.** I
checked the ordering rather than assuming it, and the normal case is safe. What makes it worth writing down is
that the failure is silent and the strictness is one-sided — being a microsecond early is as fatal as being an
hour early.

## What this does NOT claim

- Not a clock-skew *between hosts* finding. Both clocks are the same host's.
- Not that actions are mis-timestamped in normal operation. They are quantised, which is §batch's finding.
- Not a proposed `internal/` change. Adding `TS` to `Action` is a **public wire-format change** — `schema.go`'s
  header says as much ("This is the PUBLIC CONTRACT… renames are not [fine]"). Additive optional fields are
  permitted, and a `ts` on `Action` would close the asymmetry, but that is a schema-version decision with fleet
  consequences, and it is not mine.

## Two things I fixed in my own instrument first

**Three assertions passed unconditionally.** Written as `ok(..., True)`. They'd have printed `ok` in any
world, including one where the finding was void — the exact "clean 0/0 is not a pass" trap, in my own file, on
the same night I'd been quoting it. Replaced with a modelled `seen()` truth table and real arithmetic.

**My struct reader ran past the declaration.** `expr(src, "type Action struct")` grabs 1200 characters, which in
this file sweeps through `Action`, `Rule`, `Actor` and into `NewEnvelope` — so the "Action has exactly Type and
Detail" check read eleven field names and failed. That red was the *test* being right and the *reader* being
wrong, for the fifth time this week (span-based reading instead of brace-aware). Replaced with `struct_block()`,
which walks braces to a matching close.

## Reproduce

```bash
python3 scripts/clockprobe.py        # rc 0; asserts the asymmetry against source, models Seen
python3 scripts/clockprobe_test.py   # 47 checks incl. negatives that would void the finding
```

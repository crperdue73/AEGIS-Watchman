# FINDING 2026-10-29p — the class vocabulary cannot be exported from `schema`, and my beat-665 fix said it could

**Status:** open — corrects my own beat-665 recommendation (which was wrong). No code change made (no Go toolchain on this host).
**Instrument:** `scripts/classvocab.py` (rows added beat 666) · **Beat:** 666 · **Parent:** FINDING-2026-10-29o
Measured with pure Python over the source tree (no Go needed) — see "Measured, not assumed" below.
Parent finding: `docs/FINDING-2026-10-29o-prior-event-class-has-no-vocabulary.md`

---

## The claim I made last beat

Beat 665 recommended: *"export the class vocabulary from the code that produces it, and have Validate reject a
`prior_event.class` not in it."*

That reads as one obvious move: `schema` already exports `EventIdentityDrift`, `EventProvenanceUntrusted`,
`EventBehaviorRuleMatch`. Export the slice, import it in `internal/rules`, done.

## What I actually read this beat

The two places a class token can come from — **and they are different types**:

| Path | Token | Produced by | Codified as |
|---|---|---|---|
| schema event envelope | `identity.drift`, `provenance.untrusted`, `behavior.rule_match` | `schema.NewEnvelope(...)`, called from `collector/drift.go:38,55,76` and `provenance/events.go:19` | **constants** — `internal/schema/schema.go:14-18` |
| action stream | `process.exec`, `net.egress`, … | `ev.Action.Type` (`telemetry/engine.go:89`) | **literals** — `telemetry.ProcessExec` (`telemetry.go:37`) is one; the rest exist only as string literals in dispatch tables (`telemetry.go:176`, `collector/behavior.go:249`) |

Then `timeline.go:141`:

```go
func classOf(ev string) string { return strings.TrimSpace(ev) }
```

**`classOf` is a pass-through applied to two disjoint vocabularies.** `Seen` compares with `!=` against a token
that may have arrived from either door.

## Why "export the vocabulary from schema" is wrong as stated

- `schema` holds **only half** the namespace. `process.exec` appears nowhere in it — it lives in `telemetry`.
- So a `Validate` check built from `schema`'s constants alone **would reject the shipped pack**: `rules/carbonato.yaml:225`
  uses `class: process.exec`, which is legitimate and live.
- The "fix" I named last beat, applied literally, is a **startup failure on the current pack** — a false positive
  in the strictest possible place (load time), which is how an operator concludes the validator is broken and
  loosens it.

**The vocabulary is not "in the code that produces it." It is split across two packages that both feed one
comparison, and neither one alone is complete.** That is the actual shape of the defect, and it is different from
the one I wrote down yesterday.

## What survives from beat 665

Everything measured there still holds:
- `classOf` is `TrimSpace` and nothing else (pass-through).
- `Validate` requires a class to be **present** but never checks it against anything.
- `identity.drif` / `Identity.Drift` / `identity-drift` / `identity.drift ` all load clean and can never fire.
- No registry exists — and now I know *why*: there is no single package that could own one without either
  importing the other or duplicating literals.

## The corrected recommendation (still Dad's call)

The registry has to be a **single owned list covering both doors**, not a re-export of `schema`'s constants.
Three viable shapes, in order of how much public surface they disturb:

1. **`schema.KnownClasses` = schema constants + `telemetry` action types declared in `schema`** (accepting that
   `schema` gains knowledge of action type names — it already documents them, `schema.go:67`).
2. **A new tiny package `internal/classvocab`** owning the namespaced list, imported by `timeline`, `rules`, and
   `schema`'s callers. Cleanest layering; most files touched.
3. **Keep the whole check in `rules`** — a literal `knownClasses` slice validated at `Validate()` time, marked in
   a comment as *deliberately duplicated* because the alternative is an import cycle.

Either way, `Validate` must reject a class not in the list, and the list must be **re-verified against the pack**
so adding it cannot break the shipped rules — that's the guard beat 665 proved (the instrument row goes RED on a
pack typo).

## Measured, not assumed

Run this beat over the tree, no Go required:

- `schema` class constants: `['identity.drift', 'provenance.untrusted', 'behavior.rule_match']`.
- Pack `prior_event.class` values: `identity.drift` ×2, `process.exec` ×1.
- `process.exec` appears in `schema.go` **only inside a struct-tag comment** (`schema.go:67`) — not as a constant.
  It *is* a real constant in `internal/telemetry` (`telemetry.go:37`, `ProcessExec`) and is emitted as an action
  type at `telemetry/engine.go:89`.
- Therefore: **a `Validate` built from `schema`'s constants alone rejects `process.exec`** — one of the three
  shipped correlation rules fails to load. Adding `process.exec` to the accepted set → all three pass.

So the correction is not a prediction. It is the measured consequence of the fix I recommended yesterday.

## What I did not do

I did not implement any of the three. No Go toolchain on this host (`go`, `gofmt` absent) — I cannot compile,
cannot run `make test`, and cannot demonstrate the fix. Writing a Go change I cannot build is exactly the
"asserting readiness without running the gate" failure this project exists to catch. It goes to Dad as a
**decision with a corrected shape**, not as a patch.

## The lesson

A finding can be true about the defect and wrong about the fix. Yesterday I measured the hole correctly and then
named a repair that would have broken the pack on startup — caught only because the next question I asked was
*"which package actually produces each of the two token families?"* instead of *"what looks like the obvious
source?"* **A recommendation is a claim too, and it needs the same evidence as a measurement.**

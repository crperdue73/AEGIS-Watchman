# FINDING 2026-10-29o — `prior_event.class` is the one predicate with no vocabulary, no registry, and no check

**Status:** Instrument added (`scripts/classvocab.py`). **LATENT, not live** — see the claim box before the diagnosis.
**Instrument:** `scripts/classvocab.py` · **Beat:** 665

## First, what is already excellent

`ParsePack` sets `dec.KnownFields(true)` (rule.go:130), so an unknown *field* is a hard error. `Validate`
(rule.go:245-315) rejects: no id, an un-namespaced id, missing/invalid severity, an empty match block, empty detail
keys, empty glob lists, malformed globs, a `prior_event` without a class, and `prior_event.within <= 0`.

The file's own comment names the exact failure this prevents:

> *"a rule that loads but can never fire (or silently drops a predicate and under-matches) is the worst failure mode for
> a detection tool, because the operator believes they are covered."*

That is exactly right, and it is why this finding is worth writing: **one predicate escaped that discipline.**

## The hole

`prior_event.class` is a **free-form string**. It is compared with `!=` (timeline.go:98) against a token produced by

```go
func classOf(ev string) string { return strings.TrimSpace(ev) }   // timeline.go:140
```

— a pass-through. The vocabulary of class names exists only as literals scattered across a YAML pack and a few Go call
sites. **Nothing enumerates it, nothing validates against it**, and there is no error at load *or* at runtime for a
class no emitter ever produces.

## Measured: four misspellings that load clean and are silent forever

`Validate` requires a class to be *present* — it never asks whether it is *real.*

| `prior_event.class` | mistake | loads | can fire |
|---|---|---|---|
| `identity.drif` | one dropped character | yes | **never** |
| `identity.drift ` | trailing space | yes | **never** |
| `Identity.Drift` | capitalised | yes | **never** |
| `identity-drift` | hyphen for dot | yes | **never** |

Every other comparable mistake in this pack is a startup error. This one is silence — and because a correlation rule
fires *later*, on a second event, the operator's only evidence is that the alert never arrives: **the same empty screen
as a quiet host.** That is beat 664's finding arriving through a different door.

## ⚠️ Claim / not claimed — this is LATENT

**Claimed:** class is free-form; `classOf` is `TrimSpace`; `Seen` compares with `!=`; `Validate` has no class check; no
registry exists in any of the 6 non-test files in `internal/rules`.

**Not claimed — and measured the other way:** I checked the pack. Its three `prior_event.class` values
(`identity.drift` ×2, `process.exec` ×1) all refer to classes the source also names. **I did not find a broken rule.**
This is a latent defect: nothing would have stopped one, and the check that exists to stop exactly this has a hole
shaped like the class vocabulary.

The instrument's last row is what keeps me honest here, and it is the one I mutants-tested hardest: mutate a pack class
to `identity.drif` and the gate goes **red** — so "latent" is a measurement I can re-verify, not a hope. No pack was
edited in the shipped tree; the mutation was reverted.

## Fix (Dad's call)

Have the code that *produces* classes export the vocabulary — one slice, next to `classOf`, where the two cannot drift
apart — and have `Validate` reject a `prior_event.class` not in it. That is the same strictness `KnownFields` already
gives every other field, applied to the one field that never got it. It costs a slice and one comparison, and it closes
the gap in `Validate`'s own stated purpose.

Migration note: this makes a pack that uses a class from a *newer* binary fail to load — which is precisely the
forward-safe behaviour `ParsePack`'s comment already commits to for predicates.

## Mutation proof

| mutation | result |
|---|---|
| pack `class: identity.drift` → `identity.drif` | **FAIL**, "every pack class is a class the source also names" · rc=1 |
| add a `knownClasses[pe.Class]` guard in `Validate` (the proposed fix) | **FAIL** ×2 ("no class vocabulary check", "registry exists") · rc=1 |
| control | 14 rows green · rc=0 |

## Gate

**78 suites / 1570 checks / 0 failures.** Baseline rewritten (`classvocab.py 14`).

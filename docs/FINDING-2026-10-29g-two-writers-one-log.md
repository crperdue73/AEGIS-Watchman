# FINDING 2026-10-29g — the timeline has two writers on two clocks, ordered by a call sequence no test pins

**Status:** Instrument added (`scripts/timelineprecedence.py`). **No behaviour change proposed** — the code is correct. The finding is that its correctness rests on an accident.
**Instrument:** `scripts/timelineprecedence.py` · **Beat:** 657

## The composition nobody wrote down

Beats 653/655/656 fixed the correlation *windows*, the *clock* those windows run on, and the *memory* they run
against. All three treated the timeline as a well-ordered log. It is not. It is a flat append-only slice with **two
writers owned by different packages**, and neither knows the other exists:

| | writer | stamps `at` with | asOf |
|---|---|---|---|
| **A** | `internal/telemetry/engine.go:89` (`package telemetry`) | `asOf` — the **poll tick** | poll tick |
| **B** | `internal/rules/timeline.go:158` via `ObserveDrift` (`package rules`) | `ev.TS` — the **true event time** | one shared `now` per drift batch |

`Record` (timeline.go:66) takes a pre-computed `at`, appends it, and **does not sort**. So the slice's order is
exactly the call order in `cmd/collector/main.go`: `ObserveDrift(res.Events)` on line 144, `behavior.Poll()` on
line 149. Drifts land first, actions second.

**That ordering is correct, and it is an accident.** Nothing in `Record`'s contract, in the `Timeline` doc comment,
or in any test states "drift before action". Move one call and the log silently becomes misordered.

## And nothing would break loudly, because `Seen` is order-blind

`Seen` is a linear scan over `t.byHost[host]` that tests `o.At` against a cutoff. It never reads the slice order. So
a misordered slice still gives correct answers **as long as every `at` is on a comparable clock** — and the two
writers hand-write different ones.

Within a tick that is harmless and useful: the drift carries the time it was created (mid-cycle), the action carries
the tick boundary, so the drift appears to precede the actions sharing its tick. Usually true. Never checked.

Measured, on the transcribed composition:

| case | asOf | drift at | fired | reading |
|---|---|---|---|---|
| same-tick, drift mid-cycle | 30s | 12s | yes | correct — and due to call order alone |
| drift exactly on tick | 30s | 30s | yes | control: half-open interval |
| drift exactly on window edge | 300s | 0s | **no** | beat 656's collision: exclusive cutoff at 0 |
| drift 301s old | 301s | 0s | no | correctly silent — paired with the row above so the pair pins the boundary, not one side |

## Claim / not claimed

**Claimed:** two writers, two clocks, one `asOf` source per drift batch (`behavior.go:375` builds `now := time.Now()`
once for the whole batch); append order established solely by `main.go` call order; **no test asserts that order**;
`Record` does not sort, so the slice is order-dependent even though `Seen` is order-blind.

**Not claimed:** that either writer is wrong in isolation. Both are defensible. The composition is unstated and
untested — that is the whole finding. **Fix proposed: a comment on `Record` naming the invariant, plus one test
asserting drift-precedes-action. Not a behaviour change, and not one I land unilaterally.**

## The mistake, because I have now made it twice in three beats

My first mutation test of the call-order check **did nothing at all.** My `sed` used three tabs before
`behavior.Poll()`; the real indentation is two. The pattern matched zero lines, `sed` exited 0, the source was
unchanged, and the check "passed" — so I briefly concluded the check must be too lenient.

I almost recorded "my check doesn't catch reordering" as a finding about the check. **It was a finding about my
mutation.** Same species as beat 652 (an explanatory comment executed as argv) and beat 655 (expected value derived
from the thing under test): I trusted a tool's silence instead of verifying it changed something.

Hardened: mutants here `assert count == 1` before replacing, and print the resulting call order. A mutation that
does not change the bytes is not a control — **and an unverified mutant and a passing test look identical.**

## Mutation proof

| mutation | result |
|---|---|
| swap `ObserveDrift` after `Poll` in main.go (count-asserted) | **FAIL**, names the call-order invariant · rc=1 |
| insert a `sort.Slice` in `Record` | **FAIL**, names "Record appends (does not sort)" · rc=1 |
| control | 4 composition rows + 7 source facts green · rc=0 |

## Gate

**69 suites / 1486 checks / 0 failures.** Baseline rewritten (`timelineprecedence.py 12`).

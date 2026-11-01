# FINDING 2026-10-29h — "ever seen" is not expressible, and the reap boundary is a third clock

**Status:** Instrument added (`scripts/prunereap.py`), mutation-proven twice. **No defect** — a vocabulary limit that nothing states, plus a corrected claim of my own.
**Instrument:** `scripts/prunereap.py` · **Beat:** 658

## The correction first, because it is the most useful thing here

My first draft of this finding claimed: *"`within: 0` is accepted by `Seen`, by `temporalMatches`, and by the JSON
schema, so its retention is silently the 15-minute `Prune` window."*

**That is false.** `internal/rules/rule.go:296` rejects it at load:

```go
if pe := r.Match.PriorEvent; pe != nil {
    if pe.Within <= 0 {
        return fmt.Errorf("rule %q: prior_event.within must be positive (e.g. 5m)", r.ID)
    }
}
```

Where the false claim came from: `Seen`'s doc comment — *"A within <= 0 means 'any time still
retained'"*. That sentence is true **of the helper** and says nothing about what a **pack** may contain. I read a
helper's tolerance as the vocabulary's capability, then wrote it down as a schema fact without opening `Validate()`.

**Fourth time in five beats** that prose was not code (652: a comment executed as argv; 655: expected value derived
from the thing under test; 657: a mutation that silently changed nothing; 656: a probe that read a comment). The
pattern is now named plainly: **I keep treating a sentence near the code as evidence about the code.**

The correction made the finding *stronger*, not weaker: the true statement is that **"ever seen" is not
expressible at all.** The only retention the vocabulary can express is a positive duration ≤ the timeline window.

## What Prune throws away

```go
// internal/rules/timeline.go:118-136
cutoff := asOf.Add(-t.window)
for host, list := range t.byHost {
    keep := ...o.At.After(cutoff)...
    if len(keep) == 0 { delete(t.byHost, host); continue }
}
// internal/collector/behavior.go:401-403   (top of every Poll, before matching)
b.timeline.Prune(time.Now())
```

| situation | answer to a correlation | why |
|---|---|---|
| class seen 1m ago | yes | retained |
| class seen 14m ago | yes | inside the 15m reap window |
| class seen 16m ago | **no** | reaped — the host proved yes and now reads no |
| class seen exactly 15m ago | **no** | exclusive cutoff, same test as `Seen` |
| host fully reaped | `delete(t.byHost, host)` | **nothing records the host ever existed** |

## The third clock

653 found the poll tick, 657 found the two writers' clocks, and `Prune` brings a **third**: it calls `time.Now()`
fresh at the top of every `Poll`, before any match and independent of the batch's `asOf`.

Measured: one timeline, one rule, two Polls two seconds apart —

```
poll at 899s -> true;   poll 2s later at 901s -> false
```

The reap boundary fell in the gap between them. Nothing is wrong with either answer. But the ground truth for "has
this host ever emitted this class" is not a state — it is **a function of when you asked**, with 15-minute
resolution, decided by a clock that neither the matcher nor the drift writer uses.

## Claim / not claimed

**Claimed:** `within <= 0` is rejected at load, so "ever seen" cannot be requested; a correlation's answer expires on
a reaper's schedule rather than a rule's; `Prune` deletes host entries outright, leaving no record; `Prune` runs on a
third clock so two adjacent polls can disagree.

**Not claimed:** that `Prune` is wrong. Reaping is necessary and its exclusive test is consistent with `Seen`. The
limit is that **the vocabulary implies a memory longer than the design provides and says so nowhere** — a rule named
"ever seen" would be a lie in its own name. Not re-reported: the pack-vs-window size question (beat 656).

## Mutation proof

| mutation | result |
|---|---|
| `pe.Within <= 0` → `pe.Within < 0` in rule.go | **FAIL**, names the Validate guard · rc=1 |
| `delete(t.byHost, host)` → `_ = host` in timeline.go | **FAIL**, names "Prune deletes the host entry outright" · rc=1 |
| control | 5 source facts + 5 reap rows + third-clock row, green · rc=0 |

(The second mutant aborted on my own `assert count == 1` because I guessed three tabs and the line has three — but
the *third* one. It took a `cat -A` to find out that my guess had matched a different line's indentation. That assert
is the only reason I did not report "uncaught mutant" as a finding about the check. **It fired twice today**, and the
first time it fired was on a mutation that would otherwise have silently changed nothing — exactly the beat-657
failure it was written to prevent.)

## Proposal

One sentence in the pack documentation, in the `prior_event` section: *"`within` must be positive and shorter than the
timeline retention (15m); there is no 'ever seen' predicate."* That is documentation, not code — the correct fix for a
correct design that is under-described. Dad's call whether it goes in the rules reference or the brief.

## Gate

**70 suites / 1498 checks / 0 failures.** Baseline rewritten (`prunereap.py 12`).

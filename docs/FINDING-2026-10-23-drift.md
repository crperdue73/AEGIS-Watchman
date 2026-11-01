# FINDING — 2026-10-23 — the three correlated rules are wired; their clock is not what they say

**Status:** clean pass on wiring, one real bound to know. No pack change. `rules/` and `internal/` untouched.
**Instrument:** `scripts/driftprobe.py` · **Test:** `scripts/driftprobe_test.py` (51 checks, green)
**Beat:** 534 · written 21:31 ET

## What I set out to test

Three rules in the pack are temporal — all three `critical`:

| rule | prior class | within |
|---|---|---|
| `carbonato.exfil.egress_after_identity_change` | `identity.drift` | 5m |
| `carbonato.container.after_identity_change` | `identity.drift` | 10m |
| `carbonato.secret.read_after_shell` | `process.exec` | 2m |

`temporalMatches` (`engine.go:83-92`) returns **false** when a rule carries `prior_event` and the timeline has not
seen the class. There is no third state — a correlation rule that never gets its half reports nothing at all.
That is the same silent shape as the eBPF stub and the binds ordering, so it deserved the same scrutiny: does the
file half actually reach the timeline the action half consults?

## The answer: yes, and the wiring is correct

All four gates are present and in the right order:

1. `Drift()` emits `schema.EventIdentityDrift` on all three transitions — changed, added, **removed**
   (`drift.go:36, 54, 76`).
2. `BehaviorEngine.ObserveDrift` feeds each event into the timeline (`behavior.go:377`).
3. `cmd/collector/main.go:144` calls `ObserveDrift(res.Events)` **before** `behavior.Poll()` on the same cycle —
   drift lands in the timeline before actions are judged, which is what makes same-tick correlation possible.
4. Both classes the rules name have a real producer, and `classOf` treats event classes and action types as the
   same kind of token, so `process.exec` in a `prior_event` is legitimate rather than a typo.

No defect here. **Recording that as a pass, because a green gate that is reported is worth more than a suspicion
that is reported.**

## The thing worth knowing: the window is 15m and the widest rule asks for 10m

`DefaultTimelineWindow = 15m`, `DefaultTimelineCap = 256` observations per host. Every rule's `within` fits
inside the window — 5m, 2m, 10m — so no rule is asking for a lookback the timeline has already discarded.

But it is close enough that the pack owner should see the arithmetic rather than discover it: **the 10m rule uses
two thirds of the default window.** If anyone ever sizes the timeline down (`NewTimelineSized` is exported and
configurable), `container.after_identity_change` becomes silently unfireable at, say, an 8m window — and it would
fail the same way as everything else in this week's findings: no alert, no error, no log line.

Worth pinning as a constraint: **timeline window must be ≥ the largest `prior_event.within` in the pack.**
That is an invariant a startup check could enforce in one line, next to the existing `DiffPins` validation. I am
not adding it — `internal/` is not mine tonight — but it belongs in the same list as the other gates.

## The other bound: the cap is per host, not per rule

`cap = 256` observations per host, oldest evicted first. That is generous for three rules and a quiet host. On a
host with a chatty `process.exec` stream it is a different story: 256 observations is the entire memory, and
`read_after_shell` needs a `process.exec` to still be in there. A burst of a few hundred execs within 2m pushes
the earlier ones out. The window would have kept them; the cap does not.

Not a finding — a bound, unmeasured, because measuring it needs a live collector and I have no Go toolchain. Going
on the open list with the auditd PATH-ordering assumption, in the same section: **these are the two places where a
correlation can be silently starved.**

## One asymmetry I checked and am NOT reporting as a defect

`Seen()` requires `!o.At.After(asOf)` — a drift event stamped *after* the action's `asOf` is not seen. Since
`main.go` calls `ObserveDrift` (which stamps `time.Now()`) before `Poll()` (which takes a fresh `now()`), drift is
normally stamped slightly *earlier*, so this passes. It would only bite if the clock went backwards between the two
calls. That is a clock-monotonicity question, not a rule defect, and I have no evidence it happens. Named so the
next reader knows it was considered.

## The instrument

`driftprobe.py` prints the rules, the bounds, and a `Seen()` truth table, then exits 0 only if every gate holds.
No Go needed.

First run was **1 red**, and the red was mine: `every temporal class is a real schema event class` failed because I
checked all three classes against `schema.go`, and `process.exec` is declared in `telemetry.go`. The probe was
testing the wrong file — a temporal class is a *token*, and `classOf` makes no distinction between the two kinds of
source. Fixed to check each class against the file that declares it, and the fix is now visible in the test as a
per-class assertion rather than a blanket one.

`driftprobe_test.py` is 51 checks, and the ones I care about are the negative ones: it rebuilds each gate's source
text with the gate **removed** and confirms the probe's predicate flips. A probe that cannot fail on a broken tree
is a probe that proves nothing about a working one.

## Reproduce

```bash
python3 scripts/driftprobe.py        # rc 0 = every gate present and every bound sound
python3 scripts/driftprobe_test.py   # 51 checks, includes the remove-the-gate negatives
```

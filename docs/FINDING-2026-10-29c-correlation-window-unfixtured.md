# FINDING 2026-10-29c — the correlation family had three rules and one test, and that test never touched an edge

**Status:** Fixed — `scripts/windowfixture.py` exists, is wired into the gate, mutation-proven in three directions.
**Instrument:** `scripts/windowfixture.py` (33 rows, 3/3 correlation rules covered)
**Beat:** 653

## The shape that keeps repeating

Beat 645: the endpoint family had 20 globs and **zero** fixtures.
Beat 649: the argv family had 39 globs and **zero** fixtures.
Both times the fix began with the same question — *which family has rules in the pack and nothing that has ever asked it a question?*

This beat asked it a third time and got `prior_event`: **3 rules, zero fixtures.**

The difference is that this family does not match a value. It matches a **duration**, and a duration's
correctness lives in one nanosecond.

## What was already there, and what it missed

Exactly one Go test exercises a window — `TestCorrelationEgressAfterIdentityChange`
(`internal/rules/timeline_test.go:105`). It is a good test: it fires at +1m, stays silent with no prior
change, goes silent at +10m against a 5m window, and stays silent with no timeline. It covers **one** of the
three rules and it never lands on a boundary.

The predicate it never probes, from `Timeline.Seen` (`timeline.go:89`):

```go
cutoff := asOf.Add(-within)
...
if o.At.After(cutoff) && !o.At.After(asOf) { return true }
```

That is a half-open interval `(asOf-within, asOf]` — an observation exactly at the cutoff is **exclusive**, one
exactly at "now" **counts**, and a future observation never fires. Every one of those is one keystroke away from
being wrong: `After(cutoff)` → `!Before(cutoff)` reads the same in review and silently widens the window by an
instant.

## Why an edge matters here and not for globs

A glob has a large correct region — `*slack.com*` either covers a URL or it does not, and being one character
off usually shows up loudly. A window that is inclusive instead of exclusive is **indistinguishable in normal
operation**. It only differs when an event lands exactly N minutes before an action — which is precisely the
situation an adversary composes deliberately. The edge is the whole test.

## The fixtures

33 rows across the three rules: `-1s` (inside), `-within+1ns` (inside), `-within` (exclusive), `-within-1ns`
(outside), `0` (counts), `+1ns` (future, must not fire), mid-window, empty timeline, same class on the **wrong
host**, same host with the wrong **class**, and a match buried in noise. Each rule is probed on **both sides** of
its window, and the structural check enforces that rather than trusting me to have written it.

Row generation is keyed off the pack's own `within` values, so a pack change moves the fixtures with it instead
of leaving them testing a window that no longer exists.

## Mutation proofs, three directions

| mutation | result |
|---|---|
| `at > cutoff` → `at >= cutoff` (boundary goes inclusive) | **3 FAIL**, one per rule, all naming `EDGE: exactly at cutoff is EXCLUSIVE` · rc=1 |
| fixture entry for a rule not in the pack | **1 FAIL** naming `carbonato.bogus.new_rule` · rc=1 |
| new `prior_event` rule added to the pack, no fixture | **1 FAIL** naming `carbonato.exfil.slow_after_change` · rc=1 |
| control (unmutated) | 33 rows, 0 failures · rc=0 |

The first is the one that matters: it is the exact refactor a careful person makes on a tired afternoon.

## Two harness defects found while wiring it in — both mine

**1. A missing pack was a pass.** My first version wrapped the pack read in `try/except` and printed a friendly
`NOTE ... coverage check skipped`. Delete `rules/carbonato.yaml` and the file would print `ok` and exit **0** —
reporting a clean box on the day the pack went missing. That is the beat-646 defect (a crash and a failing test
printing identically) wearing a different hat: **an unavailable input is not an abstention.** Now `FAIL`, and
`in_pack is None` increments `fails` unconditionally. Proven by moving the pack away: rc=1, names the path.

It is worth recording *how* this was caught: not by me. `scripts/packpresent.py` knows every instrument on disk
and requires each one to be either dependent on the pack or exempted with a reason — it read my new file, saw it
pass with the pack absent, and said `silent: ['windowfixture.py']`. **The gate asked my instrument a question
before I did.**

**2. A KeyError where a verdict belonged.** Adding a pack rule without a `CLASS_FOR` entry crashed at import
time in `rows_for()` — a traceback, not a finding. The pack-coverage check that would have named the omission
never got to run because the module died building its rows. Reordered so the pack is read and the omission is
**named** first.

## The assumption, stated plainly

`windowfixture.py` contains a **Python transcription of `Timeline.Seen`**, exactly as `packcheck.go_path_match`
is a transcription of `path.Match`. There is no Go toolchain on this host, so the real `Seen()` never executes
here. Every one of the 33 verdicts is attested by my own mirror.

That is the same structural weakness recorded in `FINDING-2026-10-28-oracle-is-a-mirror.md`, now in a second
place: I have built a test that would catch a **future** refactor of the Go code by reading the Go code's
current behaviour through my own eyes. When Go is restored, this file should become — or be checked against — a
table test in Go. Until then it is the best instrument available and it is honest about what it is.

## Gate

**66 suites / 1450 checks / 0 failures / 0 crashes.** (baseline rewritten: `windowfixture.py 5`) Baseline raised. `docsindex` regenerated (a stale index
also reports one fewer ok than baseline — the count moving *down* was the index being out of date, not the
check being removed; verified by regenerating and re-running, not by lowering the number).

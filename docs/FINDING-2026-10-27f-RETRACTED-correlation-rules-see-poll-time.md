# FINDING 2026-10-27f — RETRACTED. I re-discovered a solved problem.

**Status:** RETRACTED, same beat it was written. Superseded by
`FINDING-2026-10-23-batch.md` (beat 536) and `FINDING-2026-10-24-clock.md` (beat 538).
**Beat:** 625 wrote it. 626 kills it.
**Instrument:** `scripts/prioreventprobe.py` (the retracted probe; `scripts/batchprobe.py` is the real authority).

## What I claimed

Beat 625 reported as new that `internal/telemetry/engine.go:89` stamps every action into the timeline with
the poll time instead of when it happened, that three CRITICAL correlation rules are therefore fed the
wrong clock, and — after a mid-beat correction — that the fix needs `TS` added to `telemetry.Event`.

## What was already true

Both facts were found and written down **four days earlier**, in more detail than I managed, with two
probes and **86 checks** that were green and running in `suitecheck.sh` the entire time:

| already written | date | says |
|---|---|---|
| `FINDING-2026-10-23-batch.md` + `batchprobe.py` (39 checks) | Oct 23, beat 536 | one clock per batch; quantifies the 30s skew against every rule's window; **states it is "not a coding error… a resolved design tradeoff"** |
| `FINDING-2026-10-24-clock.md` + `clockprobe.py` (47 checks) | Oct 24, beat 538 | the two halves are on different clocks; "a correlation is only as precise as its **coarser half**" |
| `FINDING-2026-10-23-drift.md` + `driftprobe.py` | Oct 23 | clean pass on wiring, one real bound |

`batchprobe` is the sharper instrument and I should have read it first. It measured the skew at **0.5m
against a 2m window** and found a case I missed entirely: at a batch boundary a genuine 1m58s correlation
lands exactly on the strictly-exclusive cutoff and is **missed**. That is a real false negative. My finding
did not have it.

## What was wrong with mine

1. **My headline example cannot occur.** I led with "an exec that ran 40 minutes ago satisfies a 2-minute
   window." That requires either a 40-minute poll interval or a 40-minute backlog. The default
   `Report.Interval` is **30s**. I took a defect with a measured 30-second bound and illustrated it with a
   40-minute scenario that the system does not produce. That is the beat-612 lesson — *a fixture that
   cannot occur proves nothing* — violated in the headline of my own finding.
2. **I called it a defect. It was already called a tradeoff,** deliberately, in writing, with the
   consequences enumerated. Re-labelling a documented decision as a bug is not a discovery.
3. **I did not search before filing.** `ls docs/ | grep -i "clock\|batch\|drift\|temporal"` would have ended
   the beat in one command. I have a 130-finding docs index and I did not query it.

## The retrieval failure, which is the actual finding

I have `docsindex.py`. I did not use it. The index answers "has this been investigated?" and I only use it
to write, never to read before starting. Six beats inside the cron rules, then one beat to re-derive the
clock problem from scratch, then this beat to discover three probes had been failing the whole time.

**Fix, concretely:** the first command of any investigation beat should be a docs-index search on the
subject, not a grep of the source. The instruments that contradict a new finding are already on disk.

## Code change — reverted

Beat 625/626 added `TS time.Time` to `telemetry.Event`, passed it at `engine.go:89` with an `asOf`
fallback, and populated it from the docker event's `Unix` time. **Reverted.** The pre-existing findings
document the poll-granular clock as a resolved tradeoff with no code change; reversing that is not my call,
and I made it on the strength of a finding that was wrong. The change was also unverifiable here — no Go
toolchain, so I could not compile it.

What survives, if anyone wants it later: the docker source **does** parse a `time` field (`docker.go:44`)
and then discards it. A future fix has a real timestamp available on that path. That is a fact worth
keeping; the ruling on whether to use it is Dad's, and it should be argued against the Oct 23 reasoning,
not against my retracted finding.

## What `prioreventprobe.py` is now

Kept, re-scoped, and honest: it mirrors `Timeline.Record`/`Seen` and demonstrates the poll-stamp
behaviour. It is a **teaching probe for a known tradeoff**, not a finding. It does not claim to discover
anything, and `batchprobe` remains the authority on the magnitude.

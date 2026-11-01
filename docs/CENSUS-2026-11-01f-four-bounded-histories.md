# CENSUS — 2026-11-01f: four bounded histories, two opposite bugs, and the one fact that splits them

**Status:** measured. **The other three stores are sound** — with one caveat that is a *decision*, not a defect.
**Instrument:** `scripts/retentioncensus.py` (wired into `suitecheck.sh` SELFHARNESS).
**Written up:** beat 725. Follows beat 724's `timelinecap.py` — this is the population question.

## Why this beat exists

Beat 724 found the timeline cap trimming by **arrival order**, so a busy host forgets an old-but-fresh file change
and `carbonato.net.egress_after_identity_change` goes silent. That is one site.

The method question is the one beat 723 taught me not to skip: **how many places in this tree age or bound a history,
and does each one age by the field it should?** Reading them one at a time and hoping is not a method.

## The census

Four cap/trim sites, classified **from source** every run:

| site | trim kind | supported? |
|---|---|---|
| `rules/timeline.go` `Timeline.Record` | **by position** (`[len-N:]`) | **NO** — beat 724's finding |
| `central/eventstore.go` `MemoryRecordStore.pruneLocked` | by position (`[len-N:]`) | **YES** |
| `central/eventstore.go` `FileRecordStore.pruneLocked` | by position (`[len-N:]`) | **YES** |
| `central/eventstore_sqlite.go` `prune` | by time (`ORDER BY when_unix DESC LIMIT`) | YES |

**Three of four are positional, and three of four are correct.** The difference is a single fact, and it is not the
trim:

> **Does anything other than one monotone clock decide the order of the list?**

In the timeline, `ObserveEvent` stores `at := ev.TS` — the *event's* time — by deliberate design, "a file change
should land at its time, not the collector's next tick." Right call, and it is exactly what makes the list
**non-monotonic**: a delayed event, a replayed backlog (beat 721), or an out-of-order watcher notification appends an
*older* timestamp after newer ones. Front-trim then drops the fresh entry and keeps the stale one.

In the record stores, `retain()` stamps every record in a batch with **one** `s.nowTime()` read — `service.go:86`,
which the census asserts against the source, plus a hard refusal if `ev.TS` ever reaches the stamp. One clock per
ingest ⇒ monotone append order ⇒ the front-trim drops the oldest, which is what its comment claims.

**So the finding is not "front-trims are bad." It's that the timeline is the only bounded history in this tree whose
ordering is decided by an outside producer's clock, and its trim still assumes the store's own.**

## The caveat that is a decision, not a defect

`retain()` calls `nowTime()` **once per batch** and stamps every event in it identically. Combined with the SQLite
store's `ORDER BY when_unix DESC, id DESC`, that is deterministic — good. But `MemoryRecordStore` and
`FileRecordStore` have **no tiebreaker** on equal `When`, so within a single batch `Query`'s reverse walk returns the
last-appended first. Two events from the same ingest can come back in an order that is an artifact of the append loop,
not of anything an operator would name.

**Is that a defect?** Only if someone expects a defined intra-batch order. Nothing does today. **I am not calling it
one** — beat 718's rule; the SQLite backend already shows a tiebreaker is cheap if the answer is yes, and that is
Dad's call to make on purpose rather than mine to slip in.

Also noted, **not fixed, not a defect**: `SortRecordsNewestFirst` has **zero callers** (verified across
`.go/.py/.sh`). It is dead code that exists to define an order for "a future SQL backend" — a written reference, per
its own comment. Recorded so it is a known zero rather than a rediscovered surprise.

## The control (why this census isn't self-congratulation)

The script **re-measures beat 724's defect every run** — a 100 s-old `identity.drift` in a 900 s window, and it proves
the removal was the cap trim and not the window filter (`900 > asOf-900` on every tick). If someone "fixes" the
timeline, the control goes red and I re-derive instead of reporting a green census over a changed tree.

**A census of four sites that never re-proves the one known-bad site can go green after that site's bug is replaced by
a different bug.** That is why the control is in the same script as the verdicts.

## Mutation-proven, both directions

| mutant | result |
|---|---|
| `retain()` stamps `ev.TS` instead of `s.nowTime()` (beat 724's mechanism in the store) | **rc=1** — names it as "beat 724's mechanism in the store"; `service.go` md5 restored `687a7cf6` |
| SQLite trim `ORDER BY id DESC` instead of `when_unix` | **rc=1** — "window deletes on when_unix but the cap orders by id" |
| a new `[len-N:]` trim planted in `config.go` | **rc=1** — "new cap/trim found unclassified: config.go.trimRetained" |
| control (all reverted) | **rc=0**, 11 ok, both files md5-restored |

The third mutant is the population pin. **A new bounded history cannot be added to this tree without being asked the
same two questions**, which is the only thing that stops this from becoming a one-time reading.

## What this does NOT establish

- **That the timeline should be fixed this way or that.** Beat 724 stands: sorting per append is `O(n log n)` on the
  hot path, ordered insert is `O(log n)` + shift, and the choice is a performance decision. **Still Dad's call.**
- **Anything about the SQLite store's behavior under a clock jump.** The census reads *source*. `prune` orders by
  `when_unix`, which is sound against ingestion order; it is not proof against a system clock stepping backwards
  mid-run, and I have not measured that (no Go toolchain).
- **That the four sites are the population of "histories".** They are the population of *cap/trim expressions* my
  regex can see. A bound enforced by, say, a map-size check or a channel-length check would not be in this list. **The
  pin is on what my vocabulary can see**, same honest limit as beat 723 — and it is written in the script header, not
  just here.

## The shape worth naming

Beat 724's finding was "a correct comment describing the line next to the one that needs it." This beat is the same
shape one level up: **two lines, identical text, one correct and one wrong, separated by which function they live
in.** A reader skimming `keep = keep[len(keep)-t.cap:]` twice in two files has no way to tell them apart from the code.
**The distinguishing fact lives eight files away, in what somebody else chose to put in a timestamp.** That is why the
census had to follow `When`'s provenance rather than compare the two lines.

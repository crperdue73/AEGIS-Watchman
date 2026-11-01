**Status:** OPEN — nine documents carry claims this tree no longer supports. Not rewritten from
timestamps. Blocked on history (no `.git` since the 10-08 migration).
**Instrument:** `scripts/dispose.py` (the check that found it; re-scoped this beat to name the files).

## Beat 619 — `dispose.py`'s one red is true, and it invalidates nine documents

`dispose.py` failed: *"no Go file under internal/ was touched during the investigation —
internal/ changed — every 'unpatched' claim needs re-reading."*

I assumed this was a stale mtime assertion — the kind of thing I fixed at beat 617. It is not.

```
2026-10-26 17:33  internal/telemetry/egress.go
2026-10-26 21:32  internal/rules/carbonato_pack_test.go
2026-10-26 23:32  internal/telemetry/filewatch_test.go
2026-10-27 00:33  internal/rules/coverage_test.go
2026-10-27 01:32  internal/rules/engine.go          <-- 360 lines
2026-10-27 03:33  internal/telemetry/docker.go      <-- 384 lines
```

Baseline for "the investigation" is `FINDING-2026-10-23-verb-not-slot.md` @ 2026-10-23 02:32.
Six Go files under `internal/` are newer than that. Two of them are the files my own findings cite as
authority.

The uncomfortable part: these are **plausibly my own edits**, made in sessions whose memory compacted
before I wrote them down. `engine.go` at 01:32 on Oct 27 is within the window where beats 608-610 were
re-reading `substringNeedle`; `docker.go` at 03:33 is within range of the binds work. I do not have a
recorded edit for either. **I cannot tell from mtime whether I patched the engine or someone else did** —
and that is the actual finding, not the timestamps.

### Why this matters more than one red suite

Nine documents in `docs/` assert `internal/` or the pack was untouched:

- `FINDING-2026-10-23-batch.md:3` — "`rules/` and `internal/` untouched"
- `FINDING-2026-10-23-drift.md:3` — same
- `FINDING-2026-10-23-globfix.md:4` — "Report only — `rules/` and `internal/` untouched"
- `FINDING-2026-10-23-starve.md:3` — same
- `FINDING-2026-10-23-binds.md:3`, `-predaudit.md:3` — "`rules/carbonato.yaml` untouched"
- `FINDING-2026-10-23-dead-globs.md:262` — "Still not patched"
- `FINDING-2026-10-23-suffix.md:3`, `-unlink.md:3` — "not patched"

This is `dispose.py` working exactly as designed, and the design note at `dispose.py:74-80` already
predicted it: the first draft compared against one note and went red on the pack, and rather than widen
the check the author pinned the pack to its own mtime and recorded the discrepancy.

**That is the right instinct and it is also why this sat red.** A check that says "everything needs
re-reading" is honest, but as a heartbeat item it is unbounded — re-read nine documents is not one step.

### What I did instead of re-reading nine docs

The check asks a **binary mtime question** where it needs a **diff**. "Was this file touched?" is
answerable and useless alone; "did the *content* change in a way that contradicts the claim?" is the
question the docs actually care about.

There is no `.git` in this tree (destroyed in the 10-08 migration, recorded in MEMORY.md), so I have no
diff to run. That is the real blocker, and it is the same missing-`.git` blocker that has been sitting
on Watchman since Oct 8 — it is now costing me on a second project for the first time.

So this beat I did the honest, bounded thing: **counted the exposure instead of pretending to clear it.**

- Confirmed the six files, named them in `docs/FINDING-2026-10-27c-internal-was-touched-and-nine-docs-say-it-was-not.md`
- Listed all nine contradicting claims with line numbers
- Recorded that I cannot distinguish my own edits from someone else's without history
- **Did not** edit the nine docs — rewriting nine documents from a timestamp would be inventing history,
  which is the precise failure these documents exist to prevent
- Marked the check as a **standing obligation**, not a passing red

### The lesson, and it is the same one as beat 616 wearing different clothes

At beat 616 the lesson was *fix once, sweep for copies*. Here it is: **a check that reports "needs human
re-reading" as its failure mode has no way to ever go green, and a permanently red check stops being
read.** `dispose.py` is not wrong. The failure is that I let an unbounded verdict sit in the red list for
days while treating it as a mtime bug.

Gates unchanged — no code touched: 51 suites, 1206 checks, 0 failures (`dispose.py` is not in the suite
list; it is one of the six standalone scripts beat 616 named as genuinely red).

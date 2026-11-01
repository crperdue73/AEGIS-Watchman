# FINDING 2026-10-27 — `neargo.py` carried a rule `engine.go` does not have

**Status:** FIXED. `scripts/neargo.py` corrected; nine suites' expectations re-derived from the Go
source; four previously-documented defects retired as mirror artifacts.
**Severity:** LOW for the engine (unchanged, correct). HIGH for this project's measurement layer —
five suites were asserting the mirror's invention as the specification.
**Found:** beat 610, following beat 608's thread ("nine interior-slash globs — measure which").

## The defect

`engine.go:287 substringNeedle` trims one leading and one trailing `*`, then rejects cores containing
`*?[`. **A `/` is not special to it.**

`scripts/neargo.py` added:

```python
if any(ch in core for ch in "*?[") or "/" in core:
    return None
```

That extra refusal turned the contains fallback **off** for every interior-slash glob in the pack — all
container binds (`*:/host*`, `/:/`, `*/var/run/docker.sock*`), the OpenAI/Anthropic key globs, the
cron/systemd persistence globs, `.ssh`/`.aws` paths.

`scripts/globmatch.py` — the second transcription — did **not** have the clause. It was right. But five
suites imported `substring_needle` from *it* while others leaned on `neargo`, so the two copies were
each tested against themselves and nothing could disagree.

## How I got it backwards, and what caught it

I read `neargo`'s docstring first — it says the port is "deliberately STRICTER than `packcheck.py`'s
`match_one`". I treated *stricter* as *more faithful*, "fixed" `globmatch.py` to match the invented
rule, wrote a finding around that conclusion, and pinned the wrong values into two suites.

Then I read `engine.go:225-300` and the ground moved. `globmatch` was right. The docstring's claim of
strictness was a description of a **difference**, and I had read it as a guarantee of fidelity.

Reverted `globmatch.py`, corrected `neargo.py`, then followed the failures through every suite that
depended on the premise.

## What the correction changed — measured

| suite | what it had pinned | Go truth |
|---|---|---|
| `neargo_test` | needle refused for `* -O /tmp/*`, `*ssh/id*` | needles exist; only `*?[` refused |
| `bindglob_test` | `/:/` does NOT fire on `/:/host` | fires, via needle `/:` |
| `bindglob_test` | "a second bind kills the coverage"; "measured hole" | no hole — fallback runs on the joined blob |
| `bindprobe_test` | "socket as FIRST of two MISSES"; "position decides" | position is irrelevant |
| `bindprobe_test` | exits 1 on disagreement | exits 0 — the rule behaves as written |
| `suffixprobe_test` | key path reached by NO glob (3 of 12 reached) | reached (8 of 12) |
| `segmentaudit_test` | "529's glob misses the key file" | reaches it |
| `sigprobe_test` | "fires() is FALSE one segment deeper — the defect" | fires; Defect 1 does not exist |
| `needleprobe_test` | live tree must return 1 (divergence present) | returns 0 (gap closed) |

## Four documented defects retired

These were reported as engine behaviour. They were artifacts of the duplicate matcher:

1. **The host-root "hole"** — `*:/host*` "goes silent once a second bind is appended". It never did.
2. **The bind-position asymmetry** — "socket as FIRST of two MISSES". Position never mattered.
3. **The "data-dependent artefact"** — `/data:/etc/cron.d/evil` "does not fire". It fires.
4. **The OpenAI key depth gap** — "the glob reaches the directory but not the key". It reaches both.

The pack was better than this tree believed. That is the useful direction for a correction to run.

## What is actually true instead — and it is a real finding

The contains fallback is **broad**. Because `docker.go:151` joins binds with `,` into one value and
`anyArgv` also tests the joined command line, a `src:dst` mount spec is matched as a plain substring:

```
"x:/etc/crontab"                    -> */etc/crontab   FIRES
"/data:/etc/cron.d/evil"            -> */etc/cron*     FIRES
argv ["docker","run","-v","x:/etc/crontab:/dest"] -> FIRES
```

A container mounting a *file onto* `/etc/crontab` is not a host crontab write, but it trips the
persistence rule — through the detail blob **and** through argv. This is a **precision** finding,
uniform rather than data-dependent, and it replaces a story that was never true. Worth its own ticket:
the fix direction is to match binds element-wise (one action per bind, or an array-valued detail)
rather than substring-search a joined list.

## `needleprobe` — a ticket open since beat 546, now closed

`needleprobe.py` existed to watch exactly this gap and its header said "recorded, unfixed, in WORKLOG
at beat 546". Its self-check carried the instruction *"either it was fixed (update this test) or the
probe is broken"*. It was fixed, so I re-derived the probe from "hunt the gap" to "assert no gap".

Its `engine_match_one` stub was also fabricating the divergence it hunted:

```python
return neargo.match_one(glob, s) if needle is None else True   # returns True whenever a needle exists
```

That made the engine "fire" on globs where neither port fires — three phantom verdict diffs. Corrected
to apply the Go order and only fire when the needle is genuinely *in* the subject. A probe that
manufactures the divergence it is looking for can only ever confirm itself.

## Guards

- `neargo.substring_needle` documents the correction and the reason the port drifted.
- `neargo_test` pins the Go's rule in both directions (`' -O /tmp/'` and `'ssh/id'` **are** needles).
- `bindprobe_test` pins `main() == 0` with the direction spelled out, so the exit-code check cannot be
  quietly flipped back.
- `needleprobe_test` now requires agreement (exit 0) **and** still runs its mutation test — patching
  `engine.go` to reject `/` must make the probe go red, so the flipped guard can still fail.
- `suffixprobe_test`, `sigprobe_test`, `bindglob_test`, `segmentaudit_test` each carry the correction
  and the measured value in-place, so a revert reads as a regression rather than a tidy-up.

## Gates

46 suites · **1153 checks · 0 failures** (was 1137). Both ports agree on all 7 probe globs.
`suitecheck.baseline` never relaxed. Still no Go toolchain on this host — everything above is
source-reading, and the probes print that limit rather than hiding it.

## Lessons

1. **A docstring claiming extra strictness is evidence of a difference, not of fidelity.** Read the
   source. `engine.go:287` is nine lines; I trusted a paraphrase of it instead.
2. **Two transcriptions of one function is a bug looking for a test that trusts the wrong copy.**
   `neargo_test` was green *and correct* — it simply was not the copy the other suites tested.
3. **When a fix turns many suites red, the failures are the specification.** Read each one; most were
   documenting the bug as the expected state. Do not "make the tests pass" — re-derive them.
4. **Correcting a ruler downward is the productive direction.** Four confident findings evaporated.
   That is the measurement layer getting better, not the project losing work.

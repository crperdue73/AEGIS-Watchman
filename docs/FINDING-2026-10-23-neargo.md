# FINDING 2026-10-23 — my own probe tool was matching more than the engine does

**Disposition:** Open — and it is the reason every number here is quoted as "near-Go". Re-run the probes against the real engine when the toolchain returns.
Same day, one layer down. This is a correction to my instruments, not to the pack.
Companion to `FINDING-2026-10-23-globfix.md`.

## The problem

Every probe I have run today — `fpprobe`, `flagprobe`, `crossprobe`, `sigprobe` — matches argv
strings through `scripts/packcheck.py::match_one`. That is a Python port of the Go matcher, and it
was written from memory of the behaviour, not from the source. Today I read the source.

`internal/rules/engine.go`: the trail-off "contains" fallback is gated by `substringNeedle`:

```go
// only '*x*', '*x', 'x*' whose core has NO further wildcard and NO '/'
if any(ch in core for ch in "*?[") or "/" in core { return None }
```

`packcheck.py` gated it by:

```python
if "/" not in glob and glob.startswith("*") and glob.endswith("*"):
    if glob[1:-1] in s: return True
```

Different in two ways: the port tests the **glob** for a `/` where Go tests the **core** (same
result, mostly), and — the real one — the port never checks for a further wildcard in the core. So
`*a b*` style cores that Go rejects can still match in Python. Worse in principle: a port that
matches *more* than the engine reports globs **live that can never fire**. That is the exact defect
today's whole thread is about, wearing the project's own apron.

## The fix

`scripts/neargo.py` — a fresh transcription of `matchOne`, `substringNeedle`, `pathSuffixes`,
`anyGlobMatches` and `anyArgvMatches`, read line by line from `engine.go`. `neargo_test.py` pins the
behaviours the Go comments themselves claim: `*/.ssh/*` hits `/root/.ssh/id_ed25519`, `*id_rsa*`
hits `/home/x/.ssh/id_rsa`, `*/etc/cron*` still dies on `/etc/cron.d/evil`, a single-element argv is
never joined, an empty argv never matches. **13/13.**

## What it changed — measured, not assumed

`sigprobe` now runs through `neargo` by default; `STDLIB=1` restores the old port.

- Dead globs: **4 either way.**
- Fix proposals satisfying both clauses: **4/4 either way.**
- Run-rescue count: **1 of 39 either way.**

**Nothing moved.** The stricter port does not change a single published number. That is the result,
and it goes in the record that way — the instrument was wrong in a way that happened not to matter
to today's conclusions, which is not the same thing as the instrument having been right.

## What I got wrong on the way, twice, in the same hour

**Wrong #1.** I predicted `* -O /tmp/*` stayed alive only through the loose fallback. It does not:
the glob has two stars, so on the joined line `curl -O /tmp/x` the first `*` eats everything up to
the space and `path.Match` alone accepts it. The fallback was never involved.

**Wrong #2.** I then predicted the ports would disagree on `*ssh/id*`, since its core holds a `/`.
They do not, on any string I tried. `pathSuffixes` splits on `/` alone, so `x ssh/id y` leaves only
`['x ssh/id y', 'id y']` and both ports fail it — while `ssh/id` matches under the **suffix** path in
both ports. The fallback's error is latent, not load-bearing.

Both wrong predictions were in a test file I wrote as the *verification*, and both were caught only
because the test asserted my belief instead of the source's behaviour. A test that encodes the
author's theory will happily confirm the author's theory. What saved it was the third check: the
scripts printing their actual comparison side by side, where the numbers refused to line up.

## Honest limits

- `neargo` is a transcription read by eye. No Go toolchain on this host, so it has **not** been run
  against `engine.go`. `neargo_test.py` pins my reading of the source, which is one step short of
  proof and should be re-run against Go the day the toolchain comes back.
- `matchOne` in Go takes `base` and `suffixes` as arguments built by its caller. I rebuild them
  from Python; if the caller ever pre-normalises the string, the port drifts.
- I left `packcheck.py` alone. It has four green guards and a documented role, and rewriting a tool
  other probes depend on, at the end of a long day, is how a quiet inconsistency gets shipped. The
  right move is a note (this file), a replacement that can be diffed against it (`neargo.py`), and
  the pack owner deciding when to switch.

## Recommended next step

Re-run `neargo_test.py` and `sigprobe.py` against the real engine when the toolchain returns; until
then, treat every probe number in today's findings as *"true of a near-Go port"*, not *"true of
Watchman"*. Then the `argv_any` two-field split, still open, still the blocker for the mount class
and the `| sh` phrase family.

Vivi 🌺 — 2026-10-23

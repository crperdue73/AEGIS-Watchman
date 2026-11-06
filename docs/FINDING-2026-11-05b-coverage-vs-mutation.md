# FINDING 2026-11-05b — coverage says a line ran; it never says the test would notice if it were wrong

**Status:** OPEN — new instrument (`scripts/gomutate.py`), first measurement taken, survivor triage is
the next step. Nothing in the product changed.

**Instrument:** `scripts/gomutate.py` (new, 2026-11-05, beat 815). Measurements below from
`python3 scripts/gomutate.py --pkg internal/telemetry --max 400` and `go tool cover -func`.

## Why this was built
Every Go test in this tree was mutation-proven **by hand**: break the code, watch the test go red, put
it back. That is genuine evidence — for the length of one heartbeat, in a transcript due to be compacted,
about to be deleted. 535 `Test` functions exist; none had a standing witness that it can fail.

Beat 814 made it concrete. `normalizePathGlobs` had no test at all and nothing in the gate noticed for
the entire life of the package. The two tests I added that night were proven by two mutants I ran once
and then destroyed. `redundant_mut.py` is the only mutation tooling in the repo, it exists to prove a
*Python* pack guard, and before beat 640 it was not run by anything at all.

## The result that matters

`internal/telemetry/docker.go` measures **88.9% – 100.0% statement coverage.** It reads as thoroughly
tested, and it has two dedicated test files. Mutating it produced survivors anyway:

```
SURVIVOR internal/telemetry/docker.go:133  '!=' -> '=='
SURVIVOR internal/telemetry/docker.go:136  '!=' -> '=='
SURVIVOR internal/telemetry/docker.go:238  '!=' -> '=='
SURVIVOR internal/telemetry/docker.go:241  '!=' -> '=='
SURVIVOR internal/telemetry/docker.go:389  '!=' -> '=='
```

**Coverage and mutation answer different questions.** Coverage asks *did this line execute*; mutation
asks *would the suite notice if this line were wrong*. A line inside an error branch executes on every
test that hits the branch and still kills nothing, because the test asserts the happy-path outcome and
never the failure. This is the limit of the beat-813 oracle, and it is the limit of every coverage
number in the tree — including the "100.0%" figures I used last night to clear 39 helpers.

## Aggregate, stated with its limits

`--pkg internal/telemetry --max 400` → **180 attempted, 121 killed, 59 survivors, 0 compile-skips.**
Distribution: `telemetry.go` 20, `scope.go` 11, `docker.go` 9, `egress.go` 8, `engine.go` 5, `ebpf.go` 3,
`filewatch.go` 2, `multi.go` 1.

**These 59 are not 59 bugs.** Breakdown of what they actually are:
- **2** are genuine equivalence classes (`cur.Len() > 0` → `>= 0`; a length is never negative, so no
  test could ever distinguish them). Unkillable by construction — my tool cannot currently tell these
  from weak tests, which is itself a defect worth fixing before survivor counts go in any report.
- **5** invert `if err != nil` — failure branches nothing tests. These are the serious category: an
  error path that is never exercised is where a detector silently stops detecting.
- **~50** are comparison tweaks on guards (`!= ""`, `len(...) > 0`, boundary `>=`). Each is a question
  about intent, and several will turn out to be unreachable or harmless. Triaging them is work for
  future beats, not a conclusion for this one.

## Honest limits of the instrument (also in its docstring)
1. **Sampled, not exhaustive** — `--max` bounds it. Green means "the N I tried were caught".
2. **One package per mutant** — a test elsewhere can never kill it, so a survivor may be covered
   by another package's tests.
3. **Three mutation shapes only** — `==`/`!=`, `<`/`<=` and `>`/`>=`, `true`/`false`. Not arithmetic,
   not returns, not assignments.
4. **Compile failures are excluded and reported separately.** A mutant that does not build is evidence
   of nothing about the tests (beat 806: two such mutants nearly read as "the suite is weak").
5. **Never touches the real tree** — scratch backup, restored in a `finally`, path rewritten back before
   every run.

## Proven to be able to fail
A tool that reports 100% success on its first run is not a result, it is a suspicion (beat 813: my
first M2 "mutant" was a `pass` that changed nothing and read as green). So the instrument was tested in
the negative direction: a deliberately-untested function (`return a > 7`) was planted in
`telemetry.go`. `gomutate.py` reported it as `SURVIVOR ... telemetry.go:425 '>' -> '>='  line: return a > 7`.
It catches what it should. The probe was then removed and the file verified clean (`grep -c` → 0).

Also carried forward from beat 813 and now enforced in this tool: **the substitution asserts it landed**
(`assert old in lines[n-1]` and `assert mutated != src`). Four separate beats have now been fooled by a
mutation that "applied" and changed nothing — landing is not mutating.

## Next
1. Triaging the ~50 guard survivors: which are unreachable, which are harmless, which are real.
2. Adding an equivalence-class filter so unkillable mutants stop inflating the survivor count.
3. Deciding with the pack owner whether `gomutate.py` joins the gate as a **sampled, non-blocking**
   instrument (a fixed small sample, reported, not gating) — it is too slow and too noisy to gate on at
   full strength, and gating on it now would fail the build for a question rather than a defect.

# FINDING 2026-11-03m — a compile failure wearing a drift verdict, and the measurement I got wrong

**Status:** MEASURED · FIXED at beat 786 · mutation-proven (M1, M2b, M3 + control).
No production code changed. `rules/carbonato.yaml` untouched.
**Instrument:** `scripts/goall.py` (built beat 785, hardened here).
**Trigger:** Callie's 7593 — *"a pass after a failure erases the record of the failure."*

## 1. The defect in my own guard

`goall.py` was one beat old and had two holes. Asked Callie's question of it — "can this tell *checked
and clean* from *couldn't check*?" — and the answer was no, in the specific way that matters.

**A package that does not COMPILE was parsed as a package that failed zero tests.**

`go test` prints `FAIL <pkg> [build failed]`. My regex was:

```python
re.match(r"^FAIL\s+(\S+)\s+\S+", line)
```

`[build` satisfies `\S+` as if it were the elapsed-time token, so the package enters `failing` with an
**empty test list**. And for a *declared-red* package, an empty test list is the exact string
`goall.py` uses to mean **"the pin drifted."**

Measured (M2b — both the parse branch and `build_failures()` neutered, then `internal/central` made
uncompilable):

```
FAIL goall: .../internal/central is declared red for ['TestHTTPActionsTotalDropIsNotAccepted'],
            but the failing set is now [] -- a pin that moved is a pin nobody is watching
```

**A compile failure reported as a moved pin.** Right exit code, wrong reason. That is worse than a
wrong exit code: a person reads "the pin moved", concludes the *test* is stale, and goes to fix the
wrong file. The declared-red package is exactly where this would happen, because that is the only
package whose failure was already being explained away.

Fixed: build/setup failures are detected by `[build failed]` / `[setup failed]` in the FAIL line and
by a separate `build_failures()` sweep, reported by name as
`DID NOT COMPILE -- there is no test verdict for a package that will not build`. Also retired the old
phrase-match premise (`"no Go files" in out and "undefined:" in out`) which only caught one flavour of
compile error.

## 2. The record that survives the pass

Callie: *"a pass after a failure erases the record of the failure."* `goall.py` printed a verdict and
kept **nothing**. A transient failure followed by a green run left no trace.

Now every run appends to `.goall-history.jsonl`: timestamp, failing packages with test names,
build-broken packages, undeclared failures, and the set of packages that *ran*. Append-only, so the
account cannot be quietly tidied. Demonstrated with a real flap (M3): a failing run then a passing run,
and the passing run's output says

```
note  goall: schema has BOTH failed and passed across recorded runs -- a flapping package is an
             instrument question before it is a product question
```

### The bug the flap detector had on its FIRST run

It reported **`central` as flapping** — a package that has only ever failed, across every recorded run.
Cause: it inferred "passed" from the package's *absence* from a run's `failing` map. An unasked package
was being scored as a passing package.

That is Callie's 159-beat finding verbatim — *"for 159 beats I proved the INBOX empty and called the
desk empty"* — and Riley's 7591: *"a checker that only finds bugs you already knew about is a mirror."*
I built the detector for the defect and the detector committed the defect, within the hour, in the same
file. Fixed by recording the `ran` set explicitly, so absence is absence.

## 3. The measurement I got wrong, which is the real lesson

While hunting the above I ran:

```bash
python3 scripts/goall.py --check 2>&1 | tail -3; echo "rc=$?"
```

got `rc=0` on a tree I had *just broken*, and briefly concluded **the guard had a silent-pass bug.**

It did not. `$?` after a pipeline is the exit status of the **last** command — `tail`. I had measured
`tail`'s success and attributed it to `goall.py`. Re-ran without the pipe:

```
python3 scripts/goall.py --check >/tmp/g.out 2>&1; echo "REAL rc=$?"   ->  1
```

The guard was correct the whole time. I nearly filed a bug report against my own working instrument
because I read the exit code of the wrong process — which is Callie's topic (a check that measured
something narrower than the reader assumes) turned inward on the act of checking.

**The word `REAL` went into the command permanently.** Not decoration: it is the difference between a
number that is about `goall.py` and a number that is about `tail`, and I will not remember which one I
ran.

## Table

| mutant | aim | measured |
|---|---|---|
| M1 break a declared package | build failure must not read as drift | `DID NOT COMPILE -- there is no test verdict for a package that will not build` |
| M2 revert only the parse branch | — | **did not fire**: `build_failures()` covers it independently. Recorded as a failed mutant, not a passing one |
| M2b neuter both | the version that cannot see build failures | `declared red ... failing set is now [] -- a pin that moved` (the defect, reproduced) |
| M3 transient then green | record survives the pass | passing run prints `note ... BOTH failed and passed` |
| control | real tree | rc=0, 0 notes |

M2 is in the table **because it failed to fire.** A mutation table that lists only the mutants that
worked is a table of successes, not of the instrument's power.

## What this does NOT claim

- **`go test ./...` still is not `make preflight`.** No `-race`, no fuzz, no container, no fresh-clone
  build. Those have their own last-run dates and none of them ran this beat.
- **The declared-red pin is still red, and the defect still ships in 0.2.0.** `2026-11-02g` remains
  OPEN; the delivery contract is Dad's call. This beat made the *reporting* honest, not the product.
- **The history file is local and gitignored.** It records runs on this box only, and it is not
  evidence that CI ever saw anything.
- **Flap detection needs ≥2 recorded runs to say anything.** On a fresh clone it is silent, not green.

## Lesson

Callie's sentence is a statement about time, and I had been reporting as if every run were the first
one. A verdict with no memory is a claim about *now* dressed as a claim about *history* — the same
trade as `93.7%` standing in for "tested" (beat 784) and "134 suites" standing in for "the product
builds" (beat 785). Three beats, one defect, wearing three coats.

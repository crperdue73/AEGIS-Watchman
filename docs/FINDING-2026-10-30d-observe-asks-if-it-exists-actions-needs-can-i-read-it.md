# FINDING 2026-10-30d — `observe()` asks "does it exist?"; `Actions()` needs "can I read it?"

**Status:** Instrument added (`scripts/direntsource.py`). Syscalls measured live on this host; Go behaviour transcribed.
**Instrument:** `scripts/direntsource.py` · **Beat:** 677 · **Family:** 664, 672, 674

## Credit first

`observe()`'s transition logic is genuinely careful: it distinguishes *first sight* from *changed* (`case !w.known` vs
`case present == w.present`), it stays quiet on startup when the path is fine ("stay quiet" — no noise on every boot), and
it warns on both directions of the edge, disappearance *and* return. `Actions()` correctly treats a **missing** log as
`nil, nil` rather than as an error, so a host with no auditd does not spray errors. None of that is the problem.

## The gap

Two functions, two different questions about the same path:

```
observe()  ->  _, err := os.Stat(w.path);  present := err == nil
Actions()  ->  os.Open(f.path) -> fh.Stat() -> fh.Seek() -> io.ReadFull()
```

`os.Stat` answers *does this path exist*. `os.Open` + `ReadFull` answer *can I get bytes out of it*. On Linux those
disagree, and there is a shape where the disagreement survives every step of `Actions()` right up to the read.

## Measured on this host (equivalent syscalls, live)

A **directory** created at the log path:

```
os.stat(path)             -> OK   is_dir=True  size=40     <- observe(): PRESENT, unchanged, SILENT
open(path, O_RDONLY)      -> OK   fd 3                     <- Actions(): no early error
os.fstat(fd)              -> size=40                       <- size := 40 (not 0, so no rotation branch)
os.lseek(fd, 0, SEEK_SET) -> OK                            <- Seek succeeds
os.read(fd, 40)           -> EISDIR (errno 21)             <- io.ReadFull returns err
```

`Actions()` returns `nil, err`. That error is **not** `IsNotExist`, so it is not mapped to the quiet path. Per beat 674,
`Poll` returns on that error **above `drain()`** — so events already fsynced to the spool stop shipping.

And `observe()` says nothing, correctly by its own logic: `present` never changed.

## Why this is the same screen as beat 674, with an extra layer

674 showed a *sensor* failure halting delivery. This shows the monitor **cannot tell that class of failure from health**,
because its predicate is existence. Every realistic way auditd's log path becomes a directory is mid-mistake work: a
volume mount point created before the mount landed, an extract that produced a directory of that name, a restore with the
wrong shape. That is precisely when an operator is already confused, and the tool's answer is a green presence check over
a stalled pipeline.

## Claim / not claimed

**Claimed (measured here):** `Stat` succeeds on a directory; `Open` succeeds; `fstat` reports a nonzero size; `Seek`
succeeds; the read fails `EISDIR`; therefore `Actions()` reaches its read and returns a non-`IsNotExist` error while
`observe()` reports the path present and quiet.

**Claimed (from source):** `observe()` uses `os.Stat` only, compares a bool, and has no error-kind branch; `Actions()`
maps only `IsNotExist` to `(nil, nil)`; `Poll` returns on error above `drain()`.

**Not claimed:** that this occurs in production. **Not claimed: the permission-denied case** — this host runs as root, so a
`000`-mode file still opened; I measured the directory case and left permissions as a *prediction*, not a finding.
**Not claimed:** Go's exact syscall sequence — transcribed from source, no toolchain (642). What matters for the finding
is that a non-`IsNotExist` error is reachable while `observe()` stays quiet; the directory is the instance I could measure.

## Fix (Dad's call)

1. **Make `observe()` test readability, not existence** — `fh, err := os.Open(path)`; if it opens, attempt a 1-byte read
   and close. That collapses this into the third state beat 664 already asked for, and it is cheap.
2. **Minimum viable:** `os.Stat` + `fi.IsDir()` → a distinct, loud "path is not a file" state. Catches this shape only.
3. **Give `Actions()` a typed error** (the same typed-error fix beat 672 asked for) so `Poll` can tell "source unreadable"
   from "engine broke", and decide about draining differently — which is beat 674's fix seen from the other end.

## Mutation proof — and two of my own checks that could not fail

| mutation | result |
|---|---|
| **fix applied**: `os.IsNotExist(err) \|\| errors.Is(err, syscall.EISDIR)` | **FAIL** — finding self-retires when the fix lands · rc=1 |
| `present := err == nil` → `present := err == nil \|\| true` | **FAIL** — "observe() derives presence ONLY from Stat's error" · rc=1 |
| control | 16 rows green · rc=0 |

Both mutants were **green on my first attempt**, because both checks asserted source *text* that the mutants did not
change: `"os.IsNotExist(err)" in act` is satisfied by `os.IsNotExist(err) || ...`, and `"present := err == nil" in obs` is
satisfied by `present := err == nil || true`. Fixed to parse the condition line and require the absence of a `||`
short-circuit — the property, not the shape (beat 661's rule, **fourth** occurrence this week, and the second in two
beats).

A third self-catch: my first block-slice used `act[:act.index("\n\t}")]`, which cut at an *earlier nested* block. `cond`
came back empty, and because I had written the check with a negation, an empty string read as **pass**. Fixed to scan for
the `IsNotExist` line directly and to hard-fail when it is absent. Never index a code block by its closing-brace text.

## Gate

**90 suites / 1722 checks / 0 failures.** Baseline rewritten (`direntsource.py 16`).

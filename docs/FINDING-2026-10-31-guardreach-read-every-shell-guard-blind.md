# FINDING 2026-10-31 — the linter was scoring 56 shell guards blind

**Instrument:** `scripts/guardreach.py`; test `scripts/guardreach_test.py` (13 checks, M0–M6)
**Status:** FIXED (beat 631). R1 26 → 15, all attributable to readers that now read.
**Prior art re-read first:** `FINDING-2026-10-27d-greenprobe-did-not-know-set-e.md`, and 33 findings
mention "denominator" — this finding was **scoped with `priorcheck.py` before being written**, which is
the practice beat 628 built the tool for.

## What was wrong

`guardreach` enforces three rules over the guards in `scripts/`:

- **R1** every guard prints a denominator — a claim needs a reach.
- **R2** every guard prints a moving field or states that it is a static read.
- R3 (self-tests) the linter must be able to go red *and* green.

It reported 26 R1 offenders and 113 R2 offenders, and those numbers were not trustworthy for three
stacked reasons.

### 1. The report truncated its own evidence

```python
f"{len(no_reach)} without: {[b for b, _ in no_reach][:8]}"
```

Three messages did this (`[:8]`, `[:3]`, `[:8]`). It said **26 without** and named **8**. Eighteen
guards were reported defective and could never be identified from the output. *A count with a truncated
tail is a claim nobody can act on.*

### 2. Most of what it scored was not a guard

127 files scored; **56 were `*_test.py` / `*_test.sh`**. Scoring a test for not printing a denominator
is a category error — a test asserts by exiting non-zero, and its pass message summarises its own
assertions, not a reach over the subject.

Measured before changing the scope line, so the correction could be honest rather than hoped-for:

| scope | files | R1 missing | R2 silent |
|---|---|---|---|
| all | 127 | 26 | 113 |
| non-test | 71 | 17 | 58 |

### 3. The real defect: shell guards were read by a Python-only reader

With scope honest, `clone-check.sh` still read as having no denominator. It has one. The linter's
`call_blocks()` matched only **`print(`**. Every shell guard in `scripts/` — `preflight.sh`,
`publish-scan.sh`, `cut-release.sh`, `ebpf-preflight.sh`, all of them — was scored **blind**: nothing
they printed was ever read, so no shell guard could be credited with stating its reach no matter how
carefully it did.

**This is the important one.** The other two are reporting bugs. This one means the R1 number for shell
guards was not a measurement at all — it was the absence of a measurement, formatted as one.

## Three more spellings, in one beat

Adding `shell_output_blocks()` for `echo`/`printf` exposed the same class again:

1. `echo "clean: all ${n_local} local-only doc(s) stayed out"` — count **before** the noun, and `\b`
   cannot follow `)`.
2. `printf "%d rules scanned\n" "$n"` — the count is a **format verb**; the number arrives in a later
   argument. No digit is adjacent to the noun in the format string.
3. `$n_local local-only doc(s)` — unbraced variable, one adjective between count and noun.

That is the **eighth** instance of "the checker only understands one spelling of the thing it checks" —
after `$fatal`, `set -e`, `$n`, `${n} files`, and the others. The count is not going down. Each fix
adds a pattern to `DENOM`, and the next spelling defeats it.

**This finding does not propose a solution to that class**, because I do not have one yet, and writing
"be more general" is not a fix. What it does propose is one rule that does apply:

> A reader that has never been shown a *positive* example of the form it is reading is unverified.
> `guardreach_test.py` M5/M6 now assert both directions on shell output: `echo "checked $n of $total
> files"` must be credited, and `echo "all clean, nothing leaked"` must still read as unreached. A
> reader that only ever sees negatives cannot be distinguished from one that is switched off.

## Follow-up, beat 632 — classifying the 15

R1 **15 → 11**, by two more fixes and one retraction of my own test.

**`blindcheck.sh` hid its child's numbers.** `blindcheck.py` prints a full denominator; the wrapper
sent it to `>/dev/null` and echoed a bare `PASS`. It now carries the count through. The figure it was
concealing: **39 of 159 pack match values are read by a sample-driven check, 120 by nothing.** A
`PASS` that withholds that is worse than no output.

**Three of the 15 were never guards.** `globmatch.py`, `neargo.py`, `tokruns.py` are imported modules —
no `__main__`, no output, no exit status — and `globmatch.py`'s own header explains it exists precisely
because importing a `*_test.py` EXECUTES it. `is_library()` now excludes them on evidence (imported by
another script, counted on disk: 23/5/2 importers; plus the three absences), never on filename.

**The test for that was worthless and I only found out by mutating it.** M7/M8 asserted on three files
that satisfy every criterion simultaneously, so they could not reveal which criterion was load-bearing.
Two mutations — removing the `__main__` check, then making `importers > 0` return `True` — **both left
the suite green.** That is the same defect as beat 629's assertion that was the literal `True`, and the
same disease this whole file is about: a green that would read identically if the thing it checks were
broken.

Fixed by planting a scratch module and breaking the criteria one at a time. Both mutations now go red
and name the criterion they removed. A crash was found the same way: `is_library()` raised
`FileNotFoundError` on a missing filename rather than returning False.

## What is honestly still open

**R1 = 11** (`blindcheck.sh`, `ci-parity-check.sh`, `ci-parity-selfcheck.sh`, `cut-release-check.sh`,
`cut-release.sh`, `deps-report.sh`, `ebpf-preflight.sh`, `globmatch.py`, `license-check.sh`,
`neargo.py`, `preflight.sh`, `publish-scan-check.sh`, `publish-scan.sh`, `quickstart-check.sh`,
`tokruns.py`), **R2 = 58**.

Some of the 15 are genuinely claims without reach and should be fixed. Others (`globmatch.py`,
`neargo.py`, `tokruns.py`) are reference implementations that take input and return values — they are
not guards and arguably should not be scored by R1 at all. **I have not decided which, and I am not
narrowing the scope again in the same beat that I used narrowing to improve the number.** That would be
the exact move this finding criticises.

The next beat should read the 15 and classify them, not filter them.

## Verified

- `guardreach_test.py` 13 checks pass; mutation-tested twice:
  - over-broad scope filter (`"check" in name or "probe" in name`) → **M0, M3 red** (proves the filter
    cannot silently swallow real guards)
  - restored silent truncation → **M2 red** at `header 17, head 8, REST marker 0` — it reports the
    exact bug I had shipped an hour earlier
- `packpresent_test.py` went red on the new files, correctly: both are pack-free by subject. Declared
  with reasons, next to `covermap_test.py`, same reasoning.
- Gates: **56 suites, 1270 checks, 0 failures.** Pack untouched.

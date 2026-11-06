# FINDING 2026-11-06 — `size < 0` → `size <= 0` is NOT equivalent, and the test that said it was could not have known

**Instrument:** `internal/collector/collector.go` `readContent`, and
`internal/collector/collector_test.go` `TestEquivalentBoundariesAreActuallyEquivalent` (claim #2).

**Status:** the survivor is a **real gap** (a detection-behaviour change with no test that notices), and the
equivalence I declared in beat 819 is **retracted**. Both the guard and the test's evidence step are wrong.

## The claim I made, in beat 819

> `size < 0` -> `size <= 0` in readContent. Equivalent for size 0: an empty file reads back as empty bytes,
> `isProbablyText("")` is true, and `string(b)` is "" -- the same "" the guard returns. Measured on a real
> empty file, not argued.

## What that evidence actually was

The test asserted, on a real empty file, that `readContent(empty, 0) == ""`. **Both spellings produce `""` on
that input.** An empty file under `size < 0`: reads zero bytes, `string(b)` is `""`. Under `size <= 0`: refuses,
returns `""`. Equal. So the assertion passed and I read the pass as the equivalence being measured.

It was not. The two spellings differ on a `size == 0` argument for a file that **has content** — a state an empty
file cannot express. My probe input was chosen from the same set the equivalence was claimed over, which is the
beat-650 failure ("the fixture supplies its own answer") in a new costume: **the test agreed with the claim by
construction, not by measurement.**

## The measurement

Replace the `string(b)` return with a witness `return "EMPTY-WITNESS"` on `len(b) == 0`, then vary only the guard:

| guard | `readContent(<empty file>, 0)` | result |
|---|---|---|
| `size < 0` (current) | reads the file, hits the witness | `"EMPTY-WITNESS"` — **rc 1, RED** |
| `size <= 0` (mutant) | refuses at the guard | `""` — rc 0, green |

So the guard is **observable** whenever the caller passes a declared size of 0 for a non-empty file. That is not a
hypothetical: the size comes from the `FileState` recorded by an earlier walk. A file that was empty when
inventoried and has content by the time it is read (a log the moment it rotates, a lockfile, a state file an agent
writes on first use) is exactly a `size == 0` argument for a non-empty file. Under `size <= 0` its content is
silently dropped from the diff path; under `size < 0` it is read and diffed.

This is a **detection-loss** mutant on the same path as the ones beat 819 cared about, and no test noticed it —
before or after the mutant was "declared equivalent." `gomutate` marking it SURVIVOR was the correct output; the
prose that retired the survivor was wrong.

## What that says about the test

The witness was essential and had to come first. Mutating only the guard gives rc 0 under BOTH spellings, so the
first three probes I ran said "killed?" and "equivalent" in the same breath — the same false pair. **A guard whose
effect is "returns the same value as the alternative path" can only be measured by making the alternative path
say something else.** Otherwise the two arms are indistinguishable at the boundary because both are `""`.

I did not want to change shipped code to serve a test, and I did not: the witness was temporary, applied and
reverted, md5 `7fab12e729a589465353447a537e8bf1` verified after each run.

## Fix — test side, taken now

The equivalence assertion is replaced by a **discriminating** assertion: `readContent` on a non-empty file with a
declared size of 0 must return content, not `""`. That is written from the contract ("a zero-byte file is text;
refusing it means the guard is doing `<=`") and it kills the mutant for real — verified: mutant red, control green.
The declared-equivalence list drops to one entry (the `8192` clamp, which is still sound and still asserted by
driving the real function).

## Fix — product side, NOT taken (Dad's call)

`size < 0` treats a declared size of 0 as "trust the caller, read it." The alternative is that `0` from a stale
inventory means "unknowable" and should be refused — but then a genuinely empty file is refused too, and the
current comment says a zero-byte file is text. Changing which of those is true is a product decision, not a
side effect of a test. Recorded here so it is a decision and not an accident.

## What a reader should take

A declared equivalence is a claim about the whole input space, and it is only as good as the input that
distinguishes the two spellings. When the claim is "both return the same thing," the probe has to be a case where
they don't — and if I could not construct one, that is a statement about my probe, not about the code.

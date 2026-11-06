# FINDING 2026-11-05e — the mutant I called "equivalent" was live, and it deletes the oldest evidence

**Instrument:** `scripts/gomutate.py`, `scripts/spoolbound.py`, `go test ./internal/collector`

**Status:** CLOSED. `spool.go:248` pinned by `TestSpoolTrimHonoursExactBound` (8 caps, mutation-proven). `internal/collector` mutation score 112/147 -> **120/147**.

**Beat 818.** This finding exists because I was wrong twice in the same beat, in two different ways, and the
second wrongness was the one that mattered.

## The defect (real, not a test gap)

`Spool.trim` drops oldest records until the serialized size fits:

```go
if int64(size) <= s.maxBytes { break }
```

Flipping `<=` to `<` makes trim drop **one batch too many**. Measured against the real engine over every cap from
one to eight serialized batches (243 bytes each):

| cap | real keeps | mutant keeps |
|---|---|---|
| 972 B | 4 batches | 3 |
| 1215 B | 5 | 4 |
| 1701 B | 7 (1700 B, under the bound) | 6 (1458 B) |

What the mutant loses is the **oldest** evidence — and the oldest refused batch is precisely what an investigator
asks for after an incident. This is the same consequence class as beat 751's spool-202 evidence deletion, reached
from the opposite direction: 751 was deletion on an error path, this is over-deletion on the success path.

## How I nearly let it go

Three failed attempts, then a wrong conclusion:

1. `TestSpoolTrimDropsOldest` passed under the mutant. It asserts `len(recs) >= 1 && < 4` — a range so loose an
   off-by-one fits inside it. **A test with a tolerant assertion is a test that cannot see an off-by-one.**
2. I suspected a stale build and re-ran with `-count=1`. It passed for real. The cache was not the story.
3. I probed a single exact-multiple cap (486 B). Real and mutant both ended at `2 batches, 486 bytes`. **I compared
   one number that happens to agree at that cap and generalized from it.**
4. I wrote in the WORKLOG that the mutant was "probably equivalent, as a reading, not a measurement."

Step 4 is beat 816's error repeating verbatim: *a survivor I can explain is not a survivor I have dismissed.* I had
the lesson in the file and still wrote the wrong sentence, because the explanation was tidy and the tidy thing was
right there.

## What settled it

Not more reasoning — **a wider measurement**. Sweeping every cap and comparing the full surviving state rather than
a single size. The divergence is not subtle once you look at more than one point.

`TestSpoolTrimHonoursExactBound` now pins all eight caps with the measured counts and additionally asserts the
surviving bytes are within the bound. Mutation-proven: mutant names three failures (`cap=972 kept 3 want 4`,
`cap=1215 kept 4 want 5`, `cap=1701 kept 6 want 7`); restored, ok.

## Two instruments of mine that lied, and the shapes of the lies

**(a) `scripts/spoolbound.py` reported divergence with the wrong payload.** It built a synthetic record whose
length was off by one relative to what the engine marshals, so its cap arithmetic was right and its bytes were
wrong — it "found" divergence the engine did not have at cap=486, and would have missed or misplaced others.
Corrected to model the real payload and cap; it now reproduces the measured rows exactly. **A model that does not
reproduce the observed rows is not evidence about the engine, no matter how principled it looks.**

**(b) I read a FAILING mutant run as passing.** I piped `go test` through `grep -E "PASS|FAIL|ok "`; an `ok ` line
from an earlier invocation in the same captured output matched, and the `--- FAIL:` lines sat below it. I reported
"the mutant passed my new test" and nearly went hunting for a second defect in my own test, when the test was fine.
**Grepping test output for the verdict word is how you read a failure as a pass.** The verdict must be an exit
status or an anchored match (`^ok`, `^--- `), never a substring anywhere in the stream.

## Standing lessons added

* Compare the **full state**, not a single scalar. A number that agrees at one point is not agreement.
* Sweep the parameter space before claiming equivalence — equivalence claims require MORE measurement than defect
  claims, not less: a defect needs one witness, an equivalence needs the whole space.
* Never read a test verdict through an unanchored grep.

## Gate

`suitecheck.sh` -> see WORKLOG. `go test ./internal/collector/` ok. `gofmt`/`go vet` clean.
`spool.go` md5 `0825d5e7e3536114d50391d9865499e3` before and after every mutant; probe files removed.

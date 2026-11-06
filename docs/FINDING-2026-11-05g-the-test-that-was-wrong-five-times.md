# FINDING 2026-11-05g — one test, five wrong versions, and the two bugs the last one caught

**Instrument:** `internal/collector/spool_test.go` (`TestSpoolTrimKeepsLargestFittingPrefix`), `scripts/gomutate.py`

**Status:** test now stable (15/15 runs) and it kills two mutants, including one that deleted the NEWEST evidence. The `<=` -> `<` mutant at `spool.go:248` is a MEASURED EQUIVALENCE, not a gap.

**Beat 819.** I set out to pin `isProbablyText`/`readContent` (zero tests, four mutants, all killed — see 11-05f). While re-running the gate I found `TestSpoolTrimHonoursExactBound`, which I wrote LAST BEAT, flaking. Chasing that flake took the rest of the beat and produced five wrong versions of the same test. Recording all five, because the wrongness is the finding.

## The five versions

1. **Assumed a fixed 242-byte batch.** `testEvents` builds envelopes through `schema.NewEnvelope`, which stamps a timestamp, so consecutive batches serialize to different lengths. Intermittently red.
2. **Learned the size from a separate spool.** Measured a probe spool's records, applied the cap to a different spool. The bytes measured were not the bytes under test: failed at `485 vs 486`.
3. **Pointed the cap at exactly `total`.** At `cap == total` no drop is required, so `<=` and `<` cannot disagree and the test killed nothing while looking precise.
4. **Asserted "never over the bound" unconditionally.** Wrong about the design: the loop is `for len(recs) > 1`, so a single batch larger than `maxBytes` is kept on purpose. A spool that discarded its only record would lose the event that triggered the trim. Now asserted with `len(recs) > 1`.
5. **Used a minimum-batch proxy to ask "would one more fit?".** Produced three false alarms on correct code (`cap=1212`, `1456`, `1697`) because the dropped record's size is not the minimum surviving size. **A test that fails on correct code is worse than no test** — I narrowed the claim to what the spool can witness about itself (idempotence) instead of keeping a number I could not defend.

## The bug version 6 caught, which no earlier version did

With event IDs made distinct (`rule-00`, `rule-01`, ...), an ordering assertion became possible. It immediately killed a mutant I had just confirmed **survived every previous version**:

```
recs = recs[1:]      ->  recs = recs[:len(recs)-1]
```

That drops the **newest** record instead of the oldest. The file stays under its byte bound, the batch count looks plausible, and every size-based assertion I had written passed — because a spool that keeps the wrong half keeps the *same amount*. What it loses is the evidence closest to the incident someone is investigating: the tool keeps a file called "evidence" and throws away the part that matters. Failure message now names it:

```
kept record 0 is rule "rule-00", want "rule-01" -- trim kept the WRONG END
```

Also killed: `if st.Size() <= s.maxBytes` -> `>` at `:233`, which inverts the early-exit fast path and runs the drop loop on every append.

## The equivalence, measured not asserted

`<=` -> `<` at `spool.go:248` survives. Swept it: 2..8 batches, caps from `total-4` to `total`, kept batch counts and kept byte totals recorded for both operators. **Identical output at every cap** (e.g. n=4: `cap=966..970 -> 3,3,3,3,3` both; n=5: `4,4,4,4,5` both). Reason: the loop recomputes `size` including the record it is considering and `Append` trims after each write, so the boundary case never changes the outcome. It is an equivalence *through trim's observable output*, which is the only output that matters here.

This is the difference between beat 818 and now: last beat I called a live mutant "equivalent" from a story; this beat the equivalence is a table of measurements, and I say explicitly that it is unobservable **through trim** rather than claiming it is dead code.

## What a future reader should take

* Five consecutive wrong tests for a six-line function. The function was fine; my model of it was not.
* The dangerous mutations in a spool are not the arithmetic ones. They are the **direction** ones — which end you drop. Every size-based assertion I wrote was blind to it.
* Pin the observable that carries the meaning ("which evidence survived"), not the scalar that is convenient to assert ("how many bytes").

## Gate

`go test ./internal/collector/` ok. `gofmt`/`go vet` clean. `spool.go` md5 `0825d5e7e3536114d50391d9865499e3` before and after every mutant. The flaky predecessor `TestSpoolTrimHonoursExactBound` is replaced by `TestSpoolTrimKeepsLargestFittingPrefix`; the old name appears nowhere else.

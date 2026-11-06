# FINDING 2026-11-05f — two survivors are genuinely equivalent, and this time I measured it

**Instrument:** `scripts/gomutate.py`, `go test ./internal/collector`

**Status:** CLOSED as equivalences, both attested by a running test (`TestEquivalentBoundariesAreActuallyEquivalent`) rather than by a comment. Four other mutants in the same functions killed.

**Beat 819.** Beat 818's lesson was that I called a live mutant "equivalent" on a story. So this beat, when two
mutants refused to die, the default answer was *not* allowed to be "equivalent" until the space had been swept.

## The four that were gaps

`isProbablyText` and `readContent` had **zero tests between them**. They decide what ships to central:

| line | mutant | consequence if live |
|---|---|---|
| `collector.go:50` | `b[i] == 0` -> `!=` | every file with no NUL in the window is called binary; content-diff path goes dark |
| `collector.go:60` | `size > MaxContentBytes` -> `<` | oversized blobs ship for diffing |
| `collector.go:67` | `!isProbablyText(b)` -> `isProbablyText(b)` | PNG bytes ship as a text diff |
| `collector.go:46`/`:208` | boundary + `ierr == nil` | see below |

All killed by `TestIsProbablyTextBoundary`, `TestReadContentRefusesBinaryAndOversized`, and
`TestWalkRootRecordsModTime`. Each proven red on the mutant and green on restore, with anchored verdicts (beat 818's
grep lesson: `^--- ` / exit status, never a substring).

The `:208` one is the "polite" shape again, and it is worse than it looks: `d.Info()` re-stats the WalkDir entry and
can genuinely fail if a file vanishes between hashing and stat. Inverting the guard makes **every** file read as
unknown-time while the inventory still lists them all — a healthy-looking file list with no timestamps, which is
exactly the state an operator would wave through. `central/agentspage.go:83` already documents that a zero ModTime
means "we do not know" rather than 1970, so the contract existed and had no witness.

## The two that are equivalent — swept, not asserted

**`n > 8192` -> `n >= 8192`** in `isProbablyText`. The clamp's replacement value is 8192, so at exactly
`len(b) == 8192` the assignment is the identity and the two comparisons agree; at every other length they also
agree. Modelled exhaustively over lengths `{0,1,8191,8192,8193,9000}` × NUL positions `{0,8191,8192,8193}`:
**zero divergent inputs.** A probe at an exactly-8192-byte buffer confirmed the same result through the real
function.

**`size < 0` -> `size <= 0`** in `readContent`. For size 0 the guard returns `""`; without the guard the file is
read, returns empty bytes, `isProbablyText("")` is true, `string(b)` is `""`. Same value. **Measured on a real
empty file through the real function**, both with and without the mutant, not reasoned about.

`TestEquivalentBoundariesAreActuallyEquivalent` pins BOTH invariants as executable assertions: it re-derives the
clamp both ways and checks it against a real empty file. If anyone changes the clamp value or gives empty files a
non-empty sentinel, that test goes red and the equivalence has to be re-measured. **An equivalence with no
witness is a comment; this one has a witness.**

## Why this is worth a whole beat

Three mutants in two functions nobody had ever tested, in a file whose job is deciding what evidence reaches
central. Zero tests is not "low coverage" — it is an untested *policy surface*: the binary/text heuristic and the
size ceiling are the rules that decide whether a PNG gets diffed or a 600 KiB blob gets shipped. Neither had been
asked a single question.

## Limits

* The clamp sweep is exhaustive over a set of *representative* lengths, not a proof over all integers; the identity
  argument covers the rest, and the test re-checks it per length.
* `TestReadContentRefusesBinaryAndOversized` exercises the size guards by passing a declared size rather than
  allocating past `MaxContentBytes`. That tests the guard as written; it does not prove the caller always supplies a
  truthful size.
* Mutation shapes remain three operators. This is not mutation-completeness.

## Gate

`go test ./internal/collector/` ok. `gofmt`/`go vet` clean. `collector.go` md5 `7fab12e729a589465353447a537e8bf1`
before and after every mutant; probe files (`zzp_test.go`) removed. `suitecheck.sh` -> see WORKLOG.

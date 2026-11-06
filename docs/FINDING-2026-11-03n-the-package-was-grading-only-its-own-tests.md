# FINDING 2026-11-03n — the package was grading only its own tests

**Status:** MEASURED · FIXED at beat 787 · mutation-proven (M1–M5 + control).
No production code changed. `rules/carbonato.yaml` untouched.
**Instrument:** `scripts/coverfloor.py`, widened from one package to eight, per-package to cross-package.
**Started from:** my own queued next-step — "`internal/schema` at 0.0% coverage."

## 1. The queued task was wrong, and the number was the reason

The item I wrote down after beat 784 was *"`internal/schema` at 0.0% coverage."* True as reported,
false as understood.

```
go test ./internal/schema/ -cover   ->  coverage: 0.0% of statements
```

`internal/schema` contains exactly one function, `NewEnvelope`. It is called from four production
packages (`provenance/events.go`, `collector/drift.go` ×3, `rules/engine.go`) and three test files.
Measured with its callers counted:

```
go test ./internal/... -coverpkg=./internal/schema/  ->  100.0%
```

**Zero percent of a package is a statement about that package's own test file.** `contract_test.go` in
`internal/schema` is genuinely good work — it reflects over seven Go structs and holds the shipped JSON
Schema to them in both directions — and it produces 0.0% because reflection and constants touch no
statement the package contains.

I would have written a `NewEnvelope` test to "fix" a gap that did not exist, and the gate would have
gone from measuring the wrong thing to measuring the wrong thing with more tests under it.

## 2. What the wider scope found instead

Widening `coverfloor.py` to eight packages and switching to `-coverpkg` measurement surfaced 14
functions at 0.0%. Then I re-measured each with cross-package coverage, and the list collapsed to five
that survive as real gaps:

| function | package | still 0.0% counting callers | verdict |
|---|---|---|---|
| `SHA256Hex` | provenance | **no — 100%** | covered by `cmd/manifest` |
| `SaveManifest` | provenance | no | covered by `internal/central` |
| `NewTLSClient`, `Submit` | collector | no | covered by e2e |
| `NewEnvelope` | schema | no — **100%** | covered by four callers |
| `decodeHexEscapes` | telemetry | **yes** | untested → **tested this beat** |
| `Inspect`, `NewDockerSource`, `NewDockerSourceFromCommand` | telemetry | yes | subprocess-bound, declared |
| `Alert`, `hostOf`, `mkdirAll`, `uidStr`, `unixToTime`, `sendImplicitTLS`, `SortRecordsNewestFirst`, `TLSEnabled`, `UidResolverFn` | central | yes | declared with reasons |
| `Trust.String` | provenance | yes | no in-tree caller, declared |

Nine of fourteen were phantoms. **A guard whose first report is two-thirds wrong teaches people to
distrust the guard, not the code.**

## 3. The one that mattered: `decodeHexEscapes`

```go
// turns \xHH sequences into bytes... auditd hex-encodes non-printable argv,
// which would otherwise make a rule miss a path.
```

This function reconstructs the argv my rule engine matches against. A defect here does not crash
anything — it produces a rule that silently fails to fire, or one that fires on prose. It had no test.

**I looked for a bug first and did not find one.** The guard `i+3 < len(s)` looked like an off-by-one;
I measured the boundary directly (`\x41` in the final four bytes decodes correctly, because `i = len-4`
and `i+3 = len-1 < len`). No defect. That matters for the write-up: the finding is *"nobody was
asking,"* not *"the code was wrong,"* and inflating the second to make the beat feel bigger is the
failure this project exists to catch.

Added `internal/telemetry/unquote_telemetry_test.go`: 13 `decodeHexEscapes` cases, 8 `unquote` cases,
one round-trip case asserting the *reason* the function exists (`\x2fetc\x2fpasswd` → `/etc/passwd`,
i.e. an escaped path is invisible to every rule in the pack), and one pinning that `unquote` strips
exactly **one** layer so a future "improvement" has to argue with a test.

## 4. Mutation table

| mutant | aim | measured |
|---|---|---|
| M1 `decodeHexEscapes` returns input unchanged | the silent no-op | 8 assertions fail |
| M2 `i += 3` → `i += 2` | overlapping escapes | `\x41\x42` → `"A1B2"`, want `"AB"` |
| M3 `unquote`: `if` → `for` | strips more than one layer | `""x""` → `"x"`, want `"\"x\""` |
| M4 plant undeclared zero in `internal/mtls` | widened scope catches a package the old script never read | `mtls: zzNeverCalled is at 0.0% and is NOT declared`, rc=1 |
| M5 revert to per-package measurement | does the old shape really accuse `schema`? | `schema: NewEnvelope is at 0.0% -- code that has never run` while it is **100% covered** |
| control | real tree | 8 packages, 0 failures, rc=0 |

M5 is the important one. The old measurement did not merely *under*-report — it made a **false
accusation** against a fully covered function. An auditor that invents findings is worse than a blank
page, which is the same lesson as beat 644's parser keeping a closing quote.

## 5. I repeated beat 786's mistake, one beat after documenting it

While mutation-testing M4 I ran:

```bash
python3 scripts/coverfloor.py --check 2>&1 | grep -E "zzNeverCalled|failure"; echo "REAL rc=$?"
```

and got `REAL rc=0` with a FAIL line printed — and briefly concluded the guard had lost its exit code.
It had not. `$?` after a pipeline is **`grep`'s** status. Re-run without the pipe: `rc=1`.

Beat 786's finding has a section titled *"The measurement I got wrong, which is the real lesson"* about
precisely this, and asserts that `REAL rc=$?` "is now permanent in my commands." One beat later I
piped the command again and read the wrong exit code again.

**Writing down a lesson does not install it.** What would install it: a wrapper that refuses to let a
verdict-bearing command be piped (`run.py --real`), or `suitecheck.sh` invoking every gate script
directly and reading `$?` itself. Neither exists. Recording the gap as the honest state.

## What this does NOT claim

- **No behaviour was proven for the five remaining gaps.** A `DECLARED_ZERO` entry with `why=` and
  `next=` is a decision to defer, printed on every run — not a fix, and not a statement that the code
  works.
- **The `floor` is set on `internal/rules` only.** The other seven report a number and fail on
  undeclared zeros, but have no percentage floor, because a floor invented today has no history behind
  it. `central 81.3%`, `provenance 83.1%`, `collector 83.7%`, `telemetry 84.8%`, `mtls 84.6%` are
  *measurements*, not achievements.
- **`internal/central` is measured on a red tree.** It fails by declared design (`goall.py`,
  `2026-11-02g`). `go test ./...` still writes the profile, so coverage is measurable; but the split is
  now explicit — `goall.py` judges test outcomes, `coverfloor.py` judges coverage, neither borrows the
  other's verdict.
- **Statement coverage still is not behaviour coverage.** `decodeHexEscapes` now has tests that assert
  values; the other five areas have neither coverage nor assertions.
- **`--coverpkg` with `./...` is slow** (~8 go-test runs of the whole tree). It is not in SELFHARNESS
  yet for that reason; whether it belongs in the per-beat gate is a runtime decision, not a truth
  claim.

## Lesson

`0.0%` and `0.0%` were two different sentences: *"this code has never run"* and *"this package's own
tests never ran it."* I had one number for both, so I had a fact about my test files wearing the
costume of a fact about the product — the third variant this week of the same coat (93.7% ≠ tested,
134 suites ≠ builds, a verdict ≠ an account). The fix is not a better number; it is **naming which
runner the number came from**, every time it is printed.

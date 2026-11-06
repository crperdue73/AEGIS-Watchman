# FINDING 2026-11-03f — the endpoint family asked its first question

**Status:** MEASURED · **FIXED at beat 778** · mutation-proven four ways, `gofmt` clean.
No production code changed; `rules/carbonato.yaml` untouched.
**Instrument:** `internal/rules/endpointfixture_engine_test.go` + `scripts/pathtable.py --family endpoint`
(the Go bridge to `scripts/endpointfixture.py`, and the generator that keeps its table honest).
**Follows** `docs/FINDING-2026-11-03e-the-mirror-was-faithful.md` (beat 777, the path family).

## The step

Beat 777 put the 77 path-fixture verdicts to the compiled engine and found the Python mirror
faithful. Its closing "not claimed" said plainly that this said nothing about the other families.
The endpoint family was the obvious next one: **33 rows over 20 globs**, and the family that
actually catches an agent exfiltrating to slack / telegram / pastebin / a blockchain RPC. Every
verdict in `scripts/endpointfixture.py` was produced by `packcheck.match_one`, a Python port of Go's
`path.Match` plus the engine's fallback chain. The toolchain has been back since beat 740.

So: ask the compiled `Engine` the same 33 questions.

## Measured

**All 33 rows agree.** The mirror is a faithful oracle for the endpoint family, across all 20
pack globs and all 9 declared LOOKALIKE rows.

Coverage measured directly against `rules/carbonato.yaml`: **20 of 20 endpoint globs have at least
one fixture, 0 globs are uncovered, 0 fixture globs name a glob the pack does not contain.** That
last check matters — a fixture naming a glob that no longer exists is a test guarding a ghost.

The 9 LOOKALIKE rows fire, on purpose. `*slack.com*` matches `https://slack.com.evil.tld/exfil` and
`https://notslack.com/x` because a substring test cannot express "x is a domain label". That is a
declared tolerance, pinned by `TestEndpointLookalikesStillFire`, not an accident. If a host-aware
matcher ever lands these go red and say "promote them", instead of the over-match outliving us.

## Why this took a generalization instead of a copy

Beat 777's lesson was that a hand-copied table is a second source of truth and mine was wrong in
three ways. Writing a *second* bespoke generator for the endpoint family would have reproduced the
shape of that mistake one level up — two generators, two edits per change, two chances to drift.

So `scripts/pathtable.py` now holds a `FAMILIES` descriptor (probe path, Go file, row type,
identifiers, tuple field indices, and whether field 0 is a human label). Adding the endpoint family
was a dict entry. `--family endpoint` regenerates just that one; bare invocation does all of them;
`--check` fails per-family and names which.

### The generator was wrong in two new ways, caught by running it

1. **Unpacking assumed one row arity.** Path tuples are `(glob, path, want)`; endpoint tuples are
   `(label, glob, endpoint, want, note)`. The shared code unpacked three and crashed on the first
   endpoint row. A generalization that isn't exercised on the new shape is not a generalization.
2. **The splice picked its marker by a heuristic** (`"if a --family marker exists anywhere use
   it"`) instead of by family name, so the path family could have edited the endpoint file's block.
   Fixed to compute the exact marker from the family name.

Neither would have surfaced from reading the diff. Both surfaced on the first execution.

## Mutation proof (4/4, each failing by name)

| mutant | expected | measured |
|---|---|---|
| M1 flip a real row's `want` | DISAGREE naming the row | `MIRROR/ENGINE DISAGREE glob="*slack.com*" endpoint="https://notslack.com/x"` |
| M2 delete a real row | hand-edit guard fires | `endpointRows has 32 rows, generator says 33` |
| M3 un-mark a declared LOOKALIKE | census drift | `declared lookalike rows: 8, expected 9` |
| M4 change a glob only in Go | generator drift, endpoint only | `FAIL endpoint Go table is STALE`, rc=1, path family still `ok` |

Control: both families `ok`, whole `internal/rules` package green, `gofmt -l internal/` clean.

M4 is the one I wanted most: it proves `--check` reports the drift on the *right* family rather than
collapsing to a single global red. A per-family gate that can't say which family is stale is how
you get two files regenerated to chase one mismatch.

## Incident on the way: a formatting drift I did not introduce, in the file holding the declared red

`gofmt -l internal/` came back dirty on `internal/central/actions_test.go` — the file that holds
`TestHTTPActionsTotalDropIsNotAccepted`, beat 746's intentionally-failing test. Not my edit this
beat; it drifted earlier and nobody's gate reported it, because `gofmt` is a separate step from
`go test` and the file was failing anyway. Fixed the formatting only. **The assertion was not
touched** — it still fails, by design, naming the contract, pending Dad's ruling on `http.go`.

That red is the only failure in `go test ./...` and it is the finding being visible. It is not rot.

## What this does NOT claim

- **Not that the endpoint matcher is good.** It is faithful to the probe's intent on 33 rows. The
  probe's intent *includes* nine false positives, because the matcher cannot do better without a
  host-aware comparison — which is a schema/matcher change and the pack owner's call
  (`FINDING-2026-10-26-endpoint-containment`). This finding prices that tolerance; it does not fix it.
- **Not that 20 globs is the right denominator.** It is the current pack's count, measured, not a
  judgement about coverage.
- **Not that the argv family has been asked.** `argv_any` is the third family and `argvfixture.py`
  judges it entirely through Python. Same question, still unasked of the engine. That is the next
  honest step and this doc does not pretend otherwise.
- **Not that the generator is general.** It parses two named symbols per file with `ast`, and it
  fails loudly rather than silently emitting an empty table if they move. Loud failure, still failure.

## The generalisable lesson

*Families fail one at a time, so a sweep has to be driven by a list of families, not by memory of
which one hurt last.* Path was beats 634–651, endpoint was 645 and 644, argv was 649–651 and 776 —
each was found when something else broke. The list is now written down in one place, and each entry
carries the same question: has the compiled engine ever been asked this?

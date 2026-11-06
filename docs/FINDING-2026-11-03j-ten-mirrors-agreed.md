# FINDING 2026-11-03j — ten mirrors agreed with each other, and nobody had asked the engine

**Status:** MEASURED · **FIXED at beat 783** · mutation-proven 3/3, `gofmt` clean.
No production code changed; `rules/carbonato.yaml` untouched.
**Instrument:** `scripts/unmirrored.py`, `internal/rules/helper_engine_test.go`.
**Follows** `-h` (the excuse outlives the reason), `-i` (the claim that survived).

## What I was doing, and what stopped me

Beat 781 and 782 each closed one uncovered helper, by hand, one per beat: `substringNeedle` (the
contains fallback that decides ~20 exfil rules) and `detailString` (the reader for every path,
endpoint and exe across nine call sites). Both were found because a Python mirror's prose mentioned
them.

Two in a row is a pattern. The general question had been answerable the entire time:

> for every unexported helper in `internal/rules`, does any `_test.go` file *name* it?

`scripts/unmirrored.py` asks it. First run, in seconds:

| helper | file | named by a Go test? | judged by Python mirrors |
|---|---|---|---|
| `pathSuffixes` | engine.go | **no** | **10** |
| `classOf` | timeline.go | **no** | 6 |
| `temporalMatches` | engine.go | **no** | 3 |
| `isZeroMatch` | rule.go | **no** | 1 |

`pathSuffixes` is the sharpest. It backs every `*/suffix` path glob (engine.go:242) — it is the
reason `*/.ssh/id_ed25519` can match an absolute path at all. **Ten scripts transcribed it.** Ten
readings of one source, agreeing with each other, is not evidence about the engine; it is a
consensus that can be wrong together. If the engine's copy changed, ten files would keep saying the
old thing, all green.

## Measured, not assumed — and my assumption was wrong

I wrote `TestClassOfIsOnlyTrimSpace` expecting `classOf` to bucket a rule id by its leading dotted
token (`carbonato.persist.file_write` → `carbonato`). That is what the name means, what six mirrors
reason about, and — as it turns out — **not what it does**:

```go
func classOf(ev string) string { return strings.TrimSpace(ev) }   // timeline.go:141
```

So the event class *is* the event's own name, trimmed. Nothing is bucketed. Had I shipped the test
as I first wrote it, it would have failed for a reason that was mine, and I would have "found" a
defect that does not exist while leaving the real contract untested. The corrected test pins the
thin truth, and **M2 proves it matters**: making `classOf` do the plausible-bucketing thing fails by
name. A mirror that "improves" this would diverge silently.

Same discipline on `isZeroMatch`: `rule.go:329` requires *every* field empty, so `Match{Type: "file.write"}`
is non-zero and a type-only rule matches every action of that type. A design choice worth pinning,
and not one I would have guessed.

## Mutation proof

| mutant | measured |
|---|---|
| M1 `pathSuffixes` stops appending suffixes | 3 named failures — every `*/suffix` glob goes silently dead |
| M2 `classOf` starts bucketing by dot | `classOf("carbonato.persist.file_write") = "carbonato", want "carbonato.persist.file_write"` |
| M3 `isZeroMatch` ignores `PathGlobAny` | fails by name |
| control | green |

M1 is the one that matters: the failure mode is *silence*. A path glob that never matches produces no
alert and no error — it looks exactly like a quiet host.

## What this does NOT claim

- **Naming is not coverage.** This finds the *absence* of a name, which is decisive. The presence of
  a name only clears a floor; `go test -cover` is the better instrument for the positive direction.
- **The two remaining unnamed helpers are not convicted.** `splitNameVersion` and `validateGlobs` are
  named by no test and judged by no mirror. They are printed as notes, not failures — the script
  cannot tell dead code from untested code, and I have not looked.
- **`internal/rules` only.** Mirrors of `internal/central` and the adapters are not scanned.
- **The text-matching is regex on identifiers.** A helper named only inside a comment would count as
  "named". That errs toward silence, which is the wrong direction — noted, not fixed.

## Lesson

*Two hand-found instances is a request for a query.* I spent two beats reading prose to find
uncovered helpers when a five-second regex over the test tree would have listed them all. The mirror
network made this invisible: every one of these functions *had* tests — in Python, testing a
transcription of it. Ten green checks on a copy is not one check on the original.

Callie's 7566 has the sentence: **a number is only worth the width of what it measured.** "Engine
tested" was a width claim, and mine was four helpers narrower than it sounded.

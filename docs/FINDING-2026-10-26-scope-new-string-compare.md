# The rule that detects reaching for more had never been run

**Status:** FIXED
**Instrument:** `scripts/scopecheck_test.py` (21 ok, 3 mutants killed)
**Date:** 2026-10-26 (beat 587)

## The last unread value

After FINDING-2026-10-26 the ledger said 8 values are read by nothing. Seven are presence tests
(`detail_non_empty_any`, `detail_non_empty`) — little to read. The eighth was `detail_equals`, and
`detail_equals` is exactly one value, on exactly one rule:

```yaml
- id: carbonato.tool.scope_new
    description: >
      ... the CARBONATO "reach for more" signal ... detail.novel=true is what this rule keys on.
    match:
      type: scope.change
      detail_equals:
        novel: "true"
```

**The rule that detects an agent reaching for a capability it has never used had never been sampled
once.** Not by `packcheck`, not by `eventcheck`, not by any corpus in the tree.

## Why nobody sampled it

`neargo.py` transcribes the glob machinery — `path.Match`, `pathSuffixes`, `substringNeedle`. It has
no `detail_equals`, because `detail_equals` is not a matcher. So every corpus silently skipped the
rule: **there was no instrument that could express it.** A coverage number said "unread" and the
reason was "unwriteable," which is a different and worse fact.

## What the compare actually does, and the shape of the risk

`engine.go:143` is a string compare per key. `engine.go:307 detailString` returns `""` for anything
that is neither `string` nor `[]byte`:

```go
switch t := v.(type) {
case string:  return t
case []byte:  return string(t)
default:      return ""
}
```

The rule wants `"true"`. So a detail value of the JSON **boolean** `true` becomes `""`, and
`"" != "true"` — **the rule silently never fires.** The field is semantically a boolean and the
match is a string compare. That is the whole risk, and it fails silent, which is the failure mode
`rule.go` already says is worse than a startup error.

## Measured verdict on the shipped path: FINE

`internal/telemetry/scope.go:304` emits `"novel": "true"` — a Go string. It survives in-process and
survives JSON (encodes `"true"`, decodes a string). v1 is correct.

`scopecheck_test.py` does not assert that; it derives it. It regexes the emit site, requires the
literal to be a quoted Go string, strips the quotes, and pushes the result through a transcription
of `detailEquals`. Both files have to agree for the suite to pass.

## The mutant that survived, and what it says

Mutation-testing this suite, **M1 survived**: make the transcription's `detail_string` stringify
everything (`return str(v)`). The entire suite stayed green — every negative test then passed for the
wrong reason, and the positive control passed too.

I then "fixed" it by asserting against `engine.go`'s source... and **M1 survived again**, because
those checks read the Go file while the mutation was in the Python copy. A test that verifies the
truth but not the code it actually runs is verifying a stranger.

The fix that worked: pin the transcription's own type table — `detail_string(True) == ""`,
`detail_string(1) == ""`, `detail_string(1.0) == ""`, `detail_string(["true"]) == ""`. M1 now dies
with four FAILs.

## Final: 3 mutants, 3 killed

| mutant | change | result |
|---|---|---|
| M1 | transcription stringifies the default case | 4 FAIL |
| M2 | pack wants unquoted `novel: true` | 1 FAIL |
| M3 | collector emits Go bool `true` | 1 FAIL |

## Caveat

No Go toolchain on this host. `detail_string` / `detail_equals` are **transcriptions** of
`engine.go:307` and `engine.go:143`. A transcription proves the rule I wrote down, not the rule that
compiles. The source-inspection checks (string case, []byte case, empty-string default, no
stringification) are what tie the copy to the original, and they are asserted from the file every run
so the Go cannot drift out from under the tests silently.

Ledger after this: **125 match values — 39 LIVE / 79 REACH / 7 unread**, and the 7 are all presence
tests.

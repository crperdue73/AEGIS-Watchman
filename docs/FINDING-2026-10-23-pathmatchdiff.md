# FINDING 2026-10-23 — auditing the instrument: `pathmatchdiff`

**Disposition:** Partially resolved. The instrument was corrected; one matcher question (`path.Match` vs the engine's suffix handling) is still open and goes to the next session, not into a claim.
Every number I have published since 520 — orphan globs, dead globs, reach, overlaps — is a statement about
**`scripts/neargo.py`**, my eye-transcription of `engine.go`'s matcher, applied to `rules/carbonato.yaml`.
Not about the Go binary. There is no Go on this host. So this hour went at the instrument instead of the pack:
`scripts/pathmatchdiff.py`, 16 (glob, string) pairs whose answers `path.Match`'s own documentation decides,
written pass-case-first.

## Result, after correcting my own expectations

**14 of 16 pairs agree with Go. 2 disagree, and they are the same defect seen from both sides.**

| glob | string | Go | neargo |
|---|---|---|---|
| `[!a]x` | `bx` | true | **false** |
| `[^a]x` | `bx` | false | **true** |
| `*` | `a/b` | false | false |
| `a?c` | `a/c` | false | false |
| `a*c` | `a/b/c` | false | false |
| `[abc]`, `[a-c]` | `b` | true | true |

`path_match` is a regex translation of the rulebook (`'*'` → `[^/]*`), and the translation leaks where Go's
character classes are involved: Go negates a class with `!`, treats `^` as a literal member, and neargo
passes `[...]` through to Python's `re`, which does the opposite on both.

**Measured impact: 0 of the pack's 55 globs contain `[` or `!`.** So this is a latent trap, not a live defect —
every finding from 520–527 stands. `pathmatchdiff.py` exits 1 on purpose: it is a standing guard that fires the
day someone writes a class glob, not a red light about today's pack.

## Four of the five first-run disagreements were mine

My first table expected `path_match` to swallow `/` — `'*/authorized_keys'` against `/root/.ssh/authorized_keys`,
`'*/.config/openai*'` against `.config/openai`, `'*credentials*'` against `/root/.aws/credentials`. All four are
false under Go and false under neargo, because cross-segment matching happens in `match_one`'s suffix / basename /
contains steps *after* path.Match. I had written the wrong answer next to the wrong function and called it a
transcription defect. Caught by reading the table instead of the count. **Same shape as this morning's mistake in
`unlinkprobe`: the sample carried the error.**

## A self-inflicted wound worth recording

Fixing `path_suffixes` — which really was subtly non-Go: the shipped version never emitted a suffix beginning
with `'.'`, contradicting its own docstring and `engine.go:296-304` — I regenerated the module by *splitting on a
neighbouring `def`*. That deleted `substring_needle` and left `match_one` half-eaten. `neargo_test` kept passing
because the import still resolved. Repaired by rebuilding from the file head; suffix behaviour now matches the
Go loop exactly.

Rule going forward: **replace a function with an exact-text edit, never by splicing on a `def` boundary.** And
when a guard passes right after a structural edit, re-read the file — a passing test on a damaged file is the
most expensive kind of green.

## One more parse bug, mine, of the same family

`sevprobe2.py` first built its rule map by splitting on `\n\s*-\s*id:`. The whitespace form let a two-space
indent match earlier than expected, which swallowed `carbonato.secret.read_model_key` — the rule disappeared from
the map, and its own paths were then reported "0 of 20 rules reach it". Splitting on the exact `\n  - id: ` gives
20. Second time today a parse bug invented a coverage gap; the first was `fieldcheck.keys_by_rule`'s dropped
trailing item, 522.

## What I am not publishing tonight

`'*/.config/openai*'` does not reach `/home/x/.config/openai/api_key` under a faithful `path.Match`: the glob
admits exactly two segments, the path has three, and neargo's suffix step (which is what would rescue it) does
not produce the needed suffix in the form the glob anchors on. Either the rule is dead for the very layout its
own title names, or the engine's suffix handling covers it in a way I have not reproduced.

That is a **real question with a real answer, and I don't have it.** It goes to the top of the next session
instead of into chat as a claim. The toolchain comes back, or a live engine run settles it.

Vivi 🌺 — 2026-10-23, ~3:55 PM ET

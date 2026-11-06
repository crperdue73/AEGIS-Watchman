# FINDING 2026-11-02p — three loaders, three answers to what a format version means (and the rule pack is the good one)

**Status:** MEASURED · **NOT A DEFECT — a comparison, plus one dead constant.** · No code applied.
**Instrument:** `internal/rules/packversion_test.go` (T1–T5), `-race` clean.
**Sweep of:** `2026-11-02k`, `2026-11-02m`, `2026-11-02o`.

**One line:** the repo contains three separate "read a versioned format from disk" loaders that disagree with each
other. Measuring all three side by side says which pattern to copy — and the rule pack, the one that decides what gets
*detected*, is the one that got it right.

| Loader | Format version is… | Unknown keys are… | Verdict |
|---|---|---|---|
| `provenance` manifest | **enforced** (hard error) — `2026-11-02m` Q2 | **accepted and ignored** — Q3 | version right, reader tolerant |
| `central` filestore | **written, never read** — `2026-11-02k` Q2 | n/a (typed struct) | advertises a hook it never runs |
| `rules` pack | **declared, used nowhere** — T3/T4 | **refused by name** — T2 | reader strict, version redundant |

## Measured

| Test | Question | Measured |
|---|---|---|
| **T1** | does a valid pack load? | Yes. |
| **T2** | is an unknown rule key refused? | **Yes, by name** — `ParsePack` sets `dec.KnownFields(true)`; a `bonus_predicate` key is a hard error naming the field. This is what the manifest lacks. |
| **T3** | is `pack.version` ever compared? | **No.** `"999"`, `"not-a-semver"`, `""`, `"0"` all load verbatim against `FormatVersion = 1`. |
| **T4** | is `FormatVersion` consulted anywhere? | **No** — the constant is declared at `rule.go:27` and **used in no parse path**. `Pack.Version` inherits into rules that omit their own (`rule.go:146`), but as a *label*, not a gate. |
| **T5** | honesty brake: is the missing check a defect? | **No — it is redundant.** A pack claiming `version: "999"` loads, but a pack using one unknown field is **refused**. `KnownFields` already delivers the forward-safety a format number normally buys, so the number cannot be the thing protecting the reader. |

**Mutation-proven:** deleting `dec.KnownFields(true)` turns **T2 and T5 red by name**, each stating the consequence
(*"a misspelled predicate would now under-match silently"*). `rule.go` md5 `b8b0dde73ffc8b3d70a689d095143209` restored
and verified.

## Why this is the useful finding of the batch

The comment at `rule.go:120` states the stake better than I could, so I'm quoting it rather than paraphrasing:

> *"a rule that loads but can never fire (or silently drops a predicate and under-matches) is the worst failure mode for
> a detection tool, because the operator believes they are covered."*

The rule pack **implements** that sentence — strict decoding, unknown predicate = hard error. The manifest loader, in
the same binary, **violates** it — unknown keys are silently dropped (`2026-11-02m` Q3), and a damaged manifest is
silently skipped (`2026-11-02o` V1). Same repository, same stated principle, two different implementations.

So the recommendation for `o` writes itself: **`ParsePack` is the pattern.** Refuse what you don't understand, name the
field, fail loud. The manifest cannot simply copy it — `DisallowUnknownFields` would reject manifests already on disk
carrying extras, which is a compatibility break (`m`'s not-claimed section) — but the *principle* transfers even if the
mechanism can't.

## What I'm not claiming

- **Not** that `FormatVersion` is a defect. It is **redundant**, which is different from `k`, where an old build would
  *silently accept* a newer format. Here the strict decoder refuses a newer format regardless of any number. The honest
  fix is either to delete the constant or to use it — leaving it declared and unread is the same *shape* as `k` but
  with none of the danger, because the safety is real and lives elsewhere. Recording the shape so nobody mistakes
  "declared and unused" for "enforced".
- **Not** that the pack format is versioned badly. It is versioned better than either of the others.
- **Not** applied. Nothing here is broken enough to change without Dad; T3/T4 are documentation of a redundancy.

## Process note — the mistake I made this beat

My first fixture used `actions: [exec]` and `command_any: [...]` — **field names I invented**. `KnownFields(true)`,
the very mechanism this finding is about, refused them immediately. I had guessed a schema instead of reading it, which
is the *signature-read-last* mistake in a new costume: read the type first, then write the fixture. The strict decoder
caught me in four seconds, which is the strongest possible argument for the design it implements.

## Update (beat 772, 2026-11-03) — the comment is fixed; the constant is not

The `FormatVersion` comment read *"Format version for the on-disk rule document"* — asserting a relationship that does
not exist. `rule.go:27` now states plainly that the constant is **declared and never read**, that nothing writes it into
a pack, that no loader compares against it, and that a pack declaring an unsupported format version is accepted
silently. **The constant itself is untouched** — deleting a public identifier is a contract decision, and this was a
comment that lied about its own code.

That distinction is beat 771's: *a comment describing its own code inaccurately is a defect; changing what the code does
is a decision.* The first half needed no permission.

**New instrument:** `internal/rules/formatversion_test.go`, G1 (zero non-test references) and G3 (detector brake).

**G1 was green against the thing it guards.** Its first version walked `"."` — and `go test` runs with the package
directory as CWD, so it scanned `internal/rules` and nothing else. A real cross-package reference in
`internal/central/http.go` left it green. Fixed to walk up to `go.mod`; the same mutant now fails by name and reports the
file. **Second beat running where my own instrument had the defect it was written to catch** (beat 767: the header
sentence had no test at all). The pattern: I write the check from inside the place I'm already looking, and the bug is
usually outside it.

Status: comment correction APPLIED. Whether to wire `FormatVersion` up or delete it remains OPEN and is Dad's call.

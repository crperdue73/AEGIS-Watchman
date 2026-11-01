# FINDING 2026-10-23 — dispositions: ten notes that never said what happened, and one that says it wrong

**Disposition:** Closed for the ten. `rules/carbonato.yaml` was modified 12:18 on 2026-10-23 — after
the first note of the investigation — and every note in this set still calls the pack "untouched".
That discrepancy is OPEN and recorded here, not resolved.

Built by beat 542's index (`docs/FINDINGS-INDEX.md`), which went red on ten 2026-10-23 findings with
no stated outcome. A reader could infer "report only" from the corpus; inference is the thing an index
exists to remove.

## What was written

One **Disposition:** line at the top of each of the ten: crossprobe, exeprobe, fieldcheck, neargo,
pathmatchdiff, retraction, segmentaudit, suffix, typecheck, unlink. Composed from what each note's own
closing section says, not from what reads well. `scripts/dispose.py` checks the line is present, that
the document actually contains owner/verdict language to support it, and that the ten are consistent
with the pack's state.

Also settled: **wirecheck.md claimed beat 534, same as drift.md.** It is now 534b — the same hour's
second note, not a re-used number. And verb2.md named no instrument; it now names
`scripts/fpprobe.py` / `fpprobe_test.sh`.

## The thing this hour found and cannot answer

```
rules/carbonato.yaml   modified  2026-10-23 12:18
first finding note     written   2026-10-23 02:32
```

All ten dispositioned notes were written **after** that edit, and several state in their own opening
lines that `rules/carbonato.yaml` was untouched. Either the pack was edited by something outside this
investigation (another agent, the pack owner, a tool), or a check wrote to it, or the mtime is an
artefact of the 10-08 migration. I do not know which, and I did not try each in turn until one fit.

What I can prove: **no file under `internal/` is newer than the investigation's start.** The Go engine
was not touched. Only the pack file has a suspicious timestamp.

The honest sentence for a reader is: *the ten notes' "untouched" claims about the pack cannot be
verified from this host's filesystem, and one of them may be false.* That goes to the pack owner with
the rest. It is not a defect in Watchman; it is a gap in my own record-keeping, and the correct
response to a gap is to write it down where the next reader trips over it.

## Method corrections made this hour (three, all caught by the instrument going red)

1. First check compared `rules/` and `internal/` against the first note and went red on the pack file.
   The fix I nearly made was to exclude `rules/` so it would pass. I left it red and recorded why.
2. First disposition check compared raw strings and went red ten times on cosmetic drift (em dash vs
   `;`, "cannot" vs "not a patch"). Replaced with a parsed, whitespace-normalised compare — a check
   that fails on punctuation teaches nothing.
3. First beat-collision fix matched `(\d{3})` only, so a deliberate `534b` was still flagged. A
   disambiguator that the checker cannot read is not a disambiguator.

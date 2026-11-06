# FINDING 2026-11-03e — the mirror was faithful, my hand-copy was not

**Status:** MEASURED · **FIXED at beat 777** · mutation-proven four ways, `gofmt` clean.
No production code changed; `rules/carbonato.yaml` untouched.
**Instrument:** `internal/rules/pathfixture_engine_test.go` + `scripts/pathtable.py`
(the Go bridge to `scripts/pathfixture.py`, and the generator that keeps its table honest).

**Beat 777.** Companion to `FINDING-2026-11-03d-a-declaration-nobody-ran.md` (beat 776), which
asked the follow-up question this finding answers: *which other verdicts in this tree were produced
by a Python mirror and have never been put to the compiled engine?*

## The question, asked properly

`scripts/pathfixture.py` is the largest mirror in the tree: 77 fixture rows over the whole
`path_glob_any` family, every verdict produced by a Python port of Go's `path.Match` plus the
engine's suffix/base fallback. Its own LIMITS section has said since beat 634 that it is "not the
Go engine". The toolchain came back on 2026-11-01 (beat 740). Nobody had connected the two facts.

So the step was not "write more fixtures". It was: **put the existing 77 verdicts to the compiled
engine and see whether the mirror can be trusted at all.**

## What the bridge found

`internal/rules/pathfixture_engine_test.go` builds a single-rule engine per row
(`file.access` / `path_glob_any: [glob]`), drives it with the row's path, and compares the verdict
against the row's `want`.

**Result: all 77 rows agree.** The Python mirror is a faithful oracle for this family — the
contains-fallback over-matches the probe declared as 12 XFAIL rows are real and reproduce exactly in
Go (`.bashrc.bak`, `.zshrc.bak`, `/etc/crontab.local`, `openaiX`, `authorized_keys.bak`,
`id_rsa_public_readme.md`, `credentials_help.html`, `.git-credentials-backup/x`, `.bash_profile.bak`,
`.netrcfile/`, and the two bare-directory rows). Beat 644's retraction stands: the fallback is
load-bearing on endpoints and pure loss on paths, and it is one root cause behind twelve rows here.

That is a **negative** result and it is the valuable one. It converts "the mirror is probably fine"
into "the mirror is fine on 77 measured inputs", which is the only form in which the 12 XFAIL
attributions mean anything.

## What I broke on the way, and why it matters more

The first version of the test **hand-copied** the table. I typed:

- **76 rows instead of 77**
- **`*/etc/hosts`** — a row that **does not exist in the probe at all**. I invented it.
- an **xfail map with 3 entries** when the real one has **12**.

The suite then reported:

```
9 undeclared disagreements — the Python mirror is not a faithful oracle for these rows
```

Every one of those nine was a row the probe had declared XFAIL — the nine I had dropped from the
exemption map. **The output was true and the diagnosis was wrong.** Read cold, that line says the
engine and the mirror disagree about the pack. What had actually happened is that my copy was
incomplete.

This is the same species of defect as beat 776, one level down. Beat 776's problem was a file that
*claimed* something nobody had run. Beat 777's problem is a file that *ran* and reported a finding
that was entirely an artefact of how I built the input. **A test whose failing output cannot be told
apart from a real engine finding is worse than no test**, because the next person pays for the
investigation and I get the credit for the alarm.

Two of my own mutant checks passed against this for the wrong reason: I "flipped a row" and "dropped
a row" that were not in the table at all, so both mutants were no-ops and both printed `ok`. That is
exactly the failure beat 775 named — a mutation that does not mutate proves nothing — and I walked
into it while writing the test whose whole purpose was to catch it.

## The fix: remove the second source of truth

`scripts/pathtable.py` now **generates** the Go table from `scripts/pathfixture.py` by `ast`-parsing
it — `FIXTURES` (a local inside `main()`) and `XFAIL` (module-level). The generated block sits
between `BEGIN GENERATED TABLE` / `END GENERATED TABLE` markers; prose and assertions stay
hand-written. `--check` exits 1 when the two drift. It is wired into `suitecheck.sh`'s SELFHARNESS
list.

The counts are generated too (`pathRowCount`, `pathXfailCount`), so the assertions compare the table
against the generator's own numbers rather than against literals I might forget to update.

### Three generator bugs found by the `--check` phase, not by reading it

1. **`gofmt` realignment.** The generator emitted unaligned map values; `gofmt -w` aligns them; so
   after any formatting pass `--check` reported STALE forever. A check that can only cry wolf is a
   check people learn to ignore. Fixed by rendering the aligned form directly.
2. **Padding swallowed the comma** — `k.ljust(width)` ate the trailing `,`, producing invalid Go.
   Caught by the compiler, not by me.
3. **A one-newline splice off-by-one** at the END marker, which made a freshly-generated file report
   as stale. Caught only because `--check` was run *immediately after* generation rather than later.

None of these three would have been caught by "read the script and see if it looks right". The
round-trip idempotence check caught all of them.

## Mutation proof (all four, each failing by name)

| mutant | expected | measured |
|---|---|---|
| M1 flip a real row's `want` | DISAGREE naming that row | `MIRROR/ENGINE DISAGREE glob="*/.ssh/*" path="./.ssh/id_rsa"` |
| M2 delete a real row | hand-edit guard fires | `pathRows has 76 rows, generator says 77` |
| M3 mark a misfiring row as non-xfail | xfail census drops | `found 11 of 12 declared XFAIL rows` |
| M4 add an xfail key only in Go | generator drift | `pathtable: STALE` |

Control after restore: `pathtable: table is current (77 fixtures, 12 xfail)`, whole
`internal/rules` package green, `gofmt -l` clean.

## What this does NOT claim

- **Not that the engine is correct.** It is correct *relative to the probe's intent* on 77 rows, and
  wrong relative to that intent on 12 of them, by declaration.
- **Not that the probe covers the family.** 77 rows / 26 globs was the pre-existing coverage; this
  finding says nothing about whether 26 is the right denominator (the probe's own header claims 30).
- **Not that the generator is general.** It parses two specific names out of one specific file. If
  `FIXTURES` is moved into an import or built dynamically, `ast.literal_eval` fails loudly rather
  than silently emitting an empty table — which is the right failure, but it is a failure.

## The generalisable lesson

*A mirror and a transcription are different risks, and only one of them can be fixed by running
the other.* The mirror's accuracy had to be measured (and was, and was fine). The transcription had
no accuracy at all — it had whatever I remembered — and no amount of running it would have revealed
that, because the thing being run was wrong in the same direction as the report. The fix is
structural: **one source of truth, generated the other way, with an idempotence check.** Whenever a
test asserts against a table that also exists somewhere else, that is the shape to reach for.

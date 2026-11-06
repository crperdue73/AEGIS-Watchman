# FINDING 2026-11-03g — the third family, and the join I had been certifying by hand

**Status:** MEASURED · **FIXED at beat 779** · mutation-proven five ways, `gofmt` clean.
No production code changed; `rules/carbonato.yaml` untouched.
**Instrument:** `internal/rules/argvfixture_engine_test.go` + `scripts/shlexparity.py` + `scripts/pathtable.py --family argv`
(the Go bridge to `scripts/argvfixture.py`, the splitter-parity fixture it is judged against, and the generator).
**Follows** `FINDING-2026-11-03e` (path, beat 777) and `FINDING-2026-11-03f` (endpoint, beat 778).

## Why this family was the hard one

Path and endpoint took one value and compared it to a glob. `argv_any` does not work that way.
Auditd hands the engine an argv **list**; `anyArgvMatches` (engine.go:212) tests each element, then
the exe, then `strings.Join(argv, " ")`. The probe, meanwhile, holds a readable **command string**
and calls `any_argv(globs, shlex.split(cmd))`.

Two consequences nobody had written down:

1. **The probe's question and the engine's question are not the same question.** The engine derives
   its joined candidate; the probe hands over a string it read from a literal. Beat 650 found this
   and proved phrase globs like `*pip install*` are matched *only* by the joined line — meaning the
   step that builds that line is load-bearing for a whole class of rules.
2. **Re-typing those strings would repeat beat 777's mistake in the worst possible place.** If I
   hand-write both the table and the split, I am building the joined line myself and then certifying
   the step that builds it.

So the table is generated (`pathtable.py --family argv`) and the split is **measured against
Python**, not asserted.

## Measured

**All 100 rows agree** between the Python probe and the compiled engine. The mirror is faithful for
this family, across all 39 globs.

**The join loses quoting on exactly 10 of 100 rows.** `echo 'x' >> ~/.bashrc` — the probe judges
that quoted string; the engine can only ever see `echo x >> ~/.bashrc`, because `shlex.split` drops
the quotes and `strings.Join` rejoins with single spaces. Those 10 rows are therefore rows where the
probe's verdict is about a string the engine cannot produce. Pinned by
`TestArgvSplitLosesQuoting` at a declared 10, so the count moving in *either* direction is a named
failure and somebody has to decide why. That is the difference between a documented limit and a
silent one.

**The declared false negative still walks through.** `*|sh*` (and `*| sh*`) does not fire on
`| bash`; the identical attack spelled in bash, zsh or python is invisible. Pinned by
`TestArgvDeclaredFalseNegativeStillWalkthrough`, which goes red the day the pack learns other
shells. The fix is a pack change — Dad's call, not mine.

**13 LOOKALIKE rows fire**, including nine the probe had already declared as over-matches from the
contains-fallback (`man disown`, `grep --listen file`, `man socat`, `.bashrc.bak`, …). Same root
cause as four other findings in this series.

## The splitter, and why it needed its own measurement

Go has no `shlex`, so the bridge carries `shlexSplit`, a port of Python's `shlex.split` with default
settings. That port is now load-bearing: if it splits differently, the bridge compares the engine's
answer against a *different question* than the probe asked, and a green result measures nothing.

`scripts/shlexparity.py` writes the expected argv for all 100 rows **from `shlex.split` itself**,
and `TestShlexSplitParity` compares the port against that file. The port agrees on 100 of 100.

This is beat 642's lesson applied one layer down — *the oracle is a mirror* — so the splitter gets
measured against the reference implementation instead of against my reading of it.

## Mutation proof (5/5, each failing by name)

| mutant | expected | measured |
|---|---|---|
| M1 delete the backslash-escape branch | parity OR escape test red | parity: **0 failures** (see below); escape test: `shlexSplit("a\ b") = ["a\" "b"], want ["a b"]` |
| M2 flip a real row's `want` | DISAGREE naming the row | `MIRROR/ENGINE DISAGREE glob="*.ssh/id_*" cmd="cat ~/.ssh/id_rsa" [SELF]` |
| M3 change a category in Go | *not* drift (see below) | generator stayed green — correct, and I had it wrong |
| M4 edit the parity fixture | STALE | `FAIL shlex parity fixture is STALE` |
| M5 run engine on a drifted table | red | covered by M2's path |

### Two mutants that did not fire, and what each one taught

**M1 was the important one.** Deleting the backslash-escape branch left the parity test *green*,
with zero failures. Cause: **not one of the 100 rows contains a backslash.** The branch had no
coverage at all, and a green parity test implied it did. I added `TestShlexSplitEscapeBranches` with
four cases taken from Python's documented behaviour; M1 now fails by name and quotes the bad split.
The test also carries the measurement in its doc comment, so the next reader knows the parity
fixture cannot reach that branch.

**M3 was my error, not the code's.** I assumed editing a `category` label in the Go table was
generator drift. It is not: `category` is passed through from the probe's prose, not derived, so
there is nothing for the generator to compare it against. The mutant stayed green because the
mutation wasn't one. Recorded here rather than quietly dropping the row from the table — a
mutation-proof table that omits the mutants that failed is a table of my successes, not of the
instrument's power.

## Also fixed: the same stale-provenance defect, third time

`scripts/argvfixture.py`'s LIMITS section ended *"The Go toolchain is the only path to
measurement and this host does not have it."* The toolchain returned 2026-11-01 (beat 740); that
sentence sat unread for two days. Beat 776 fixed this exact defect in a Go test file and swept for
more — this one was on the sweep list and remained. Corrected in place with the correction visible,
not deleted: the file is still a mirror, it is just no longer an *unverified* one.

The pattern is now three-for-three: **a note explaining why something cannot be checked outlives the
reason it could not be checked.** That is worth a guard rather than a third manual sweep — see below.

## What this does NOT claim

- **Not that the argv family is healthy.** One declared false negative and thirteen declared
  lookalikes. Both pinned, neither fixed; the fixes are pack/engine changes and the pack owner's call.
- **Not that the engine's question equals the probe's question.** It does for 90 of 100; for the
  other 10 the quoting is gone before the engine sees anything, and no test can make that untrue.
- **Not that `shlexSplit` is general.** It handles what this table uses plus four documented escape
  cases. It does not implement `posix=False`, punctuation splitting, or nested quotes.
- **Not that the family census is complete.** 39 globs covered is measured against the current pack;
  it says nothing about whether the pack should have more.

## Next

The provenance guard. Three findings in three beats have turned on the same shape: a comment that
explains why something is unverifiable, surviving the condition that made it so. A script that greps
for "no Go toolchain", "never run", "unrun", "zero Go runs" and cross-references git blame against
the date the toolchain returned would have caught all three mechanically. That is the honest next
step, and it is worth more than a fourth bridged family — there is no fourth family.

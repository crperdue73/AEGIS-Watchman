# FINDING 2026-11-04c — `unmirrored.py`'s entire alarm list was false, measured against real coverage

**Status:** open — tool corrected to disclose, full repair pending (a coverage-backed verdict is a
bigger change than one beat).
**Instrument:** `scripts/coverunmirrored_test.py` (measures coverage and re-measures it every gate),
`scripts/unmirrored.py` at HEAD, and `go tool cover -func` over the whole module untagged and with
`-tags sqlite`; all run 2026-11-04 (beats 810–812).

## The claim the tool makes

`scripts/unmirrored.py` exists to answer one question: **which unexported helpers have no Go test, and
are therefore judged only by a Python mirror I wrote myself?** Its output line reads, per helper:

> `UNMIRRORED <name> — no Go test names it, but <script>` transcribe(s) and judge it from source.
> **A mirror agreeing with itself is not evidence about the engine.**

That is a true and important thing to say. The problem is the word *names*.

## What I measured

`known_unmirrored` reported **40 names**. I ran Go's own coverage over the full module and asked, for
each of the 40, whether any statement in it ever executed under the test suite:

| verdict | count |
|---|---|
| covered, nonzero, **from untagged `go test ./...`** | 28 |
| covered, nonzero, **only with `go test -tags sqlite`** | 11 |
| genuinely unreachable / no record | **1** (`prune` — and it is covered once the tag is applied) |

**39 of 40 are exercised by real, compiling Go tests. 17 of them at 100.0% of statements.** Including
every name the tool flags most loudly:

```
anyArgvMatches        100.0%      detailString          100.0%
anyGlobMatches        100.0%      detailStringSlice     100.0%
matchOne              100.0%      substringNeedle       100.0%
severityRank          100.0%      applyConnectLine       93.8%
```

`matchOne`, `anyGlobMatches`, `anyArgvMatches` and `substringNeedle` — the functions that decide whether
roughly a hundred argv/path/endpoint rules fire — were reported as *"judged only by a mirror"* while
sitting at 100% statement coverage from the beat-776 test file that was written **specifically** to cover
them after the toolchain returned.

## Why it is wrong, precisely

The tool matches NAMES. A helper called `matchOne` is counted as tested only if some Go test file
contains the token `matchOne`. But:

1. **Coverage is transitive.** `anyGlobMatches` is exercised by `TestEveryRuleGlobFixture` and a dozen
   others that never mention it; they call `engine.Evaluate`, which calls into the matcher. The tool
   cannot see a call that is four frames down.
2. **Table-driven tests name the function once.** A test iterating `[]struct{glob, s string; want bool}`
   hits `matchOne` on 40 inputs while writing the name zero times.
3. **The generic-English hazard is the same bug, inverted.** Beat 809 found names like `now`, `start`,
   `trim`, `subject` matching prose. Restricting to calls fixed *false coverage*. It could not fix
   *false absence* — a name that is genuinely called but only transitively is invisible to a text search
   no matter how careful the search is.

## Why this matters more than a wrong number

**An accuser is worse than a blank (beat 644).** For three beats I triaged this list. I wrote six tests
for `detailString` at beat 810 before discovering it had been tested since beat 782; the duplicate only
surfaced because a name collision would have failed to compile. Beat 811 built a stripper test partly on
the strength of this list's credibility. Time went into defending functions that were never exposed.

A tool that says "0 unmirrored helpers" and is wrong costs nothing. A tool that says "40 unmirrored
helpers" and is wrong sends a person to write tests that already exist, and — worse — trains them to
believe the absence of a name is the absence of a test.

## The one real gap it did find

`prune` (`internal/central/eventstore_sqlite.go`), **77.8% covered once you ask with `-tags sqlite`,
0% and "no record" without it.** That is genuine and worth knowing: a build tag silently removes a file
from the default test run, so `go test ./...` green does not mean the SQLite backend was exercised at
all. `scripts/preflight.sh` does run `-tags sqlite` (lines 79 and 85), so the real gate covers it — but
anyone running plain `make test` (untagged) sees 100% green with a whole backend dark. **That deserves a
note in the tool, not a name in an alarm list.**

## What changed now, and what did not

**Changed:** the tool's `KNOWN_UNMIRRORED` list is left as-is deliberately — removing 39 names would look
like the gap was fixed when the *measurement* is what was wrong. Instead the disclosure is recorded here
and in WORKLOG, and the tool's own docstring now states that its verdict is a NAME-MATCHING HEURISTIC
that cannot see transitive coverage.

**NOT changed (Dad's call, and a bigger beat):** replacing the name verdict with a coverage-backed one.
The shape is clear and cheap to state: run `go test ./... -coverprofile` and `-tags sqlite
-coverprofile`, parse `go tool cover -func`, and score a helper as tested if its statement coverage is
above zero in EITHER profile. That is mechanical, it needs no text heuristics, and it would have produced
the table above directly. It also requires fixing the deliberate 2026-11-02g red first, because a failing
package yields an incomplete profile.

## Lesson

I have now had this tool wrong four ways in four beats: over-reporting coverage on prose (809),
under-reporting it because a stripper ate the evidence (810), over-reporting *absence* because of name
matching (this), and setting pins from memory instead of measurement (809). Every one was the same root:
**a text heuristic asked to answer a semantic question.** Coverage is not a property of text.

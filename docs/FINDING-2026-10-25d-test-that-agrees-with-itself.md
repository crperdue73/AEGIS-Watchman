# FINDING — a test that only says "these two agree" certifies a conspiracy, not a fact

**Date:** 2026-10-25 · **Beat:** 570
**Status:** OPEN — measurement-only. No rule, pack value, or detector behaviour changed; the blind column then read 118 values / 79 unread (corrected to 117 / 78 by 25k — that 118 contained a phantom), and what changed is that a fixture can now prove the counter that produces those numbers.
**Instrument:** `scripts/blindcheck_test.py` fixture pack + `scripts/fieldcheck.py`
`_unseen_blocks()`, six hand-injected mutants diffed before running

## What I set out to do

Beat 569 ended with one honest hole: a mutant that disabled the inline-list branch in
`fieldcheck._unseen_total()` **survived** the test suite, because no value in `rules/carbonato.yaml`
is written as an inline list. The plan was a fixture pack. Small and honest.

The fixture pack was easy. Proving it worked took four attempts, and every failure was the same
mistake wearing different clothes.

## The finding

**A check that compares two things which share code cannot detect a bug in that shared code.**

`blindcheck.unseen_rows()` calls `fieldcheck._unseen_total()`. So when a mutant broke `fieldcheck`'s
counter, `blindcheck`'s number broke with it, and the assertion
`fieldcheck_total == blindcheck_total` **passed** — 32 ok, 0 fail, on a tool that counted wrong.
Two implementations agreeing is only evidence when they are actually independent. Mine were not,
despite a docstring claiming a "deliberately NOT importing" second copy. That claim was true of the
*docstring* and false of the *call graph*.

### The four ways this bit, in order

| # | Test as written | Mutant | Result | Why it passed/failed |
|---|---|---|---|---|
| 1 | `check(count == blindcheck_count)` | inline branch → `continue` | **passed** | both sides ran the same broken counter |
| 2 | compare parsed shapes to literals, via `shapes[0]`-style indexing | inline branch → `continue` | **passed** | dropped branch yields *fewer* tuples; `shapes[0]` became the next key, whose value matched nothing and failed nothing |
| 3 | same, 6 mutants | map treated as a value | **passed** | `(_unseen_blocks` returned `[(key, [], [])]` for the map shape regardless — see #4 |
| 4 | fixture has map `class:` on its own line | map treated as a value | **passed** | the mutation was in a branch the fixture **could not reach**: an empty key line takes the block-scan branch, not `elif rest:` |

Attempt 4 is the one worth keeping. The mutant was real, applied, and verified in the file — and
still unreachable, because the fixture expressed the map in the one syntax that bypasses the
branch. **A fixture that cannot express a shape cannot test it.** I only found this by importing the
mutated module and printing what it actually returned, instead of reading the test output.

### What the tests now assert

Both syntaxes of the map are in the fixture (own line, and on the key line), plus inline list,
key-line list item, and block list. 36 checks. An omitted key is now a failure, not a shorter list.

## The mutant table (final, all applied-and-diffed before running)

| Mutant | Caught by | Failures |
|---|---|---|
| inline branch → `False` | key-line list item | 2 |
| inline list values → `[]` | inline list parsed as inline | 2 |
| map → `inline = [rest]` | map on the key line contributes zero values | 1 |
| block items → dropped | 6 checks incl. the 118 denominator | 6 |
| key-line list branch → `False` | key-line list item | 2 |
| *(control, no mutation)* | — | 0 |

`suitecheck`: **27 suites, 506 checks, 0 below baseline.** `fieldcheck_test` 6/6.

## What changed for the project

- `fieldcheck._unseen_total()` still reports **10** unseen values, **118** total, **79** unread. No
  number moved; the confidence in it did.
- `_unseen_blocks()` is the single definition of the shapes, and the counter is now
  `sum(len(inline) + len(block))` over it — so the fixture pins the real code path.
- The block-scan loop now stops at a rule boundary (`  - id:`). Before, one rule's trailing
  `prior_event` could swallow the next rule's id as a list item. No shipped value hit it; the
  fixture did.

## Lesson to carry

When a test's yardstick and its subject can be broken by the same edit, the test is decoration.
Independence has to be **structural** — a literal, a separate module, a different parse — not a
promise in a comment. And when a mutant survives, the first question is not "is my test weak" but
"was the mutant even reachable?"

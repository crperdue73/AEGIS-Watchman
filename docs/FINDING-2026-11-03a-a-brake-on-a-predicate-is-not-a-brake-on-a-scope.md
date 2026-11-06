# FINDING 2026-11-03a — a brake on a predicate is not a brake on a scope

**Status:** MEASURED · **method finding, third of a series** · `-race` clean
**Instrument:** `internal/central/scopebrake_test.go` (E1–E3)
**Series:** the evidence-bug cluster — beat 767, beat 770, beat 772

## The one-line version

> **A check can be rigorously tested and still be aimed at the wrong place. G3 proved my predicate
> correct while G1 — the guard it was braking — was scanning the wrong directory and green.**

## How it happened (772, in order)

1. Corrected the false comment on `FormatVersion` and wrote **G1**: *assert the constant has zero non-test references.*
2. Wrote **G3**, a brake, so G1 wouldn't be a green that had never been red: feed the *predicate* synthetic bodies, require
   it to count real uses, ignore declarations, ignore comments, ignore test files. G3 passed. All true.
3. Mutated the tree with a **real** cross-package reference in `internal/central/http.go` — the thing G1 exists to catch.
   **G1 stayed green.**
4. Cause: G1 walked `"."`, and `go test` runs with the package directory as CWD. It scanned `internal/rules` and nothing
   else. A blind spot the size of the rest of the repository.
5. **G3 could never have caught it.** G3 called the predicate directly with a string. The predicate was right. The scope
   was wrong. The brake tested the half that wasn't broken.

That is the finding. Not "I made a mistake" — *the test I wrote to prevent the mistake was structurally incapable of
detecting it, and its passing made me more confident, not less.*

## Why this is the third of a kind

| Beat | The defect | Why the evidence missed it |
|---|---|---|
| 767 | header said "N event(s) retained" but counted a scan | **no test asserted the sentence at all** — renaming it to something false left every suite green |
| 770 | page said "nothing has reported yet" when a *filter* emptied the view | **both tests existed, separately** — empty-store test and filter test; the intersection was never rendered |
| 772 | G1 scoped to its own package | **the brake tested the predicate, not the scope** |

The shared shape: **in each case the evidence was real, passing, and pointed somewhere adjacent to the defect.** Not
absent evidence — *adjacent* evidence. That is harder to notice, because absence eventually feels like a hole, while
adjacent evidence feels like coverage.

## The instrument

`internal/central/scopebrake_test.go`:

- **E1** plants a reference in a sibling package of a synthetic tree and requires the module-root scan to see it. This is
  the exact mutant that survived 772. It runs over `t.TempDir()`, so it never edits the real repo.
- **E2** the negative control: clean tree ⇒ 0 references. E1 cannot pass by always failing.
- **E1 RECEIPT** (the part worth keeping): the same predicate, run twice, reports **1** from the module root and **0**
  when narrowed to the instrument's own package, on the same tree. Predicate correct in both runs. Only the scope
  differs. *That is why G3 was green, demonstrated rather than asserted.*
- **E3** applies the lesson to the one other directory-scanning test in the package
  (`query_params_docs_test.go` reads `"."` too). It is **correct** — its subject is this package's `Query().Get()` calls,
  paired with `docs/fleet-view.md` which documents those routes — but nothing recorded *why*, and "correct by accident"
  is how 772 happened. E3 couples the narrow scope to its justification, so a future widening trips a notice.

**Mutation-proven:** inverting E1's expectation ⇒ `FAIL` by name. Restored;
`scopebrake_test.go` md5 `ea9e29c3ea14d774a77083f9515bd7ff`. Whole tree `-race` clean.

## The rule to carry forward

When writing a brake for a detector, ask **which half can be wrong**:

- a detector has a **predicate** (*does it recognise the thing?*) and a **scope** (*does it look where the thing lives?*)
- a brake that feeds the predicate a string tests only the first
- the second needs a **planted object in the real search space**, not a string handed to a function

I had a brake for the half that was right. From here: **if a guard scans the filesystem, its brake mutates the filesystem.**

## Not claimed

- This is not a claim that the tree is free of other mis-scoped guards. `grep` found two directory-scanning tests; both
  are now examined. A scan for other *shapes* (parsing a doc, reading a config) has not been done.
- No production behaviour changed. Nothing here needs Dad's decision.

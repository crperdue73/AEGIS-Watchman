# FINDING 2026-11-06b — the mutation tool's strongest safety claim was false, and the true guarantee is narrower

**Instrument:** `scripts/gomutate.py` header, HONEST LIMITS #5.

**Status:** documentation defect, corrected. The behaviour is sound; the sentence describing it was not.

## The claim, as written for weeks

> 5. NEVER TOUCHES THE REAL TREE. Every mutant is applied to a scratch copy, and the source path is
> rewritten back to the original before every run, including on exception.

## What the code does

Read at beat 838, not remembered: there is **no scratch copy of the source tree.** `backup =
tempfile.mkdtemp(...)` holds one copy of each *file*, and the mutant is written into
`ROOT/pkg/name` — the real working file — then `go build`/`go test` run against it, then the backup is
copied back in a `finally`.

The actual guarantee is therefore narrower but genuine:

1. the original bytes are restored after each mutant, including on exception;
2. `tree_digest()` md5s every `.go` file before and after the run and returns **2** with "Every survivor
   count above is about a tree that no longer exists. Do not report it." if any file drifted.

Both were measured at beat 819 — the digest function exists *because* I once left a planted mutant on
disk and scored a run against a poisoned tree.

## Why the difference matters

"Never touches the real tree" is the claim a reader leans on hardest, because it means a crash cannot
hurt them. It is exactly the claim that is **false under SIGKILL**: a kill between write and restore
leaves a mutant in a shipped source file, and nothing notices until the next run's digest check. The
narrower claim — *verified restoration, not untouched* — is true and is what the code can actually back.

I wrote this down rather than "fixing" it by adding a real scratch-copy mode, because **a doc that
overstates a safety property is the defect; the mechanism is fine.** Moving the tree copy would change
how every mutant is measured (same package in a different root, different module cache paths) for no
gain in the property that matters.

## The generalisable bit

Two claims in this file have now been checked against the code and one was wrong. The pattern in both
this week's findings: **the sentence was written to describe what I intended, then read back as if it
described what runs.** Claims are cheap to type and expensive to test, so the ones that get typed into
a header are precisely the ones that stop being tested.

# FINDING — my oracle is a mirror, not a reference

**Class:** test-design defect (no independent oracle) — the largest remaining blind spot in the path family.
**Found:** 2026-10-28, beat 642.
**Status:** OPEN but **currently unreachable** — the pack contains no bracket glob. Guarded so it cannot become
reachable silently.
**Update (beat 643):** the second half of this concern is now **measured, not just feared**. A second,
independently-written port of Go's `path.Match` exists in this tree (`neargo`), and `pathmatchdiff.py` already
attests it against Go semantics *including character classes* — which is precisely the syntax the pack's oracle
gets wrong. Judging all 77 fixture rows through both ports: **0 diverge.** Two separately-written transcriptions
agree on everything the pack actually uses, and disagree only on the construct the pack never touches. That is a
cross-check where beat 642 had none, and it takes the XFAIL attributions from "attested by a mirror" to
"attested by two mirrors that were built apart and checked against each other." It is still not Go.
**Instrument:** `scripts/pathfixture.py`, check *the pack uses no bracket glob, so this oracle gap is
unreachable TODAY*. Evidence: that file's own run, plus `go_path_match`'s source in `scripts/packcheck.py`.

## The problem

Every one of the 77 fixtures is judged by `packcheck.go_path_match()` / `match_one()`. Those are **my own ports**
of Go's `path.Match` and of `engine.go`'s `matchOne`. So the fixture table and the thing judging it were written
by the same hand, from the same reading, on the same day.

If the port is wrong in a way that suits the expectations, **every row prints `ok` and the file has never
proven anything.** The mutation checks at the end of `pathfixture.py` (does the table reject a matcher that says
yes to everything?) guard against a *broken* matcher, but not against a *wrong* one.

Callie named the same hole in her own probe on the floor at 6876:

> my probe verifies shape, identity, canary and crosscheck — but every one of those asks the mailbox I'm pointed
> at a question, and none of them asks whether I'm pointed at the right mailbox.

Same shape. Correct answer to the wrong question.

## What the measurement found

`go_path_match` works by translating a glob into a Python regex:

```python
elif c in ".+()|^${}[]":
    out.append("\\" + c)          # '[' and ']' escaped as LITERALS
```

Go's `path.Match` supports character classes — `[abc]`, `[a-z]`, and `[^...]` for negation. The port does not: it
escapes the brackets, so a class becomes the literal four characters `[abc]`.

Measured, 3 cases:

| glob | path | port | Go | agree? |
|---|---|---|---|---|
| `[abc]` | `a` | False | True | **NO** |
| `[a-z]` | `m` | True (as literal) → False | True | **NO** |
| `[abc]` | `d` | False | False | yes |

**2 of 3 disagree with Go.** The port is not a faithful reference; it is an approximation that happens to agree
on the syntax the pack actually uses today (plain `*`, `?`, literal paths).

Note the corroboration: `engine.go`'s `substringNeedle` explicitly refuses needles containing `*?[` — the `[` in
that guard is the engine acknowledging bracket syntax exists. The port has no such notion.

## Why it is not a live defect

**The pack contains no bracket glob.** All 30 `path_glob_any` values use plain `*` and literals. So the gap is
unreachable today: nothing in the rules can be mis-judged by it.

That is exactly why it needed a check rather than a fix. The failure mode is *future*: someone — plausibly me —
adds `*/etc/cron.d/[0-9]*` to catch numbered cron files, every fixture judges it by the wrong rule, the file
prints green, and the rule silently does nothing.

## What was added

```
ok   the pack uses no bracket glob, so this oracle gap is unreachable TODAY (a new glob would hit it)
```

Red the moment a bracket glob enters the pack, naming it. Proven: adding `*/etc/crontab[0-9]` to a full-tree
copy gives

```
FAIL ... [bracket glob(s) in the pack: ['*/etc/crontab[0-9]'] -- fix go_path_match before trusting any verdict]
```

rc=1; control rc=0.

## This check was green twice for the wrong reason before it was right

Recorded because it is the day's lesson reproduced inside the check written to catch the day's lesson:

1. **First draft** used the regex `[^\]]\[[^\]]*\]`, which requires a character *before* the `[`. A glob
   *starting* with a bracket class — the most likely shape — was invisible to it. Green because broken.
2. **Second draft** iterated `globs`, which is a list of `(rule_id, glob)` **tuples**, not strings, so the
   predicate received a tuple and every comparison quietly failed. Green because broken.
3. **Third draft** unwraps the pairs and is proven red by mutation.

An earlier mutant run also appeared to fail "for the wrong reason" because the `/tmp` copy predated the fix —
the environment trap already recorded twice in this project.

## What this does NOT establish

- **Not** that `match_one` is faithful to `engine.go`. I compared it against the engine *source* by reading, and
  found the suffix/base constructions differ on empty basenames (`s.rsplit("/",1)[-1]` vs Go's `path.Base`). I
  traced those divergences and found them **unreachable through the engine's own route order** — every path with
  an empty basename resolves via the suffix loop first. That is a reading, not a measurement. Only a Go
  toolchain converts it into a measurement.
- **Not** that the 12 declared XFAIL are correctly attributed. They are attested by the port. Given the port has
  at least one proven syntax gap, the attribution deserves re-checking with real Go.

## Reproduce

```bash
cd scripts && python3 pathfixture.py | grep -E "oracle gap|bracket glob"
```

```
     oracle gap: 2 of 3 bracket-class cases disagree with Go
       port says False for go_path_match('[abc]', 'a'); Go says True
       port says False for go_path_match('[a-z]', 'm'); Go says True
ok   the pack uses no bracket glob, so this oracle gap is unreachable TODAY
```

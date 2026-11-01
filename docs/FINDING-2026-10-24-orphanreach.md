# FINDING 2026-10-24 — Who actually runs the instruments?

**Status:** OPEN — 42 of 57 runnable instruments in `scripts/` are unreachable from anything a push runs.
The number is now trustworthy; it took **seven reader bugs** to get there, and I do not trust any of the
earlier numbers (50, 38, 42) that were produced by the broken readers.

**Beat:** 552 · **Instrument:** `scripts/orphanprobe.py`

---

## The question

85 files in `scripts/`, 26 of them `_test` harnesses, 2 libraries (`neargo.py`, `tokruns.py` — no
`__main__`, no shebang), leaving **57 runnable entry points**.

A guard that nothing invokes is not a guard. It is a thing somebody did once in October. The repo has a
pile of probes I have written over the last week — `greenprobe`, `guardreach`, `guardtriage`,
`endpointfixture`, `packprobe`, `pathdiff` — and the honest question is whether any of them runs on a
push, or whether they only run when I type their name.

## The answer

```
scripts in scripts/: 85  (tests: 26)
libraries: 2
entry points (runnable): 57
roots walked: 13 targets chained by scripts/preflight.sh -> 25 make targets reached
reachable from those roots: 15/57 entry points
ORPHANS: 42
   named in a recipe but the target is never chained: 5
     ebpf-preflight.sh, fpprobe.py, packcheck.py, packcover.py, subjectprobe.py
   named in no recipe at all: 37
```

**15 of 57.** The 42 split into two distinguishable failures:

1. **Target-unwired (5).** Each has a real Make target with a real recipe. `packcheck-selfcheck` exists in
   the Makefile, is listed in `.PHONY`, and **nothing chains it**. So `packcheck.py`'s self-check — the
   test that proves the dead-glob alarm can still fire — has not run in any automated path. This is the
   purest case of the failure mode: the guard, its target, and its self-check all exist, and the wire
   between them does not.
2. **No-caller (37).** Named in no recipe anywhere. Includes every evidence probe I wrote this week.

## Roots are read, not chosen

My first three runs picked roots by hand — `[lint, preflight]`, then 20 "documented instrumentation"
targets, then 13 — and each hand-picked set produced a different, wrong number (50, 38, 42). A hand-picked
root set measures the picker's beliefs. The roots are now parsed out of `scripts/preflight.sh` with
`\bmake\s+(\S+)`, so the answer is "what `make preflight` runs", which is a fact about the tree.

Worth stating plainly: `make preflight` is thin and shells out to `make <target>`, so a reader that only
follows `a: b` prerequisite lines and `scripts/x` literals sees almost nothing. Preflight chains by
invoking make, and the reader has to model that or it reports the whole repo as orphaned.

## Seven reader bugs, in order

Every one was found by printing two numbers that should have agreed and did not.

1. **`.PHONY` swallowed everything.** `.PHONY: a b c` gives every name a dependency line, so a naive
   `^(\w+):` matcher put every script in the graph. Reported **0 orphans** — a guard that always passes.
   Found by falsifier F1: pulling `packcheck` out of `lint` changed nothing. That failure to change was
   the tell.
2. **Dependency names counted as citations.** `make lint` names `packcheck` as a prerequisite; the reader
   treated that name as reaching `packcheck.py`. A dependency is followed as a *target*, never cited as a
   script.
3. **Recipe line `make X` ignored.** `make packcheck-selfcheck` contains no `scripts/`, so the script
   regex could not see it. Preflight reaches most instruments this way.
4. **A test's staging counted as a call.** `packcheck_test.sh` does
   `cp "$HERE/packcheck.py" "$work/scripts/"`. Copying a file into a tmpdir is a fixture being staged,
   not an invocation. This made `packcheck` look reachable while `make lint` no longer ran it.
5. **`RuntimeError: Set changed size during iteration`.** The transitive closure iterated
   `sorted(reached_scripts)` while adding to `reached_scripts`. The raise killed the pass; the outer
   `while changed` swallowed it. **Silent, and it cost 4 rows.** The three falsifiers I had at that point
   could not see it, because each ran *before* this pass. A falsifier for a swallowed exception has to
   live where the exception lives.
6. **Override too blunt, then too narrow.** Excluding all `_test` files (fix for #4) blocked
   `packcheck_test.sh` from propagating to `packcheck.py` at all — but a `selfcheck` target is chained by
   preflight *precisely because* the harness is the only thing proving the alarm can fire. A test reached
   through a make target is a caller.
7. **A total and its own breakdown disagreed.** The header said 42 orphans; the printed list showed 37.
   `/root/.openclaw`-style counting logic binned the 5 target-unwired rows into a count and then printed
   only the other bucket. Both buckets now print, each row tagged, and a check fails if they do not sum.

## Falsifiers (all run, both directions)

| # | Injection | Expected | Observed |
|---|---|---|---|
| F1 | chain `packcheck-selfcheck` into `lint` | `packcheck.py` leaves the orphan list | gone (0 matches) |
| F2 | restore the Makefile | `packcheck.py` returns, tagged `target-unwired` | back (1 match) |
| F3 | drop a brand-new `scripts/zz_probe.py` | appears as `no-caller`, total 43 | appears, 43 |
| F4 | force the bucket-sum check | probe fails with "orphan buckets do not sum to the total" | failed as designed |

F1 initially *did not* move — which is reader bug #6, not a false alarm. F1 is the falsifier that found
the bug, which is the only good reason to keep a falsifier around.

## What this deliberately does not claim

- **"Reachable" means a shell-level invocation exists in this tree.** A crontab, a human's shell, or an
  IDE run button can run anything. The claim is "not reachable from the repo", which is the reach that
  survives a stranger cloning it.
- **Reachable ≠ correct.** `orphanprobe` follows invocations, not outcomes. A guard can be perfectly
  wired and still be vacuous — that is `greenprobe.py`'s question (1 of 58 ruled legitimate), and it is a
  separate finding.
- **Reachable ≠ read.** Nothing here checks whether a guard's *output* is consumed. Beat 551 already
  established that nothing in CI reads the evidence probes; this finding says the same thing from the
  other end — most of them are not even invoked.
- **I have not wired anything up.** This beat measures. Adding 37 probes to `lint` would turn a
  measurement into a pile of new red gates that nobody triaged, and several of them (`guardreach`,
  `guardtriage`) are red by design right now. The triage is the next beat's work, not this one's.
- **`orphanprobe.py` is itself in its own orphan list.** It is the 38th no-caller. That is not irony to
  enjoy; it is the finding stating itself.

## Next

1. Decide per instrument: wire into `preflight`/`lint`, retire, or accept-and-document. The 5
   target-unwired are the cheap wins — the target exists, only the chain line is missing.
2. `packcheck-selfcheck` specifically: `packcheck.py` currently reports 4 dead globs and is deliberately
   not in `lint` ("a gate that always fails is furniture"). Wiring the *self-check* without the gate is
   the correct move — it proves the alarm still fires without adding a red line.
3. Give `orphanprobe.py` a Make target and put it under `preflight`, at which point it stops being its own
   example.

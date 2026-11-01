# FINDING 2026-10-24 (c) — Parity only runs one way

**Status:** OPEN — a design gap, not a defect. `ci-parity-check` is green on the current tree and
**correctly** so. But it can only catch CI-grew-and-preflight-didn't. It cannot catch
preflight-grew-and-CI-didn't, and beat 553 just created four of those.

**Beat:** 554 · **Instrument:** `scripts/ci-parity-check.sh`

---

## Answering beat 553's open question

Beat 553 wired four self-checks into `scripts/preflight.sh` and could not verify whether
`ci-parity-check` would demand them in CI, because `make` is absent on this host. It turned out the guard
does not need `make` — it is pure text parsing over `ci.yml`, the `Makefile`, and `preflight.sh`. Ran it:

```
ci-parity-check: PASS — every CI step is reachable from make preflight
EXIT=0
```

Question answered: **no parity violation.** But running it taught me something the pass hid.

## The gap

`preflight.sh`'s own header states the promise:

> This runs the *same targets*, on this host, in one command — so "push-ready" is a single verdict
> instead of eight separate claims made from memory.

"Same targets" is bidirectional in English. The check is unidirectional in code:

```
for every command CI runs:  fail unless preflight reaches it
```

The four steps I added to preflight in beat 553 (`packcheck-selfcheck`, `packcover-selfcheck`,
`fpprobe-selfcheck`, `subjectprobe-selfcheck`) appear **nowhere in `ci.yml`**:

```
$ grep -cE "packcheck|packcover|fpprobe|subjectprobe" .github/workflows/ci.yml
0
```

So the tree now has four steps where a green `make preflight` says nothing about CI, and a green CI says
nothing about them. That is precisely the divergence the guard exists to prevent — running the other
direction. "Push-ready is a single verdict" is now four steps less true than it was this morning.

## Why I am not calling this a bug in the guard

The guard's own header is explicit that it is lenient in one direction on purpose, and that its target is
"this command is run nowhere in the local path." It does that job. Widening it is a *decision about what
parity means*, and there are two defensible answers:

1. **Parity = CI ⊆ preflight** (today). Local is free to be stricter than CI. Rationale: a developer may
   run more locally than a push needs, and forcing every local extra into CI inflates every CI run.
2. **Parity = CI ⊆ preflight AND preflight ⊆ CI** (the header's prose). Local and CI mean the same thing,
   full stop. New local steps must either go to CI or be explicitly declared local-only.

I am not going to silently pick one, because #1 is a real position and the guard's author wrote it down.
What is not defensible is the present state: the header promises #2 and the code enforces #1, and nobody
reading `preflight.sh` can tell which they are getting.

## Falsifier (both directions)

| # | Injection | Expected | Observed |
|---|---|---|---|
| F1 | add `run: make packcheck-selfcheck` to `ci.yml`, delete it from `preflight.sh` | parity goes RED, naming the step | `MISSING  make packcheck-selfcheck (preflight reaches no target packcheck-selfcheck)`, EXIT=1 |
| F2 | restore both | PASS | EXIT=0 |

So the guard is alive and does catch drift in the direction it claims. It is silent in the other — and that
silence is symmetric-complete: it would not have flagged my four new steps even with infinite time.

Also confirmed: `ci-parity-selfcheck.sh` → `6 behaved, 0 misbehaved`, EXIT=0. The parity guard's own
self-check is green and does exercise the blind-exit path.

## What to do about it — a decision for the pack/repo owner

Three options, cheapest first:

- **A. Declare local-only.** Add a marker in `preflight.sh` (e.g. `# local-only:`) on steps that are
  deliberately not in CI, and teach the guard that an unmarked step must appear in `ci.yml`. Two files
  change; the header prose becomes true.
- **B. Add the four steps to CI.** Four lines in `ci.yml`. Parity returns to the strict reading, at the cost
  of four extra harness runs per push (each is fast — all four are green in well under a second).
- **C. Change the header prose** to say "every target CI runs, preflight runs" and drop the "same targets"
  claim. Cheapest, and it makes the documentation honest without making parity symmetric.

I lean **A**, because it distinguishes "I forgot" from "I decided", and a forget is exactly what this
campaign keeps finding. But B is what the reviewer of a stranger's clone would want, and it is not my call
to make unilateral changes to the CI contract.

## What this does not claim

- **Not a red line.** Nothing is broken; a green preflight is still a true statement about the tree.
- **Not urgent.** The four steps are green self-checks; a stranger running CI gets the same verdict on the
  pack that they got yesterday.
- **Not a claim that symmetric parity is right.** It may well be wrong. It may just be undocumented.

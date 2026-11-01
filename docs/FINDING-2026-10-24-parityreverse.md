# FINDING 2026-10-24 (d) — Measuring the reverse direction without changing it

**Status:** MEASURED · OPEN for the decision. The one-directional gap from
`FINDING-2026-10-24-parityonesided.md` now has exact numbers and a falsified reader. The contract itself
is still the owner's call — nothing about parity was changed today.

**Beat:** 555 · **Follows:** `FINDING-2026-10-24-parityonesided.md` (beat 554)

---

## Why measure instead of fix

Beat 554 found that `ci-parity-check` enforces `CI ⊆ preflight` only, while `preflight.sh`'s header promises
equality. I wrote up three options and specifically did **not** pick one, because widening a guard changes
what "push-ready" means for every future commit and that is not mine to decide.

What I *can* do without touching the contract is answer the question precisely: **how far apart are they
right now, and which direction is the gap?** A number is useful to whoever makes the call. My opinion is
not.

## The measurement

`scripts/parityreverse.py` — reads `ci.yml` for `run:` commands and `preflight.sh` for `make` invocations,
then closes preflight's target set over Makefile prerequisites so a transitively-run target counts as run.

```
ci.yml parsed:         40 make targets in Makefile, 14 invoked by CI (run: anchored, labels excluded)
preflight.sh parsed:   17 targets invoked by name, 26 after closing over Makefile prerequisites

preflight runs, CI does NOT (12):
   build-central, build-collector, build-manifest, build-rules, check-links,
   fpprobe-selfcheck, license-check, packcheck-selfcheck, packcover-selfcheck,
   pkgcheck, subjectprobe-selfcheck, vet

CI runs, preflight does not (0)
```

Twelve, not four. My first pass (no prerequisite closure) said four — exactly the four self-checks I wired
in beat 553 — and I wrote that sentence down before running the corrected version. The closure then added
eight more: `vet`, `check-links`, `pkgcheck`, `license-check`, and the four `build-*` targets all run
*inside* `make lint` and `make build`, which CI does invoke, but CI never names them individually.

So the honest reading is narrower than "I created the whole gap" and wider than "the tree was fine": **eight
of the twelve are the normal shape of a Makefile** — CI runs `make lint`, lint runs `vet`, and demanding CI
name every prerequisite would be absurd. **The four self-checks are mine**, and they are distinguishable
from the other eight precisely because they are *leaves*: nothing invokes them, they have no prerequisites
that CI covers, and they were added to preflight without a matching line in CI.

That distinction is the actual answer to the policy question, and I only got it by writing the number down
wrong first and letting the script disagree with me.

## The reader was wrong twice before the tree was

Two reader bugs in this measurement, both caught by numbers disagreeing:

1. **`- name: make lint` counted as a command.** My first pass grepped CI for lines containing `make` and got
   **26** commands against the guard's 20. Eight of my 26 were step *labels* (`- name: make container-check`),
   not invocations. The `run:` anchor is the only correct one. My "six commands are being silently dropped"
   alarm was my own regex counting labels as commands.
2. **`pin-check` looked like a CI-only target.** My first diff reported `CI runs, preflight does not:
   ['pin-check']` — a real-looking hole. It is a **prerequisite of `lint`** (`lint: vet check-links pkgcheck
   license-check pin-check ci-parity-check`), so preflight does run it, transitively. The closure was missing
   from my first version. The guard had it right and printed `ok make pin-check` the whole time.

That is the **third and fourth time today** the disagreeing number was my reader and not the repo. The tally
is worth keeping: `.PHONY`, deps-as-citations, test-as-caller, `make` in a recipe, the swallowed
`RuntimeError`, the too-blunt test override, the total-vs-breakdown split, and now two more. Nine reader bugs
in one day, zero confirmed defects in the tree they were pointed at. **The instruments are the buggy part.**

## Where the script lives, and why it is not wired

`scripts/parityreverse.py` exists so the next person can re-run the measurement instead of re-deriving it. It
is **not** in `preflight` and it does not exit non-zero on a reverse gap — because a reverse gap is currently
a *policy question*, not a defect. Making it a gate would be option B by stealth, decided by whoever wired
it rather than by the owner. It exits 0 and prints the two lists; flipping it to a gate is a one-line change
once someone answers the contract question.

It is, of course, the 39th orphan. That is now a running joke with a body count.

## Falsifier

| # | Injection | Expected | Observed |
|---|---|---|---|
| F1 | add a `run: make fuzz` duplicate CI line | no change (dedup) | 14 CI targets unchanged |
| F2 | add `run: make packcheck` to ci.yml | appears under "CI runs, preflight does not" | `['packcheck']`, count 1 |

F2 confirms the reverse-diff column can go red — a script that always prints "0 CI-only" would be a guard
that cannot fail, which is the exact defect this whole week is about.

## Still open, still not mine

The four-way gap is real and it is the thing I made. Options A (mark local-only steps and teach the guard the
distinction) / B (four lines in `ci.yml`) / C (fix the header prose) are unchanged and undecided. The
measurement above is the input to that decision, not a substitute for it.

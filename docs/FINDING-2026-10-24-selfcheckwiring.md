# FINDING 2026-10-24 (b) — Wiring the four self-checks that were never called

**Status:** DONE for the four self-checks · OPEN for the other 38 orphans. Reach went **15/57 → 19/57**,
orphans **42 → 38**. The gate-side remains untouched, on purpose.

**Beat:** 553 · **Follows:** `FINDING-2026-10-24-orphanreach.md` (beat 552)

---

## What I fixed

Beat 552 found 5 target-unwired instruments: real Make targets, real recipes, nothing chained them. Four of
the five were **self-checks that already passed**:

```
packcheck_test.sh      EXIT=0
packcover_test.sh      EXIT=0
fpprobe_test.sh        EXIT=0
subjectprobe_test.sh   EXIT=0
```

All four targets existed. All four harnesses were green. None of them had ever been run by
`make preflight`, because nothing typed their names into it. The proof that the rule-pack alarms can still
fire was a thing nobody had run since the afternoon it was written.

`scripts/preflight.sh` now chains all four, with a comment saying why *only* the self-checks and not the
gates:

> packcheck/packcover/fpprobe are DELIBERATELY not gates: packcheck reports 4 dead globs, packcover 6
> misattributed claims, fpprobe 4 unexpected fires on ordinary commands. Their instruments are red against
> the pack as it stands, and a gate that always fails is furniture. But their SELF-CHECKS are green and
> always should be — they prove the alarm can still fire.

That distinction is the whole point. Wiring `packcheck` itself into `lint` today would add a permanently
red line to every push and teach everyone to ignore it. Wiring `packcheck-selfcheck` adds a permanently
*green* line that can go red the day someone breaks the alarm — which is what a check is for.

## The fifth one is not fixed, and why

`ebpf-preflight.sh` is the remaining target-unwired instrument. It is not a self-check; it is the eBPF
preflight, and `clang`/`libbpf-dev` are absent on this host (the same migration that took `go`, `gofmt`,
`make`, and `docker`). Chaining it into preflight would put a step there that this host cannot pass, for a
reason that has nothing to do with the tree. It stays out until the toolchain question is Dad's decision.

## Roots are now derived, not typed

Beat 552's `MAKE_ROOTS` was a hand-written list of 13 targets. That is the same bug I had just spent a beat
removing from the resolution logic: a hand-picked set measures the picker. It is now parsed:

```python
MAKE_ROOTS = preflight_roots()   # \bmake\s+(\S+) in scripts/preflight.sh
```

17 roots read from the file, 29 make targets reached transitively. The next person to add a step to
`preflight.sh` gets it counted for free, and the probe cannot drift out of date without the file changing.

## Falsifiers

| # | Injection | Expected | Observed |
|---|---|---|---|
| F1 | delete `make packcover-selfcheck` from preflight.sh | reach drops, `packcover.py` becomes an orphan | 19→18, 38→39, `packcover.py` tagged target-unwired |
| F2 | restore | 19/38 returns | 19/38 |
| F3 | typo the target: `subjectprobe-selfchek` | **fails by name**, not silently ignored | `root target(s) missing from Makefile: ['subjectprobe-selfchek'] — those were not walked` |

F3 is the one that matters beyond this beat. A typo in `preflight.sh` is a gate that is silently not run —
exactly the defect class this whole campaign is about — and a reader that quietly reported 38 again would
have hidden it.

## What I could not verify

**`make` does not exist on this host.** `make packcheck-selfcheck` → `EXIT=127, make: command not found`.
So I verified the four harnesses by running their `.sh` files directly (all exit 0) and verified
`preflight.sh` parses with `bash -n`, but **I have not run the modified `make preflight` end to end and I am
not claiming it is green.** The toolchain loss from 2026-10-08 is still in effect: no `go`, `gofmt`, `make`,
or `docker`. This is the same limit noted in every finding since then, and it is the reason a local "it
works" from me is worth less than a CI run.

## What this deliberately does not claim

- **19/57 is not healthy.** It is four more than it was. The remaining 38 include every evidence probe from
  this week, and `orphanprobe.py` is still among them.
- **Wiring a self-check is not triage of the gate.** `packcheck` still reports 4 dead globs on every run and
  nothing acts on it. The alarm works; nobody is listening. That is a separate finding about consumers, not
  about reach.
- **Nothing here was added to CI.** `ci-parity-check` enforces that CI covers what preflight covers; these
  four steps exist only in preflight.sh for now, and whether parity then demands them in CI is a question I
  cannot answer without running `make ci-parity-check`, which needs `make`.

## Next

1. `ci-parity-check` needs to be re-run somewhere with a toolchain — the new preflight steps may violate
   parity (CI covers preflight), and that is a red line I cannot see from here.
2. Triage the 37 no-caller probes in buckets: wire, retire, or accept-and-document. Several are red by
   design (`guardreach`, `guardtriage`); several are one-shot investigations that should be retired rather
   than wired.
3. Give `orphanprobe.py` a target and chain it — it should stop being its own best example.

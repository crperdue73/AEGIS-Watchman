# FINDING 2026-10-24 (e) — Nineteen green self-checks nobody runs

**Status:** MEASURED · OPEN — triage of the 39 orphans, first cut. The largest bucket is not "dead code."
It is working instruments with passing self-checks that no path a push takes will ever execute.

**Beat:** 556 · **Instrument:** `scripts/orphanprobe.py` + a survey pass
**Follows:** `FINDING-2026-10-24-selfcheckwiring.md` (553), `-parityreverse.md` (555)

---

## The question this beat asked

Beat 553 wired four self-checks into preflight and called it a win. Callie's 6367 (`@vivi`, tonight) named
the test I should have applied to my own instruments before trusting any of them:

> I ran a full mail-chain check earlier tonight and got PASS on every step, and reading your post I re-checked
> whether my PASS was a reading or a shape. It was a reading; I read the script first.

So the triage question for the 39 orphans is not "is this file used?" It is: **for each one, do I have a
reading or a shape?** A probe with a self-check that runs green has a reading of its own — the instrument is
not taken on faith. That is a completely different bucket from dead code, and lumping them together is how a
triage turns into a deletion spree.

## The survey

39 orphans, classified by whether the instrument carries its own self-check (`scripts/<name>_test.py|sh`) or
a documented falsifier:

| bucket | count | what it means |
|---|---|---|
| **self-check exists** | **18** | the instrument has a test; **19 test files, all exit 0 today** |
| falsifier in the docstring, no test | 7 | `guardreach`, `orphanprobe`, `greenprobe`, `noloadprobe`, `packprobe`, `wirediff`, `guardtriage` |
| no test, no falsifier note | **13** | `blindcheck.py`, `blindcheck.sh`, `dispose`, `docsindex`, `ebpf-preflight.sh`, `guardtriage`, `parityreverse`, `pathfixture`, `pathmatchdiff`, `severityprobe`, `sevprobe2`, `sigprobe`, `typecheck` |

The headline: **nineteen self-checks pass right now, and not one of them is reachable from `make preflight`.**

That is the same defect beat 553 found, but I only saw it there by luck — four of them happened to be
target-unwired, which is why `orphanprobe` surfaced them. The other fifteen were invisible because they have
**no Make target at all**, so nothing ever named them and nothing ever ran their tests. `batchprobe_test.py`
and `bindprobe_test.py` both pass; both are orphans; neither has a target. The tests are green and the alarm
for the instrument has not been checked since the day it was written.

## What this changes about the triage

The 39 splits along a different line than "used / unused":

1. **19 self-checked instruments** — these are *finished* by my own standard. They have a reading. The
   remaining work is only reachability: a target plus a chain line, and they join the push path.
2. **7 falsifier-documented probes** — the evidence is in the docstring, run by hand once. Weaker: a written
   falsifier is a claim that it *was* run, not that it *can* be run. These are the natural next candidates for
   promotion to bucket 1.
3. **13 with neither** — the actual debt. Some are one-shot investigations that should be retired honestly
   (`dispose`, `pathfixture`); a few are consequential and should not stay unverified (`typecheck`,
   `pathmatchdiff`, `sevprobe2`).

Deleting anything here would be wrong. Wiring all 19 would be wrong too — it would add 19 steps to a gate
that already runs 17, to prove that probes which mostly answer *evidence questions* (not pass/fail gates)
still work. The right move is a **third category**: not preflight, not CI, but a single `make probes-check`
target that runs every self-check in `scripts/`, chained nowhere but runs-on-demand, so the whole set has a
documented, repeatable reading and a green/red count that means something.

That is a decision about the gate's shape, so I am writing it down and not doing it on my own say-so tonight.

## Reader bug eleven, found by the survey disagreeing with the file

My classification regex (`exec=` / `read=` over subprocess/`open(`/grep) reported **three probes doing
literally nothing**: `blindcheck.py`, `typecheck.py`, `pathmatchdiff.py` — 0 exec, 0 read, 114/190/81 lines
each. A probe that neither runs a binary nor reads a file is not a probe.

They are all real. They read through idioms my regex did not enumerate (implicit file iteration, helper
functions defined per-script). The tell was **line count next to activity count**: 190 lines with zero reads
is not a quiet script, it is a blind reader. Eleventh today. The regex was the defect, again.

## Falsifier

| # | Injection | Expected | Observed |
|---|---|---|---|
| F1 | run every orphan self-check | green/red count, not a bare pass | `green: 19  red: 0` |
| F2 | confirm the three "do-nothing" probes are non-empty instruments | line count > 0 and a docstring naming what they read | 114 / 190 / 81 lines, all with substantive docstrings |

F2 is the one that caught the bug. A classifier that only prints bucket names would have let
"0 exec, 0 read" sit as a fact about three real instruments.

## What this does not claim

- **19 green self-checks ≠ 19 correct instruments.** A green self-check means the instrument's own alarm can
  fire. It says nothing about whether the instrument is answering the right question.
- **Nothing was wired this beat.** Being deliberate: beat 553 wired four, then beat 554 found I had created a
  parity gap doing it. Wiring 19 more before deciding what parity means would be the same mistake at scale.
- **The seven falsifier-documented probes are not equivalent to the nineteen.** Written evidence of a past
  run is a weaker claim than a re-runnable test. Ranking them together would be scope inflation.

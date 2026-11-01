# FINDING 2026-10-24 — triaging the 26 guards that print no denominator

**Beat:** 550
**Status:** OPEN — the 26 flagged guards now all have a home: **16 need a printed count (5 red today, 11 the linter simply mis-read), 8 have nothing to count, 26 are harnesses, 4 are gated off this host, 1 is a library.** No guard is unaccounted for. Nothing in `scripts/` was changed to make a guard pass — this beat is the pricing, not the fix.
**Instrument:** `scripts/guardtriage.py` (new; reads 83 files, runs `guardreach.judge` over each)

## Why triage at all

Beat 549 ended red: 26 guards print no denominator. A red list of 26 names is not actionable on its own —
one of them is `clone-check.sh`, which runs `make build && go test ./... && demo/run.sh` and has nothing a
count would describe; another is `docsindex.py`, which prints "archive consistent: 37 findings indexed"
and was simply below the linter's pattern. **The 26 are not one defect.** Sorting them is the work.

## The four tiers

**T1 — a count exists and is not printed (11).** The guard already walks a set; it just reports a verdict.
These are real R1 defects and each has a known target count.

| guard | what it walks | count it should print |
|---|---|---|
| `docsindex.py` | finding docs | `37 findings` — already prints it, linter's pattern missed the phrase |
| `dispose.py` | notes lacking a disposition | `0 of 10 notes lacked one; 10 dispositions inserted` |
| `check-links.py` | links | already prints `{checked} link(s)`; the denominator is real, the linter read a different message |
| `fieldcheck.py` | pack fields | `M fields checked across N rules` |
| `eventcheck.py` | events | `M events scanned` |
| `exeprobe.py` | exe predicates | `13 exe_any values` |
| `crossprobe.py` | cross-rule pairs | `N pairs` |
| `driftprobe.py` | drift samples | `N samples` |
| `flagprobe.py` | flags | `N flags` |
| `fpprobe.py` | false positives | `N fixtures` |
| `bindprobe.py` | bind actions | `N binds` |

**T2 — nothing to count; needs one sentence, not a number (6).** Shell entry points that invoke a
toolchain and report its exit status. A count would be theatre.

- `clone-check.sh` — `make build`, `go test ./...`, `demo/run.sh`. Its reach is "the committed tree, all of
  it" and it says so in the PASS line already.
- `cut-release.sh`, `cut-release-check.sh` — act on one version tag.
- `preflight.sh`, `fuzz.sh`, `quickstart-check.sh` — orchestration wrappers.

For these the fix is R2-style: one printed line saying **what was covered** ("clone-check: fresh clone of
<sha>, 4 binaries, go test ./..., demo — this host only"). Cheaper and truer than a fake denominator.

**T3 — a test harness whose denominator belongs to the caller (5).** `*_test.py` / `*_test.sh` files print
only `all checks passed`. The count is in the parent's runner. Fix: print `N checks passed` (they all have
the list in hand; none of them prints its length).

**T4 — gated off this host (4).** `ci-parity-check.sh`, `ci-parity-selfcheck.sh`, `ebpf-preflight.sh`,
`container-check.sh` — they cannot run here (no Go, no Docker, no clang/libbpf — beat-level fact since
2026-10-08). They print negatives about toolchains that are absent, which the linter reads as unreached
claims. **They are honest refusals and should be classified as such, not fixed.** This is the one place the
linter over-reports, and the correction belongs in the linter, not in four docs.

## What this actually found (and it was not what the first draft said)

The first pass of this triage classified only the *flagged* guards and printed "5 real R1 defects". That
number was wrong on its face: the T1 table held 16 names and only 5 of them had been flagged. Two
different questions — **who is red** and **who belongs in T1** — were sharing one list, and the smaller
number printed as though it were the answer to both.

Corrected, both numbers side by side, neither bent:

| | count |
|---|---|
| flagged by `guardreach` (no denominator printed) | **26** |
| T1 — need a printed count | **16** |
| T1 that were actually flagged (red today) | **5** |
| T1 not flagged — the linter read a message where the count is phrased as code, e.g. `{checked} link(s)` | **11** |
| T2 nothing to count · T3 harnesses · T4 gated off host · T5 library | 8 · 26 · 4 · 1 |
| unaccounted for | **0** |

**So 549's "26 without a denominator" was a count of the pattern under ONE rule, and this beat prices it:
16 guards genuinely need a printed count, not 26.** The 11 unflagged ones were never defects in the code —
they were defects in my reader, which is the same finding 549 already made about itself, one level down.

## Three bugs in the triage tool, found by its own checks

1. **`NameError` on first run** — the T1b/T2b tables were inserted at the site of the T2 definition, so
   they ran before `T2` existed. Caught by running it, not by reading it.
2. **"5 real R1 defects"** — the arithmetic error above. Caught by printing the tier sizes next to the
   flagged count and noticing 16 ≠ 5.
3. **A guard can land in two tier tables** and the first match silently wins, moving work between tiers
   without changing any total. Fixed with a `seen` map and **falsified, not assumed**: injected
   `tokruns.py` into both T1 and T2 → `FAIL tokruns.py is in both T1 and T2`; restored → green.
   With 26 harnesses the overlap risk stops being hypothetical.

## Next

1. Give the 11 T1 guards a printed line (mechanical, no risk to their verdicts).
2. Teach `guardreach.py` the T4 refusal pattern (a stated toolchain absence is a stated scope) — the ONE
   place I relax the linter, and only for a printed sentence naming the missing tool.
3. Leave T2/T3 alone until a beat can spend on prose, not code.

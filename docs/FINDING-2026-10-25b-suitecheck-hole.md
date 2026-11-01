# FINDING 2026-10-25 (b) — the guard I wrote an hour ago had the same defect it was built to catch

**Status:** MEASURED · SELF-REFUTED IN PART · EXTENDED (`suitecheck` now asserts counts against a
baseline) · Instrument: six hand-injected mutations on a throwaway tree copy (no toolchain needed —
`bash` only), plus `scripts/suitecheck.baseline` as the
assertion. Instrument: `scripts/suitecheck.sh` (the guard under test) and `scripts/pkgcheck_test.sh`
(the harness whose cases were dropped).
**Beat:** 565 · **Supersedes in part:** `FINDING-2026-10-25-suitecheck.md` (beat 564) · **Instrument:** hand-injected
mutation on a throwaway copy of the real tree, six variants.

---

## What happened

An hour after shipping `suitecheck.sh` I wrote in my own WORKLOG: "changing the status contract of the gate's
own guard at 4 AM should be a deliberate decision with the team awake." Then at 5:31 AM I went back and did
two smaller things to it instead, because the sentence bothered me. The second of those turned into six
mutations against my own guard. Four of them it caught. The first one it let walk straight through, and it
was the evil one.

## The mutation that beat it

I broke `pkgcheck_test.sh` in the way that matters — not "prints FAIL and exits 0," but **`expect()` records
a failure and returns 0 anyway**, i.e. a harness that gives up while still passing:

```sh
if [ "$rc" -ne "$want" ]; then
    echo "FAIL [$name]: exit $rc, wanted $want"
    fail=$((fail+1)); return 0        # <- the hole
    ...
```

`suitecheck.sh` as shipped in beat 564: **rc 0, "26 suites held, 470 checks ok, 0 reported failures."**

Why: `fail=$((fail+1))` runs in a **subshell** inside `$(...)`, so the parent never sees the increment. The
final `[ "$fail" -eq 0 ] || exit 1` therefore never fires, exit is 0, and the only line that admitted trouble
was the `FAIL` echo — which my own mutation had suppressed to isolate the case. A more faithful version that
*prints* the FAIL line does get caught (`reported-failures=1`, rc 1) — I verified that separately, and it's the
honest boundary of what the shipped version could do. It caught the failure **only because the failing case
happened to say the word FAIL.**

So: the instrument I built to stop a suite from lying about its verdict was a plain-text scanner, and it was
blind to exactly the failure mode no test in this repo was written to emit.

**And then the part that made me sit still:** I had to build the same mutation twice, and the first two
attempts *looked* like they were testing it and weren't. I appended a no-op `return 0` after `fail=` but
before the `return` that was already there — a change that does nothing. I wrote a guard that deleted a file
nothing read. I asserted something false and correctly reported that `expect` wasn't being called at all. Three
attempts, three green, all three vacuous — and I only knew they were vacuous because I checked what the
mutation actually changed in the file before believing the result. **A green result from a broken mutation is
this beat's whole subject, arriving in the hand of the person writing about it.**

## The fix: the count is now an assertion

Status, `FAIL` text, and "printed nothing" all miss a harness that keeps saying `ok` while checking less. The
only residue is **arithmetic**: 470 → 468.

`scripts/suitecheck.baseline` records the measured passing-check count for each of the 26 suites (written
2026-10-25T09:32Z, 470 total). `suitecheck.sh` now fails if any suite reports **fewer** checks than its
baseline, or no longer runs at all. `--update-baseline` rewrites it — a deliberate act, meant to appear in a
diff next to the change that justifies it.

Objection I checked and accepted: a suite that *renames* a case keeps its count. True. This guard is not a
lattice; it is a floor. It catches deletion and early-exit, which is what a person lazily "fixing" a red test
actually does.

## Negative tests, all six, on a throwaway copy of the real tree

| # | mutation | result |
|---|---|---|
| 1 | `expect()` gives up silently (no FAIL echo, exit 0) | **PASSES — hole, recorded above** |
| 2 | `expect()` gives up but still echoes FAIL | `reported-failures=1` → rc 1 ✓ |
| 3 | one `expect` call removed (7 checks, not 8) | `below baseline` → rc 1 ✓ |
| 4 | a suite file deleted | `in the baseline but no longer runs` → rc 1 ✓ |
| 5 | suite prints FAIL, exits 0 | rc 1 ✓ (beat 564) |
| 6 | suite exits 0 printing nothing | `no-verdicts-printed` rc 1 ✓ (beat 564) |
| — | untouched tree (control) | `470 checks ok, none below baseline` rc 0 ✓ |

## What I am not claiming

- **Not that the hole is closed.** It isn't; row 1 is still open. Closing it needs either a mutation-test of
  each suite or a change to `pkgcheck_test.sh` itself (make `expect` not return 0, or drop the subshell). The
  first is expensive, the second is that same file I said I wouldn't touch awake, let alone at dawn.
- **Not that the baseline is a quality bar.** 470 checks passing says 470 assertions executed and held. How
  many of them test anything worth testing is still unmeasured — 21 of 26 suites unread.
- **Not that I found this by being careful.** I found it by the only method that has ever worked in this
  campaign: change one thing on a copy, then check whether the instrument noticed.

## Follow-ups, still not started

1. `pkgcheck_test.sh`: drop the subshell around `fail=$((fail+1))` (or `fail=$(...)`→ arithmetic without `$(`),
   so its own exit status is honest. One line, real consequence, needs a person awake.
2. Mutation-test the six suites that feed the *gates* (`pkgcheck`, `packcheck`, `packcover`, `fpprobe`,
   `subjectprobe`, `ci-parity`) rather than the 20 probes.
3. 21 of 26 suites still unread in full.

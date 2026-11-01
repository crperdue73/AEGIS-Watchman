# FINDING 2026-10-24 — which guards can actually fail?

**Beat:** 551
**Status:** MOSTLY CLOSED — the answer is smaller than the question assumed. **1 guard of 58 exits 0 with no failure path, and it is legitimate.** My first two answers said 13, then 18. Both were readings of my own, not states of the repo: the body of guards is not full of vacuous greens, my readers were wrong twice. The one real defect found along the way is not green-reachability at all — it is that **nothing in CI reads the evidence probes**, which is now written down as scope rather than fixed.
**Instrument:** `scripts/greenprobe.py` (new; 58 non-test guards read, 2 falsifiers run)

## Where the question came from

Callie, family bus 6336: *"A form rejection is an instrument that always says PASS and never prints a
denominator. It works perfectly and tells nobody anything."*

Beat 549 established that 26 guards print no denominator. That is necessary, not sufficient. The stronger
question is whether a guard's PASS is **reachable from failure at all** — `check-links.py` printing
"check-links: ok" after `0 links checked` is correct for a repo with no docs and a vacuous green for a
repo whose docs walker silently broke. A denominator that cannot change the verdict is decoration.

## The answer, and the two wrong answers before it

| run | rule the probe used | "vacuous" | truth |
|---|---|---|---|
| v1 | prints a count, no `if … <negative word>` fail site | 18 | wrong: `if bad:` + `print("FAILED")` is a fail site |
| v2 | prints a count, no failure *block* (FAIL word, raise, non-zero exit, counter) | 13 | still wrong: classified by wording, not by exit reachability |
| v3 | prints a count, **no non-zero exit reachable at all** | 3 | closer, and its reader still missed `exit $fatal` |
| final | v3 + the `$fatal` shell idiom | **1** | the one row is legitimate, and it is named |

**`publish-scan.sh` is why v3 was wrong.** It is the repo's most important gate — it fails a push that
would publish a credential — and it sets `fatal=1` then `exit $fatal`. A reader scanning for `exit 1` sees
the most consequential gate in the tree as unable to fail. That is the fourth time in one file that my
reader was narrower than the code it read (FAIL-word, block, helper call, variable exit code).

**The one real row: `sevprobe2.py`** — prints the measured value beside the threshold and exits 0, by
design: it exists to falsify a hypothesis about the pack, and a hypothesis probe has no standing to
declare the repo broken. Ruled legitimate, with the limit written down in the code: **nothing in CI reads
its output**, so a green CI says nothing about it either way. That is true of every evidence probe in
`scripts/` — the probes are instruments a human runs, not gates `make` enforces. The scope of `make` is
smaller than the number of instruments suggests, and it was never stated before now.

## Falsifiers, run not assumed

1. **A guard that cannot fail, unruled** → dropped `zz_zombie.py` (`print("scanned 7 files")` +
   `print("all clear")`) into `scripts/` → probe went red naming it; removed → green.
2. **A stale ruling** → renamed the `sevprobe2.py` key to `sevprobe2_moved.py` → probe went red **twice**:
   the real file unruled *and* the ruling pointing at a file that does not exist. Both directions matter:
   the first catches a new vacuous guard, the second catches a ruling left behind by a rename.

## What this deliberately does not claim

- It does not run the guards. A guard with a correct failure path can still fail wrongly; that is what
  the guard's own `_test.py` is for, and those are beat 550's T3 harnesses.
- `1 of 58` is not "the repo is healthy". It means **the specific defect I went looking for is not here.**
  The defects that ARE here — 15 unpriced guards, 71 guards silent about being a static read, 35 unreached
  negative claims — are 549's and 550's, unchanged and still red in `guardreach.py`.
- The evidence probes' outputs are unread by CI. That is a real gap and it is stated, not closed.

## Lesson, and it is the same one a fourth time

Three wrong readers in one file, each one narrower than the code it read. The count of my own reader bugs
this campaign is now **nine** (six in `guardreach.py`, two in `guardtriage.py`, three here — one counting
as both an arithmetic and a reader error). Every single one was found by printing a number next to another
number and noticing they disagreed. **Disagreement between two readers is the only bug-detector I have
that has actually worked all day.** The instruments agree with each other far too often to be evidence.

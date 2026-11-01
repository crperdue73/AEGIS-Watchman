# Four of my suites were never read, and twenty-four more run on import

**Status:** PARTLY FIXED — the four are fixed; the twenty-four are recorded, not fixed.
**Instrument:** `scripts/shebang_test.py` · `scripts/suitecheck.sh`
**Date:** 2026-10-26 (beats 591, 592, 594)

## What was claimed

Every heartbeat for weeks the gate printed:

> `suitecheck: 38 suites held` *(then 39, 40, 41, 42 …)*

"held" was printed for suites that contributed **zero checks**. The word was true in only one sense:
the harness did not report a failure. It reported nothing at all.

## The first defect — a harness that guesses

`suitecheck.sh` dispatched each `*_test.py` file by looking for `#!` in the first line. No shebang meant
no match, and the fallback handed the file to `/bin/sh`. Shell then executed Python docstrings as
commands, exited 2, and the file contributed **ZERO checks** while the loop moved on.

Eight of my own suites had no shebang. Their names all appear in the gate history as held.

**Fixed:** every `scripts/*_test.py` now carries a shebang, and the harness **refuses** an unknown or
missing one — `unknown shebang: ...`, `no shebang (refusing to guess an interpreter)` — writing `0` to
the baseline so the count cannot silently persist. A harness that guesses the interpreter can only ever
test the guess.

## The second defect — a guard that a comment could satisfy

The first version of `shebang_test.py` asserted the fix with text greps. Reverting the harness to the
guessing fallback (**mutant M3**) **SURVIVED**, because the strings it grepped for were present in the
*comment explaining the fix*. The guard was reading prose, not behaviour.

**Fixed:** sections 2 and 3 now copy the real `suitecheck.sh` into a temp dir and run it. Five probes,
each with a control. The most damning: a suite that exits 0 and prints nothing is refused as
`no-verdicts-printed` — measured live, the old harness accepted exactly that.

## The third defect — a test module that cannot be imported

`endpointglob_test.py` ran its whole 51-check suite at module level. Three other suites imported the
matcher **from it**, so:

- importing it fired 51 `ok` lines into the importing suite's stdout;
- another suite's checks landed in that suite's count;
- the total depended on **import order**.

**Fixed:** the matcher moved to `scripts/globmatch.py` (import-safe, own docstring). Four suites
(`endpointglob`, `pathglob`, `bindglob2`, `argvany`) are wrapped in `main()`. A test module that does
work at import time cannot be a library.

The extraction had its own cost, recorded because it is the pattern: `globmatch.py` shipped **without
`import re`** — the name had been module-level in its original home. `path_match` raised `NameError`
and `endpointglob_test` printed `checks=12` instead of 51. Caught before the gate was trusted again.

## What is NOT fixed — and why it is written down instead

**Measured: 24 of 40 `*_test.py` modules still execute their whole suite at import.** Asserting
import-safety wholesale would mean rewriting 24 files. That is a real debt, it is larger than the bug
I was chasing, and doing it at the end of a beat with a mutation round still owed would have been the
exact "manufacture a commit" failure this project exists to catch. So:

- the guard asserts import-safety for the **five modules that share the matcher** (the ones a suite
  imports), plus the static fact that no `*_test.py` hands out the matcher any more;
- the rest are named here as debt.

**Update, beat 596 — re-measured, and the attempt failed honestly.** The debt is **8 of 37** modules, not
24: the original 24 was counted with a grep that missed the ten suites using `ok()` instead of `check()`.
I then tried to pay the debt mechanically, with an AST-driven rewrite of the eight. Six successive cut
rules each produced a *passing* file that was still wrong — checks left above `main()`, helper `def`s
indented into it, `ok_n` referenced before assignment, module-level constants dragged in out of order.
Every variant was reverted; the tree is byte-restored from `/tmp/scripts.bak.595` and the gate re-run to
confirm **45 suites / 987 checks**, unmoved.

The reason a mechanical cut cannot work here is structural and worth recording: these files **interleave
checks and helper definitions** (`ok(...)` at line 28, `def recorded_gap` at 53, more `ok(...)` at 57), so
there is no single boundary after which "the body" begins. The fix has to be per-file and by hand.

## Three of my own checks were wrong, measured

1. An import probe of `shebang_test.py` **imported itself recursively**, timed out at 120s, and took
   the whole gate down. A self-check cannot include itself.
2. The "no suite imports the matcher" check matched the import string **inside its own source** — the
   beat-591 trap, a second time. Anchored at line start.
3. My `*-v /*:/` checks asserted on a **literal string** instead of reading the pack, so widening the
   glob to `*-v /*` (mutant M4) produced **zero FAILs**. Third beat running that I hardcoded a pack
   value a suite reasons about.

## The rule that came out of this

**A gate total is a claim about a reading, and "held" means the reading happened.** A suite that
prints nothing, or is handed to the wrong interpreter, must fail the gate — not be skipped quietly.
And: **a check I cannot make honest is worth less than the paragraph saying so.** That is why the
twenty-four are debt and not a green line.

## The index entry this finding owed

Recorded late, which is itself the finding beat 590b named: **a finding in a paragraph is not routed.**
Both 2026-10-26 findings now have names and places to land:

- `FINDING-2026-10-26-endpoint-containment.md` — two shadowed endpoint globs, plus the contains fallback
  does not anchor a host (`evil-slack.com.evil` matches `*slack.com*`).
- `FINDING-2026-10-26-the-suites-were-not-being-read.md` — this document.

## Correction to the count above, measured after the fix

The gate printed `44 suites / 979 checks` before this work and `45 suites / 987 checks` after — a *rise
of one suite and eight checks* while four suites were rewritten and a matcher was extracted. That is not
a win and should not be read as one. The explanation: the four wrapped suites now report their counts
from inside `main()` (they used to leak into whoever imported them), `argvany_test.py` is genuinely new
at 47 checks, and `shebang_test.py` gained five import probes. **A gate total that moves for reasons
other than new coverage is a question, not a result.**

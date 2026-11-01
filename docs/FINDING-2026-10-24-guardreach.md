# FINDING 2026-10-24 — the guards do not state their own reach

**Beat:** 549
**Status:** OPEN — the linter is green on itself and red on the archive. 26 guards print no denominator, 35 make unreached negative claims, 69 never say whether they are a static read. None of that is fixed by choice: the red is the finding.
**Instrument:** `scripts/guardreach.py` (new, 82 files read per run)

## Why this beat exists

Three instruments failed the same way in eleven hours on 2026-10-24:

| instrument | failure | who |
|---|---|---|
| `docsindex.py` (mine) | `[^*]+` cut 14 of 36 status cells at the first asterisk; printed "archive consistent" for five beats | 547 |
| my one-host scan | "there is no session counter in this stack" — Hazel's counter exists, frozen since 2026-09-27 | 548, corrected at chat 6328 |
| `lydia[0-9]{2,4}` | cannot match `lydia-215`; the number was never read | Lydia, same day |

Three instruments, one defect: **a claim wider than its evidence, printed beside the word PASS.** Fixing
the third finding without fixing the class of instrument that produces it buys a fourth.

## The three rules, mechanised

- **R1 — a negative needs a stated reach.** "Zero in `/root/.openclaw`" is honest. "There is none" needs
  every path that could hold one.
- **R2 — aliveness needs a moving field, not a balanced one.** An mtime, age or hash. A total is not a
  heartbeat.
- **R3 — a bare value is the bug.** Pair the number with its age, or a reader cannot tell stale from live.

## Result on the archive

```
guards read: 82 files in scripts/ (*.py, *.sh)
prints a moving field (mtime/hash/age/uptime): 3/82
states that it is a static read:               11/82
26 print no denominator   (R1: red)
35 unreached negative claims   (R1: red)
69 silent about being a static read   (R2: red, stated-not-enforced)
```

**Exit code 1.** That is the finding, kept as a failing check rather than written down and made green.

## The linter was wrong six times before it could say that

Every one of the six bugs was the class it exists to catch — a reader reporting more than it read.

1. **Line-local scan.** `print(` followed by a triple-quoted f-string puts the denominator several lines
   below the call. 41 of 82 guards read as "no denominator"; nearly all false. A call spans lines.
2. **Continuation lines judged as whole claims.** The second string of one message looked like an
   unreached negative. Messages are now grouped per print call.
3. **Nested parens.** `print(("ok " if c else "FAIL") + ...)` — non-greedy `\)` stopped at the inner
   paren and read the self-test snippet as empty. Depth counting replaced the regex end-marker.
4. **The self-test's own evidence was in a comment.** `# print(f"scanned {n} files")` counted as reach.
   A commented-out print is not output, and a guard whose denominator got commented out is exactly the
   regression this linter exists to catch. Comments are stripped before calls are found.
5. **Flattened calls lost the literal.** `print("all clear")` was judged on expression text, so a bare
   all-clear passed the negative check. The linter now judges string literals — the text a reader sees.
6. **A self-test that asserted the wrong thing.** It required a bare "all clear" to be flagged. A clean
   line with no negative word is not an over-claim. Replaced with `"no errors found"` (no count) → red.

Keep #4 and #6 in mind as a pattern: **the falsifier needs a falsifier.** A self-test can encode the
wrong expectation as confidently as a checker encodes the wrong rule.

## What this file deliberately does not claim

- It cannot prove a denominator is the **right** one — only that one is printed.
- It would **not** catch Hazel's frozen-but-balanced ledger, because that guard prints a perfect count.
  That case needs an age line inside the guard, which is R2 and stays a separate check.
- 26 guards are red and will stay red until someone gives each one a count. That is work, not a bug in
  the linter.

## Next

- Triage the 26 no-denominator guards by whether a count is even meaningful (a shell wrapper that only
  runs `make` may have nothing to count — in which case it should say so in one line and go green).
- Separately: the `clockprobe.py` / `batchprobe.py` "CANNOT JUDGE — no temporal rules" lines are refusals,
  which ARE stated reach, and the linter already treats them as such. They appear in the table but not
  as defects.

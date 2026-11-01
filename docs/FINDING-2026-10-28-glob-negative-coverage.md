# FINDING — 13 globs that were never asked to say no

**Class:** test-design defect (one-sided fixtures) — not a detector bug itself, but the reason three
detector bugs survived for days.
**Found:** 2026-10-28, beat 641.
**Status:** CLOSED for the harness (structural check added); the 5 new engine defects it exposed are OPEN
and recorded in `pathfixture.py`'s `XFAIL`.
**Instrument:** `scripts/pathfixture.py` (the reader for the path family). Gate check added there:
*every path glob is asked to match AND to not match*. Evidence is that file's own run, 30 globs / 77 fixtures.

## What was wrong

`pathfixture.py` checked that every pack glob had at least one fixture:

```
ok   every path glob has at least one fixture (no glob is left to rot)
```

True, and worth nothing on its own. **13 of 26 globs had only *positive* rows** — every fixture said
"yes, this fires." Not one of them had ever been shown a path it was supposed to ignore.

A glob that has only ever been observed firing has not been tested. It has been demonstrated. The two are
indistinguishable in a green tally, which is why this sat there while the file reported full coverage.

> A green number that was never asked to be red. — Noah, 6869, describing the same species from another box.

## What it cost

Sweeping in the missing negative half found **five more over-matches**, all live defects, none of which any
existing fixture could have caught:

| glob | path it wrongly matched | why |
|---|---|---|
| `*/.config/openai*` | `./.config/openaiX` | `openai*` matches `openaiX` — glob too loose, `path.Match` correct |
| `*/.bash_profile` | `./.bash_profile.bak` | slash-bearing needle + `Contains`; `.bak` counts as the file |
| `*/authorized_keys` | `./authorized_keys.bak` | same; a backup counts as the authorized file |
| `*id_rsa*` | `./keys/id_rsa_public_readme.md` | needle `id_rsa` + `Contains`; any name containing it matches |
| `*credentials*` | `./credentials_help.html` | needle `credentials`; a help page matches |

Four of the five are `substringNeedle` + `strings.Contains` again — **the same root as both prior findings**,
now confirmed as the single largest source of wrong verdicts in this matcher, in both directions:

- **slash-free needle** → far too loose → matches any file whose name merely contains the word
- **slash-bearing needle** → also too loose → `Contains` ignores the segment boundary the glob was written for
- **`*/`-shaped glob vs bare relative path** → too tight → never fires at all (beat 640)

Three findings, one `substringNeedle`. That is now the whole argument for fixing it, and it is a stronger
argument than any of the three made alone.

## What changed

1. **Added the negative half**: 14 must-not-fire cases, one per one-sided glob, each reasoned *before* running.
2. **Recorded the 5 real over-matches** as declared `XFAIL` with `engine:` reasons — measured behaviour pinned,
   nothing "fixed" by adopting it. `XFAIL` is now 12 entries.
3. **Made it structural** — a new gate check:

   ```
   ok   every path glob is asked to match AND to not match
        (a glob never asked to say no is untested)
   ```

   Add a glob without a negative row and the gate goes red, naming it. Proven: flipping one glob's only
   negative row to positive gives
   `FAIL ... 1 globs have only positive rows: ['*/etc/cron.daily/*']`, rc=1; control rc=0.

   The first attempt at that mutant was a `SyntaxError` — a crash, which is not a red gate and proves nothing.
   Rebuilt so the file stayed valid and only the property broke.

## The generality

This is the same defect as the rest of the week, one level up. Not "a check that passed over nothing," but
**a check that only ever asked half a question and reported the whole answer.** The fix is never a better
number — it is asking the question that can come back no.

## Reproduce

```bash
cd scripts && python3 pathfixture.py | grep -E "asked to match|xfail"
```

`path family has a reader: 30 globs, 77 fixtures, all green`, 12 declared XFAIL.

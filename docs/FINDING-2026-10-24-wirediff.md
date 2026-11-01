# FINDING — 2026-10-24 — wirediff: the 108 is a key list, and the path family now has a reader

**Beat:** 545
**Date:** 2026-10-24
**Status:** Open, priced, no pack change. `path_glob_any` (30 values) now has a fixture reader that
reasons its expectations from `path.Match`; `endpoint_glob_any` (32 values) is routed to the pack
owner as a policy question. `rules/` and `internal/` untouched. No commit. (The engine caveat in this
note was withdrawn by beat 546 — `engine.go` DOES have the suffix loop.)
**Instrument:** `scripts/wirediff.py` (pricing), `scripts/pathfixture.py` (the fixture set), both
verified by running them; readings taken from `rules/carbonato.yaml` as it stands
(sha256 `a2cffbd659905b18…`, 439 lines, 20 rules).

## Why this note exists

fieldcheck (10-23) ended with: 108 matched values in the pack, of which 30 (`path_glob_any`) and
25 (`endpoint_glob_any`) "acquire readers… pure test-and-fixture job". That is a work order, not a
result. This beat did part of it and found out why the counts would not line up.

## 1. The 166 → 125 → 127 → 108 story

`wirediff` first reported **166** matched values (it counted 21 `description` + 20 `technique` prose
lines — a prose field is not a predicate). Then a block-walk bug: a predicate's list has no closing
token, and a value line `        - "x"` is indented the same as `        class:` under
`prior_event:`, so the cron list ran into the next block and read three metadata keys as path globs.
Fixed by closing the predicate on any non-item line. That moved `endpoint_glob_any` 32 → 32-accidental
and `exe_any` 11 → 13.

The remaining gap to fieldcheck's 108 is **not a disagreement about the rules**:

- my walk reads every non-metadata key; fieldcheck's `KEY_RE` reads exactly five
  (path / endpoint / exe / argv / detail), so `binds` / `own` / `session` / `novel` (6 values) are mine;
- `detail_equals` is **one** value, nested (`novel: "true"`), which fieldcheck counts as 1 and a naive
  line-walk counts as more.

So the checker now asserts the delta and **names it** rather than bending the number to 108. It prints
127, the 6 scope values, and the reason.

## 2. The family split, priced

| family | values | decidable from docs? | owner |
|---|---|---|---|
| `argv_any` | 39 | exact / set | this tree |
| `endpoint_glob_any` | **32** | **no — normalisation** | **PACK OWNER (policy)** |
| `path_glob_any` | 30 | yes — `path.Match` | this tree (a fixture) |
| `exe_any` | 13 | exact / set | this tree |
| `detail_non_empty_any` | 6 | exact / set | this tree |
| scope keys (`binds`/`novel`/`own`/`session`) | 6 | exact / set | this tree |
| `detail_non_empty` | 1 | exact / set | this tree |

`endpoint_glob_any` is where the work stops. The engine normalises URLs (scheme? port? subdomain?),
and that normalisation is **undocumented**. A fixture there encodes a *decision*, not a fact, and the
decision is the pack owner's. Writing 32 fixtures would look like diligence and would be a guess with
a green check on it — the exact failure this project exists to catch. Not done. Priced, routed, and
left alone.

## 3. The path family does now have a reader

`scripts/pathfixture.py`: 30 path globs (all 30 have shape `leading *`), 34 fixture paths, every
expected verdict reasoned from Go `path.Match` semantics plus the engine's documented fallback —
not recorded from a run, because a fixture whose expectation came from the matcher tests nothing.

Three checks, all green: (a) the pack's path count is 30; (b) **every** glob has a fixture, so nobody
can add a path glob and leave it unread; (c) no fixture names a glob the pack does not have.

## 4. What the fixture uncovered, and cannot settle here

All 30 path globs begin with `*`, and `*` does not cross `/`. On a plain `path.Match`, `*/etc/cron.d/*`
does **not** match `/etc/cron.d/evil` — there is no segment for the leading star to consume. Three
absolute-path fixtures *do* match in this tree, and only because `packcheck.match_one` (a **Python
port**, not the engine) retries the SUFFIX of the path (`etc/cron.d/evil`), where the star has a
segment.

Consequence, stated plainly: **if the Go engine matches like `path.Match` and has no suffix loop,
every one of the 30 path globs only fires on relative paths** — and `packcheck`'s verdicts are more
permissive than the engine, which is the dangerous direction for a detector (a pack that reads as
covered and isn't).

I cannot verify which. There is no Go toolchain on this host, and whether the suffix fallback should
exist is a rules-engine decision, not mine. Recorded in the script's output on every run and here.

## Method corrections this beat (mine)

1. Counted prose metadata into a predicate denominator (166 vs 108).
2. Block walk with no end marker → swallowed the next block's keys.
3. Reasoned three fixture expectations from "should be false" instead of from `path.Match` + the
   port's actual fallback, and wrote them as `False` — the run disagreed, which is the fixture doing
   its job. Corrected the *reasoning*, not the matcher.
4. Asserted a bashrc glob from the `argv_any` block was a path-family glob; the "no extra glob" check
   went red and named it. Kept it as an explicitly-declared `ARGV_ONLY` exception instead of hiding it.

`rules/` and `internal/` untouched. No commit.

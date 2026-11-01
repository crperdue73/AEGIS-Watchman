# FINDING — 2026-10-25 (beat 569): the denominator was never the pack

**Status:** Open, and it corrects a number this tree has been quoting for two days. No pack change,
no engine change. `rules/` and `internal/` untouched. The instrument is the finding.
**Instrument:** `scripts/blindcheck.py`, `scripts/blindcheck_test.py`, plus a correction inside
`scripts/fieldcheck.py`.

## What was believed

Since `FINDING-2026-10-23-fieldcheck`, this tree has said:

> 108 matched `detail` values in the pack; 39 read by a sample-driven check; 56 read by nothing.

That number is in the index, in a finding doc, and in the WORKLOG. I quoted it myself.

## What is true

**108 is not the size of the pack.** It is the size of the five keys `fieldcheck`'s `KEY_RE` happens
to know:

    path_glob_any · endpoint_glob_any · exe_any · argv_any · detail_equals

The pack's match blocks use four more keys, and `fieldcheck` reads none of them:

| key | values | rules |
|---|---|---|
| `detail_non_empty` | 1 | `carbonato.net.listener_bound` |
| `detail_non_empty_any` | 6 | `container.privileged_start`, `container.after_identity_change` |
| `detail_glob_any` | 3 | `container.host_root_mount` (nested under `binds:`) |
| `prior_event` | 3 conditions, 0 glob values | `exfil.egress_after_identity_change`, `secret.read_after_shell`, `container.after_identity_change` |

**Corrected numbers:**

- full match-block denominator: **118** values, not 108
- read by a sample-driven check: **39** (33%, not 36%)
- read by nothing: **79**, not 56

`prior_event` is a scalar map (`class:`/`within:`), not a glob list. It is counted as 0 values on
purpose — folding it in would inflate a denominator with something no glob check could ever read.
It is reported separately as 3 conditions.

## The part that matters more than the arithmetic

**A denominator that silently drops rows makes a blind column look smaller than it is.** The
instrument built to expose unread coverage had an unread row of its own: four keys it could not
see, and no line anywhere saying so. The 56 was not wrong about the rows it counted; it was
presented as a total when it was a subtotal.

`fieldcheck` no longer prints a bare denominator. It now prints `⚠ THIS DENOMINATOR IS PARTIAL`,
names the four unseen keys, gives 118, and says which pair to quote.

## A second, smaller disagreement found in the same file

`fieldcheck` computed its numerator as `key == "argv_any"` — a literal — while the table one screen
above said `exe_any` was a candidate source only with no sample targeting it. So the file printed
"39 read / 56 unread" and the unread list on the next line did not match either: summing the table
gave 69. Two numbers, one report, neither visibly wrong alone.

Fixed: the numerator now derives from the same `CHECKED_BY` column the table prints
(`SAMPLE_DRIVEN(key)`), so the sentences cannot disagree. It prints 69 on the five visible keys and
79 on the full block, and says both.

## What this does NOT claim

- Not that any unread value is wrong. Silence about a value is not a verdict on it.
- Not that the pack is worse than yesterday. The pack did not change. **My measurement of it did.**
- Not that 79 is the end. There may be keys in the match blocks neither file parses. The number is
  a floor on blindness, not a count of it.
- The YAML is parsed by indentation-shaped regex, not a YAML library (none available, and I will
  not assume one). Every count is printed so a wrong one is visible rather than trusted.

## Proof the guard fails correctly

`blindcheck_test.py`: 23 checks, 0 fail. Mutation-tested — three mutants on throwaway copies:

| mutant | result |
|---|---|
| numerator widened to include `exe_any` (the old literal, restored) | **3 FAIL** — candidate-only key becomes sample-driven, 69 and 79 both wrong |
| `_unseen_total` forced to 0 ("pretend the pack is fully visible") | **2 FAIL** — fieldcheck stops naming 118, the two files disagree |
| inline-list branch in `_unseen_total` disabled | **NOT caught** — measured, see below |

**The third is a hole and I am not hiding it.** No current pack value is an inline list, so
disabling that branch changes nothing on today's pack. The test pins today's counts; it does not
prove the inline branch works. A pack edit that introduces an inline list would be counted 0 and
nobody would notice. Closing it needs a fixture pack, not a mutation of this one.

## Note for the pack owner (not a patch)

`carbonato.net.listener_bound` has `detail_non_empty: ["listen"]` — it asserts "the `listen` key is
non-empty," which no rule author can fail by writing the key at all. Whether that predicate was
meant to carry a value is a one-line yaml question, and it is the pack owner's to answer.

## Files

- `scripts/blindcheck.py` — new report; both denominators, side by side
- `scripts/blindcheck_test.py` — new; 23 checks, mutation-tested
- `scripts/fieldcheck.py` — denominator self-labelled PARTIAL; numerator derived from its own
  column instead of a literal

# FINDING — 2026-10-24 — the index I built this morning was quietly truncating its own statuses

**Beat:** 547
**Date:** 2026-10-24
**Status:** fixed in the instrument (`scripts/docsindex.py`), index regenerated; archive green at 35
findings / 81 scripts. No pack change, `rules/` and `internal/` untouched, no commit.

## What was wrong

`scripts/docsindex.py` (beat 542) read each finding's status with:

```python
re.search(r"\*\*Status:\*\*\s*([^*\n]+)", text)
```

`[^*]` stops at the next asterisk. A status is prose and prose wears italics — so **14 of 36 rows
were cut at the first `*`**:

| in the document | in the index |
|---|---|
| `OPEN — analyse-only. Not fixed (see *Why this is not patched here*).` | `OPEN — analyse-only. Not fixed (see` |
| `Open, pack owner's call. 56 of 108 matched values have no local guard;` | `Open, pack owner's call. 56 of 108 matched values have no local guard;` |
| *(no `**Status:**` field at all)* | `Beat:` |

The last row is the worst of it: two documents I wrote **this morning** (545, 546) put `**Beat:**`
where the others put `**Status:**`, and the fallback regex — "the first bolded capitalised sentence
in the opening 14 lines" — happily matched the *label* `**Beat:**`, so the table showed the word
"Beat" in the status column and nobody could tell it was empty.

That is the same failure this index exists to prevent, committed by the index: a reader could not
tell an open finding from a closed one, and `docsindex.py` reported "archive consistent" the whole
time.

## Why the existing checks were blind to it

Every check asked whether a status *exists*, not whether it is a **whole** field. `\S` was found →
green. The truncation was invisible by construction.

## The fix, and the three false starts

1. `[^*\n]+` → `[^|\n]+`, plus `status_field()` accepting `**Status:**` or `**Disposition:**`.
2. **Fragment detector:** red on a status ending on a dangling connector (`see`, `(see`, `because`,
   `below`, `noted`) or on a bare colon.
3. **Cell counter:** every data row must have exactly 5 markdown cells.

Two of my own checks went red on legitimate prose before they were right:
- the first fragment rule flagged any status *containing* a colon — including `12:18 on 2026-10-23`,
  a timestamp, which is content. A fragment ends ON a colon; a sentence merely contains one.
- the first version then flagged the two documents I had written this morning, whose colon-ending
  statuses were genuinely missing a field. **That was the check working** — I added real `**Status:**`
  lines to 545 and 546 rather than relaxing the rule.

## Falsifier (run, not assumed)

Injected an extra cell into the DISPOSITIONS row of the generated index:

```
FAIL every index row has exactly 5 cells  [1 ragged: ['[FINDING-2026-10-23-DISPOSITIONS](...)']]
```

Index restored, check green again. A check that has never been seen to go red is a decoration.

## Denominator

35 finding documents, 81 scripts/probes on disk, 35/35 status fields read, longest field 199 chars.
Before the fix: 14 truncated, 2 of them carrying a label instead of a disposition.

## Standing correction

For three beats this morning I reported "archive consistent" from this tool's exit status. The exit
status was honest about the checks it ran; the checks were not asking the right question. The number
was real, the assurance was not.

**The rule this earns:** a generated table needs a structural check (cell count, field width), not
just a content check. Prose read by someone who trusts the tool looks exactly like prose read by
someone who shouldn't.

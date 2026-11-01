# FINDING 2026-10-25l — the census was never a census

**Status:** OPEN — a defect of the instrument, not the pack. Nothing in `rules/carbonato.yaml` changed.
**Instrument:** `scripts/fieldcheck.py` (`KEY_RE`, `keys_by_rule`), `scripts/blindcheck.py`,
`scripts/eventcheck.py`. Measurements below are direct and reproducible; the Go engine is read, not run.

## The thing I got wrong in 25k, corrected within the hour

In `25k` I fixed a phantom value and published **117 / 39 / 78** as the corrected denominator. That fix
was right about the phantom and **wrong about the census**. I treated three calls to `keys_by_rule` as
the whole surface. There are three, but the parser behind them is the problem, not the call sites.

## `KEY_RE` is an allowlist, and it was reported as a total

```python
KEY_RE = re.compile(r"^\s*(path_glob_any|endpoint_glob_any|exe_any|argv_any|detail_equals):\s*(.*)$")
```

Five keys, hard-coded. The pack's match blocks contain **twelve**. These have never parsed, in any run
of any tool in this project:

| invisible key | blocks |
|---|---|
| `detail_glob_any` | 1 |
| `detail_non_empty` | 1 |
| `detail_non_empty_any` | 2 |
| `prior_event` | 3 |
| `own` | 1 |
| `session` | 1 |
| `type` | 20 |

**29 match blocks were never counted.** Everything downstream — "118 values", "79 unread", "unseen keys
are 5 (`detail_non_empty`, `detail_non_empty_any`, `detail_glob_any`, `prior_event`)", the whole unseen
breakdown — was computed from a view that could not see them.

That the unseen-key list happened to name four of the invisible keys is the coincidence that hid this:
the tool *reported* them as unseen while its parser could not *reach* them. Two mechanisms produced
lookalike output. I read agreement and saw a census.

## Three counts, none equal, and why

| method | result | what it actually counts |
|---|---|---|
| `KEY_RE` allowlist (`fieldcheck`/`blindcheck`) | 117 | inline-list items under 5 named keys |
| quoted-string census | 113 | `"..."` literals anywhere in a match block |
| structural walk of every key | 152 | every scalar, list item, and map sub-field |

None is wrong. **The error was calling any of them "the denominator."** A match block is
heterogeneous:

- `path_glob_any` — a list of globs.
- `prior_event` — a *map* of named conditions (`class: process.exec`, `within: 2m`). Two sub-fields, not
  two globs; counting them as "values" is a category error, and not counting them hides a real predicate.
- `type: file.access` — a gate, not a match value at all.
- `detail_non_empty` — a key-presence test; its "value" is the key.

A single integer cannot mean all of those. The habit of tracking one number ("118") made the divergence
invisible for two days.

## What this means for the findings that quoted it

- The **defects** in `25e`–`25j` are unaffected. Every one was measured on *glob values directly*
  (e.g. `match_one('*id_rsa*', '/srv/backup/id_rsa.tar.gz')`), never on the total. The unanchored-glob
  inventory, the three mechanisms, the bind-spec suffix behaviour — all still stand.
- The **coverage claims do not stand as stated.** "39 of 117 read (33%)" cannot be restored to a total
  when the total was never measured. A rule using `prior_event` may be tested by `eventcheck` and still
  be reported unread, because `keys_by_rule` never handed its block to anything.
- Anyone who quoted "66% unread" to a stakeholder quoted a fraction of a subset as if it were a
  fraction of the whole.

## The general property (this is the finding)

**An allowlist that reports itself as a census is the most expensive kind of blind spot**, because its
output is a plausible number and the keys it drops are exactly the keys nobody wrote a test for. `25k`
took me to the phantom; the phantom was a symptom of this.

Two guards that would have caught it, neither present:

1. Every key seen at match-block depth must be either parsed or in an explicit `UNPARSED_KEYS` set that
   is **reported by name and count**. Silence is what happened.
2. The total must be printed **with its scope**: `117 inline-list items under 5 keys`, not `117 values`.

## Not fixed this beat

Rewriting `keys_by_rule` to be structural is a real change to a file that three tools depend on, at
11:31 PM, and `fieldcheck`'s `CHECKED_BY` mapping is built around the five-key world. Doing it now would
mean shipping an unverified parser into the one instrument everything else trusts. The next step is
scoped and written down, not half-done.

**Beat 583 next:** add `UNPARSED_KEYS` reporting to `fieldcheck` first — a change that makes the blind
spot visible without touching parse semantics — with a test that asserts the reported set matches the
pack. Then, with a person awake, replace `keys_by_rule` structurally.

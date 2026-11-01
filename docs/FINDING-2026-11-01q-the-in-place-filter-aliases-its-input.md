# FINDING — 2026-11-01q: the in-place filter aliases the slice it is reading

**Status:** source reads. **NOT CLAIMED as an observed overwrite** — see the reasoning ladder below, which is the
point of the note. **NOT APPLIED.** **Instrument:** `scripts/inplacefiltercensus.py`.

**One line:** `pruneLocked` filters with `keep := m.recs[:0]` while ranging over `m.recs`, so `keep` and `m.recs`
share one backing array — the filter writes each survivor over the slots it is still reading from.

## The code

> ⚠️ **CORRECTION, same beat.** I first wrote that `FileRecordStore` (:287-299) is "identical". It is **not** —
> the code is line-for-line the same, but **`FileRecordStore.pruneLocked` has no comment at all**: no "Find the first
> index", no clock caveat, nothing. The asymmetry is *who explains themselves*, not what they execute. Every comment
> claim in this doc (C, D) applies to the Memory copy only, and the instrument now says so explicitly rather than
> quietly passing a missing file as a match.

```go
// eventstore.go:186-201 (MemoryRecordStore)
// FileRecordStore:287-299 runs the same filter with no comment whatsoever
func (m *MemoryRecordStore) pruneLocked(now time.Time) {
	cutoff := now.Add(-m.retain.Window)
	keep := m.recs[:0]
	for _, r := range m.recs {          // ranges over the FULL original length
		if r.When.After(cutoff) || r.When.Equal(cutoff) {
			keep = append(keep, r)      // writes into m.recs[n] where n <= current index
		}
	}
	m.recs = keep
	if len(m.recs) > m.retain.MaxRecords {
		m.recs = m.recs[len(m.recs)-m.retain.MaxRecords:]
	}
}
```

`keep` is `m.recs[:0]` — **zero length, same backing array, same capacity.** Every `append` writes a surviving record
into a slot of the array the `range` is still iterating.

## Why this is NOT a bug, worked out rather than asserted

The safe idiom is well known and this looks like the dangerous version of it. It isn't, and I want the reasoning on
the record so the next reader doesn't have to redo it:

- `range` copies each element out **before** the loop body runs. Element `i` is read into `r` first, and only then
  does `append` possibly write into index `j`.
- Because `keep` is only ever appended when a record survives, `len(keep) <= i` at all times. So the write index `j`
  is always **at or before** the read index. The write lands on a slot the range has already copied past.
- Overwriting an already-consumed slot cannot change what a later iteration reads. Safe.

**The one condition that makes it safe is `len(keep) <= i`** — i.e. the filter can only shrink, never grow. That holds
here. It is the property to check when reviewing any `xs[:0]` filter, and it is not written down anywhere in this
file.

## The consequence that IS worth noting

Because the filter writes in place and then takes `m.recs = keep`, the **stale values past `len(keep)` are still in
the backing array.** They are unreachable through `m.recs` (its length excludes them), so this is a memory-hygiene
detail rather than a correctness one — but it means a dropped `Record` keeps its strings (path, summary, host) alive
in the array until the array is reallocated by a later append. Those strings are the ones most likely to be from the
alarming events retention is trying to forget.

Same shape as beat 730: **the record is gone from every view, and still resident.** Not a leak in the GC sense — the
array bounds collection, not the slice — but the retention promise ("dropped on write") is about visibility, not
residency, and the two differ by up to `cap(m.recs)` records.

## The comment above it is right about order and wrong about what it's guarding

*(Memory copy only — the File copy has no comment. See the correction at the top.)*

```go
// Find the first index still in window (records are roughly sorted by
// append time; a mis-ordered clock only costs a stale record surviving,
// never data loss).
```

Two problems, both beat-735 territory and both cheap to state:

1. **"Find the first index"** describes a search for a boundary index. The code does a **full linear filter** — no
   index is found, no early exit, and the position of the first out-of-window record is never used. The comment
   describes an optimisation the code does not perform.
2. The clock caveat is attached to the **window** pass, where order genuinely cannot hurt, and not to the **cap** pass
   below it, which is positional — beat 735's finding. This comment is in the wrong place for the second time in as
   many beats.

And a third, which is the correction above: **only one of the two copies has this comment.** So the positional cap's
ordering assumption — beat 735's whole subject — is documented in exactly one of the two places it holds. A reviewer
reading `FileRecordStore.pruneLocked` sees an unexplained in-place filter doing a positional trim and has nothing to
tell them either half of what beat 735 and beat 736 each found.

## Cost, measured statically

`pruneLocked` runs on **every `Append`** (`:149`, `:253`) under the store's mutex: one full O(n) scan of the whole
ring plus a possible re-slice, per record written. At the 10 000-record cap that is 10 000 comparisons per ingest
event. SQLite does the equivalent as two indexed statements and does not scan in Go at all. **This is a performance
observation, not a recommendation** — beat 724 stands: performance trade-offs are Dad's call, and I am not filing a
fix.

## Options — listed, not picked

1. **Leave the filter, fix the comment** — say "compact in place", drop the "find the first index" sentence, and write
   down the `len(keep) <= i` invariant so the next reviewer doesn't have to prove it again. Zero risk.
2. **Nil the tail** — `for i := len(keep); i < len(orig); i++ { orig[i] = Record{} }` before re-slicing, so dropped
   records don't outlive their deletion in memory. Costs a small loop on the prune path.
3. **Boundary search** — since append order is ≈ time order (beat 735), find the first in-window index by binary
   search and slice, which is what the comment already claims. Only sound because of the ordering assumption beat 735
   found unverified.
4. **Leave all of it** — defensible; the code is correct today. But then the comment is still describing a different
   function.

## What I am NOT claiming

- **Not** that the two backends are identical. They execute identically and document differently, which I got wrong on
  the first pass and corrected above.

- **Not** that data is corrupted, dropped wrongly, or that this is undefined behaviour.
- **Not observed** — no instrument can watch a `range` on this host (no toolchain, beat 642). The safety argument
  above is a reading of the language spec, and it is why I am **not** calling this a bug.
- **Not** that the strings are leaked — the backing array bounds them.
- **Not** that the per-append full scan is a problem in practice. It is a measurement of shape, not of impact.

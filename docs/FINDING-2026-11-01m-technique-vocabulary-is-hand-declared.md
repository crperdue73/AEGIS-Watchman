# FINDING — 2026-11-01m: the technique vocabulary is hand-declared, unvalidated, and "ATT&CK-style"

**Status:** source reads. **NOT APPLIED.** **Instrument:** `scripts/provenancecensus.py`.
**One line:** rule provenance survives the trip correctly; what is thin is the *meaning* of the technique label — a
hand-coined, unvalidated vocabulary that the schema calls "ATT&CK-style."

**And, said up front: this is smaller than** the seven findings before it. Those each found the tool asserting
something untrue. This one finds the mechanism sound and the label overpromising. I am not inflating it.

## The good part, measured before the criticism

Provenance is stamped **at match time**, from the rule object that fired:

```go
// internal/rules/engine.go:73
ev.Rule = &schema.Rule{ID: r.ID, Pack: r.Pack, Version: r.Version, Technique: r.Technique}
```

`RecordFromEvent` copies all four onto the record verbatim (`eventstore.go:351-355`), and SQLite persists
technique/pack/version as columns. So **editing the rule pack later does not retroactively relabel old records** —
the store keeps what fired, not what the pack says now. That is the correct design, and it is the answer to beat 731's
question: this particular measurement is *not* re-derived.

## What is actually thin — three narrow things

**1. The vocabulary is 11 strings, hand-coined, unversioned.** Measured from `rules/carbonato.yaml`: 20 rules each
declare a `technique:`; 20 values; **11 distinct**: `phase3/command-and-control`, `container-escape`, `persistence`,
`secret-access`, `execution`, `exfiltration`, `defense-evasion`, `scope-expansion`, plus three `correlated-*`
variants. These are useful grouping labels. They are **not ATT&CK technique ids**, and no ATT&CK id appears anywhere
in the pack.

**2. The schema comment invites the wrong reading.** `schema.go:76` — *"Technique is the rule's ATT&CK-style tag"*.
A consumer reading that comment and grouping by technique is entitled to think they have ATT&CK coverage. They have
eleven slugs someone wrote by hand. Nothing validates the vocabulary: a typo in a new rule creates a twelfth category
and the fleet rollup (`fleet.go:115` `h.ByTechnique[rec.Technique]++`) silently grows a column. No error, no warning,
a dashboard that looks fuller than it was yesterday.

**3. One field, two sources, no marker.** `eventstore.go:367` falls back to `ev.Action.Detail["technique"]` when the
rule didn't declare one — a documented legacy path, recorded verbatim. The record then cannot say whether the rule
declared the technique or a producer typed it in an action detail. Both are defensible alone; together, the field
means "one of two things" and the store doesn't record which.

**Pack identity IS stamped** — and I got this wrong first time. My draft said "nothing assigns `Pack`/`Version`," and
the instrument contradicted me: `rule.go:144-149` sets `r.Pack = p.Name` and `r.Version = p.Version` in the loader
when the rule doesn't declare its own. So a record does carry pack-build identity. That is the second item on my
"thin" list that turned out solid on inspection, and it is the reason to check before writing the sentence, not after.

## Why I am calling this the small one

The previous seven beats produced a specific, operator-visible wrong statement: an alarm on an unread file, a
freshness column that measures alarms, an identity gap. This one produces a *misleading adjective* in a comment and a
missing validator. The failure mode is a human or a downstream tool **over-trusting the word "ATT&CK"** — real, and
worth fixing, but it needs a reader to be fooled rather than a record to be false.

## Scope / what I am NOT claiming

- **Not observed** on a running system (no toolchain, beat 642). Source reads.
- **Not** claiming the techniques are wrong to use. As a grouping vocabulary they do their job.
- **Not** claiming anyone has been misled, or that any dashboard reports ATT&CK coverage.
- **Not** claiming `Pack`/`Version` emptiness is a bug — it is a gap I measured, and whether the pack should stamp
  identity is a packaging decision.

## Options — listed, not picked

1. **Rename the doc comment** to "grouping slug chosen by the pack author" — one line, removes the overpromise,
   changes nothing else. Cheapest honest fix.
2. **Validate the vocabulary at load** — warn on a technique value seen in no other rule, or against an allowed list.
   Turns the silent twelfth category into a startup line.
3. **Stamp `Pack`/`Version` at load** from the pack's own metadata so records carry pack-build identity.
4. **Add `TechniqueSource`** so the record says which branch filled it. Only worth it if the legacy path is live;
   otherwise it is a field protecting against a producer that no longer exists.
5. Leave all of it. **Defensible** — none of this makes a single event wrong. I would still do (1), because a comment
   is a promise and this one is slightly larger than the field it describes.

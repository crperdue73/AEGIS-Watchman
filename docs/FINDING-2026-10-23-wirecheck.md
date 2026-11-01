# FINDING — 2026-10-23 — no script was checking wires; I built the one that does, and it corrects one of my own findings

**Status:** new guard, green. `rules/` and `internal/` untouched. No commit.
**Instrument:** `scripts/wirecheck.py` · **Test:** `scripts/wirecheck_test.py` (31 checks, green)
**Beat:** 534b, written 21:55 ET — drift.md holds 534; this is the same hour's second note, not a
re-used number. Renumbered 2026-10-24 by `scripts/docsindex.py`, which counts beat claims per file.

## The gap

At 12:18 today a *class* check took over the filename `scripts/typecheck.py` and overwrote the *wire* check
that lived there. The orphaned test was parked in `scripts/attic/` with a note saying so — and then nothing
happened. By tonight, no script in the tree was checking wires:

- **`typecheck.py`** — declared action **type** vs. a producer existing. Not a wire check.
- **`fieldcheck.py`** — key **spelling** only. It asks "does this rule's key exist among this type's emitted
  keys". It never asks *which key the engine reads* for each predicate.
- **`blindcheck.py`** — behavioural fixtures. It can only catch a predicate family that some fixture happens
  to exercise.

That left a specific hole: a rule using `path_glob_any` on a type whose producer never sets `path` is invisible
to every guard, because the engine reads `path` hard-coded — the rule doesn't name it, so spelling checks
can't see it and fixtures don't cover it. This is precisely the `persist.file_write` rename defect, found by
hand twice this week and pinned by nothing.

## What it does

Reads both sides **out of the source**, not from a remembered table:

- **Demand** — `internal/rules/engine.go`: for each predicate block, the first `detailString(action, "k")`
  inside it. Five detail-reading predicates: `ExeAny→exe`, `ArgvAny→argv`, `PathGlobAny→path`,
  `EndpointGlobAny→endpoint`, `DetailEquals/DetailGlobAny`→key from the rule.
- **Supply** — every `internal/telemetry/*.go` constructor (`...Action(...)`), brace-matched, with
  `detail["k"] =` assignments and map-literal members attributed to a type.

Verdicts: `WIRED` · `MISSING` (dead wire — the predicate can never pass) · `CONDITIONAL` (emitted only on some
path — legal, but the rule must tolerate absence) · `NO-PRODUCER` (no constructor in this tree; the R2.2 eBPF
stub, not judgeable).

Exit **0** = no MISSING and denominator non-zero · **1** = MISSING · **2** = cannot judge.

## Result on the current tree

**21 rule/predicate/key wires, all 21 judgeable. 4 unconditional, 17 conditional, 0 MISSING.**

The 17 conditional ones are not noise — they are the real shape of the producer:

| class | example | why conditional |
|---|---|---|
| `process.exec` / `argv` | 9 rules | set only `if len(r.Argv) > 0` |
| `net.egress` / `endpoint` | 4 rules | set only when the endpoint resolves |
| `container.start` / `privileged`,`host_pid`,`host_network`,`binds` | 4 rules | set only `if cfg != nil` |

**That last row is the parent of 533's position defect.** `carbonato.container.privileged_start` reads three
detail fields that exist only when the daemon handed us a `ContainerConfig`. The producer's own comment says
nil `cfg` still fires identity-only — "report what you saw, flag what you could not check". So the pack's
container rules are already gated on a `cfg` that may be absent, *before* you get to the bind-ordering bug.
Same family, one layer down.

## The correction — one of my own findings was wrong

I came into this heartbeat believing `persist.file_write` has a dead wire on `path`, because the rename path
doesn't set it. **The producer sets `path` in the map literal unconditionally:**

```go
detail := map[string]any{
    "path": p,      // ← always present
    "syscall": ...,
}
```

What's conditional is `sycall`/`nametype`/`watch`, not `path`. So the wire is **live**, and my earlier
"missing field" claim was reading a hand-derived list rather than the constructor. The real rename defect
(527/526) is about *which value* `path` carries on a rename, not whether the key exists. That's a narrower and
more accurate statement, and it's now what the record says.

**Removing a false accusation from my own list is a correction, and it goes in the record as one.**

## Three instrument bugs caught before publication

This probe failed three times in a row, each time in a way that would have published a false claim:

1. **Zero constructors found** — my function regex required `func WordAction(` and missed the receiver form
   `func (r *AuditRecord) WriteAction(`. Reported "21 missing wires" on a working tree.
2. **`ExeAny → argv`** — the engine hoists `exe := detailString(action, "exe")` *above* every predicate block,
   so a window-scan attributes the next block's key to it. Fixed by reading the hoist directly.
3. **`path` invisible on `file.write`** — map-literal members are written `"path": v,`, which never matches
   `detail["path"] =`. This is the same *class* as the YAML quote bug: **a value can be spelled two legal ways
   and the reader only knew one.** Third occurrence of that shape today (predaudit, bindprobe, this).

Plus the list-marker-then-quote bug a fourth time in the pack reader, and a missed nested-map form
(`detail_equals:` → `novel: "true"`). All five are pinned as assertions in `wirecheck_test.py` now, not
comments.

## Reproduce

```bash
python3 scripts/wirecheck.py        # rc 0 = no dead wire, denominator 21
python3 scripts/wirecheck_test.py   # 31 checks incl. synthesised dead-wire + empty-denominator-must-exit-2
```

## For the pack owner

Nothing here needs a pack change. Two things worth carrying to the list:

1. **`detail_non_empty_any` on `privileged`/`host_pid`/`host_network`** is only reachable when `cfg != nil`.
   That's a producer gate, not a rule bug — but it stacks with 533's ordering gate, and both sit on the
   CARBONATO `:2375` vector, which is the documented entry point.
2. **The three `secret.*` rules remain unjudgeable** — `file.access` has no constructor in this tree. Same R2.2
   item that's already at the top of the handover; this just names the wire-level consequence.

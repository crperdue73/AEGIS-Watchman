# FINDING 2026-10-26d — `InertRules` guards the envelope, not the contents

**Follow-on to:** `FINDING-2026-10-26c-endpoint-globs-have-no-subject.md` (beat 599)
**Status:** OPEN — design note, no code changed.
**Found:** beat 600

## What the codebase already gets right

`internal/rules/coverage.go` has `InertRules(packs, available)`, and
`internal/collector/behavior.go:253-266` calls it at startup, logging the count and naming the dead rules:

> `N of M loaded rule(s) cannot fire with the attached sources — no source emits their action class: ...`

This is exactly the right instinct, already implemented, already wired to the startup path, with a comment
that names the failure mode in the project's own language — *"look protected, detect nothing"*. I want to be
clear that this is good, because what follows is not a criticism of the idea. It is a measurement of its
**reach**.

## Where it stops

`InertRules` compares **`r.Match.Type`** against the set of live action classes:

```go
if t := r.Match.Type; t != "" && !available[t] {
    out = append(out, r)
}
```

One condition, on one field. So its verdict is: *"can this rule fire at all?"* — which is the right question,
and not the whole question.

On the reference deployment (auditd + eBPF + docker attached) all seven action classes are available, so the
log line reads:

> **all 20 loaded rule(s) can fire with the attached sources**

That sentence is true. It is also the sentence an operator will read as *"20 rules of coverage."* And it
sits directly on top of finding 26c: **30 of the 32 endpoint globs inside those 20 rules have no path to a
hostname.** `c2.blockchain_rpc` is not reported inert — its `type: net.egress` is available, so the rule
passes the check and will fire in principle — but eleven of its eleven globs cannot match what the egress
adapter emits.

## The distinction worth naming

There are two different ways a rule can be dead, and the code only asks about one:

| | question | answered by |
|---|---|---|
| **Envelope** | can an action of this class reach the engine at all? | `InertRules` — yes, correctly |
| **Contents** | given an action of that class, can any of the rule's match values ever accept it? | *nothing asks this* |

The envelope check is cheap, mechanical and correct. The contents check needs to know the **shape** of the
detail a source emits — finding 26c is precisely that knowledge, obtained by hand.

## What makes the contents question hard (and why it is still worth asking)

A match value is inert when no action of that type can carry a detail that satisfies it. That is not decidable
by looking at the rule. It requires a claim about each source's **output shape**:

- `net.egress` emits `"<service> <ip:port>"` or `"<ip:port>"` — so any glob requiring a hostname not in
  `knownServices` is inert.
- `net.listen` emits `"<ip:port>"` — so any `endpoint_glob_any` on a listen rule requiring a name is inert.
- `container.start` emits `binds` as a comma-joined string — so `*:/host*` is over-broad but *live*.
- `file.access` / `file.write` emit paths — so `path_glob_any` values are live if the path shape overlaps.

Each is a fact about one file in `internal/telemetry/`, and each is exactly the kind of fact that rots
silently when the collector changes.

## Options (none applied)

- **Do nothing, and let finding 26c stand as prose.** Cheapest; also the state the project says it is
  fighting.
- **Per-source output-shape declarations**, written next to each adapter and checked by `InertRules` (or a
  sibling `InertValues`). Larger change, but it makes the answer mechanical instead of an audit.
- **Fixture-driven**: a table of realistic emitted actions per type, run through each rule's matcher at load,
  reporting rules whose every value fails on every fixture. This is the version I would argue for — it needs
  no new declarations, it reuses the real matcher, and a stale fixture shows up as a failing test rather than
  a stale comment.

The third option is the one that fits how this project already verifies things.

## Ledger impact

None. This is a finding about the **instrument that reports coverage**, not about a match key. It does mean
the startup line "all 20 loaded rule(s) can fire" should not be quoted as a coverage number without 26c
attached to it.

## Beat 606 — the blind spot is now executable, and a correction to 26f's arithmetic

`TestInertRulesCannotSeeInsideAnAvailableClass` (`internal/rules/coverage_test.go`) now states the limit in
code: two rules whose class is available but whose match values can never match what the emitter produces.
`InertRules` calls both **live**. That is the contract — content-blind by construction — and if anyone
extends it to inspect match values, the test fails and this doc must move.

**Correction to the 26f numbers.** While writing the guard I re-measured and got **6 inert / 14 live**, not
26f's **7 / 13**. Traced rather than edited:

`stockAvailable()`, the existing helper in `coverage_test.go`, includes **`scope.change`**.
`behavior.go:245` sets that class only when `scopeAttached`. So the helper models *stock build with the scope
adapter* — **not** auditd-only. The rule that separates the two hosts is **`carbonato.tool.scope_new`**.

- 26f's **7 / 13 is correct** for a true auditd-only host. It stands.
- The helper's **6 / 14** was correct for what it models; its *name* overclaimed.

Fixed so the ambiguity cannot be inherited: added `auditdOnlyAvailable()` mirroring `behavior.go:245`, made
`stockAvailable()`'s doc comment say the scope adapter is included, and pinned both counts and the one-rule
delta in `TestStockAndAuditdOnlyAreDifferentHosts`. A Python guard asserts the helper's class set equals the
one behavior.go actually builds.

**Why this matters beyond bookkeeping:** 26f is the finding that says five of six critical rules go dark on a
degraded host. If its denominators are soft, the finding is soft. They are now pinned from both directions —
Go test and Python guard — against the source that produces them, not against my notes.

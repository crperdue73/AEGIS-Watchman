# FINDING — 2026-10-31: the flagship rule is named for an ordering it does not perform

**Status:** measured, **not fixed** — a `severity: critical` rule change with a false-negative trade-off. Pack owner's call.
**Severity:** high. This is the canonical CARBONATO rule; the product's thesis is stated in its own name.
**Instrument:** `scripts/packcheck.py` + direct parse of `rules/carbonato.yaml`; engine semantics read from
`internal/rules/engine.go:84-92` (`temporalMatches`).
**Written up:** beat 715.

## The rule

```yaml
- id: carbonato.secret.read_order_follows_constraint     # <- the name is the thesis
  title: Agent reads a credential store after a constraint-ignoring prompt
  severity: critical
  description: >
    The canonical CARBONATO ordering: a prompt tells the agent to ignore prior
    constraints and read credentials. A file.access into ~/.ssh, ~/.aws, or a
    keychain after such a prompt is the exfil precursor.
  match:
    type: file.access
    path_glob_any:
      - "*/.ssh/*"   -*- 8 paths -*-
```

**There is no ordering in it.** No `prior_event`, no temporal predicate, nothing that references a preceding
prompt. The match block is a path list and nothing else.

## The engine *can* do ordering — this is not a missing capability

`internal/rules/engine.go:84-92`:

```go
func (e *Engine) temporalMatches(m Match, host schema.Host, at time.Time) bool {
	if m.PriorEvent == nil { return true }
	if e.timeline == nil || at.IsZero() { return false }
	return e.timeline.Seen(host.ID, m.PriorEvent.Class, m.PriorEvent.Within, at)
}
```

And three sibling rules use it correctly — measured over the pack's 20 rules:

| rule | uses `prior_event` |
|---|---|
| `carbonato.secret.read_after_shell` | **yes** (`class: process.exec, within: 2m`) |
| `carbonato.exfil.egress_after_identity_change` | **yes** |
| `carbonato.container.after_identity_change` | **yes** |
| `carbonato.secret.read_order_follows_constraint` | **NO** |

For comparison, the sibling that does it right:

```yaml
- id: carbonato.secret.read_after_shell
  title: Credential read within 2m of a shell spawn
  match:
    type: file.access
    path_glob_any: [ ... same 5 paths ... ]
    prior_event:
      class: process.exec
      within: 2m
```

**Same predicate, same paths, one has the temporal half and the critical-order rule does not.**

## Consequence

`secret.read_order_follows_constraint` fires on **any** read of a credential path, by any agent, at any time,
regardless of what happened before it. It is a duplicate of the plain path match, wearing a name that promises
correlation.

The practical damage is not a missed attack — it is **alert semantics**: the pack contains two rules on overlapping
paths, one of which reports itself as "the canonical CARBONATO ordering". An operator triaging
`read_order_follows_constraint: critical` will believe the ordering was observed. It wasn't. That is a *false
positive with a confident story attached*, which is worse for trust than a rule that admits it fires on paths.

## Why I did not fix it

Adding `prior_event` **narrows** a critical rule, and the choice of class is a real judgment:

- `prior_event: {class: process.exec, within: 2m}` would duplicate `read_after_shell` almost exactly and
  **silently retire** the generic path alert — a genuine CRITICAL false negative if the truly-canonical trigger
  isn't a shell spawn but e.g. a `file.read` of a prompt file or an LLM call.
- The pack has no `prompt` action class in evidence here, so "after a constraint-ignoring prompt" may not be
  expressible with a class the timeline can see. That would mean the rule's *name* describes something the action
  schema cannot currently observe — a deeper gap than a missing predicate.

**CONFIRMED (beat 716) — the second possibility is the true one.** `internal/schema/schema.go:67` enumerates the
closed set of action types:

```
// process.exec | net.egress | net.listen | file.access | file.write | scope.change | container.start
```

**Seven types. There is no prompt type.** `Action.Type` is a `string` with no validation, but the collector only
emits the seven (`internal/collector/behavior.go:249` marks availability per type), and `prior_event.class` matches
against classes the timeline has *seen*. **So "a read after a constraint-ignoring prompt" is not observable with the
current action schema at all** — not merely unexpressed in this rule.

That settles the options below:
- Option 2 ("add `prior_event` once someone defines the prompt-observed class") is **blocked on a schema change**, not
  on a pack edit. It is not a one-line fix and I should not have listed it as one.
- **Option 1 (re-describe to what it does) is the only change available today** — and now it is clearly the
  correct one rather than the cheap one.
- The honest product story: the pack can detect *credential reads* and *reads correlated with shell spawns*, but the
  canonical CARBONATO ordering (prompt-injection ⇒ credential read) is **not detectable by this product yet**. That
  belongs in the docs, not just in a worklog.

## The customer-facing doc repeats the same false claim (measured beat 716)

`docs/rules.md:156` is the table a user reads to decide what the product does:

```
| `carbonato.secret.read_order_follows_constraint` † | critical | phase3/secret-access | file.access |
  credential read after a constraint-ignoring prompt |
```

**"Catches: credential read after a constraint-ignoring prompt" is not true, and cannot be true** with the current
action schema. The † does not cover this — `docs/rules.md:176-190` defines † as *"needs the eBPF source
(`file.access`) — not built (P2.0)"*, i.e. a caveat about whether the rule can *fire at all*, not about what it
matches when it does.

The rest of that document is careful and correct — I verified its two headline numbers against the pack rather than
trusting them:

| claim in `docs/rules.md` | measured | verdict |
|---|---|---|
| 6 critical rules | 6 | **correct** |
| 14 of 20 rules can fire on a stock build | 14 live / 6 inert (`file.access` + `container.start`) | **correct** |
| 1 of the 6 criticals can fire | 5 inert → 1 live (`exfil.egress_after_identity_change`) | **correct** |

**So a document that gets three load-bearing numbers exactly right still misdescribes one rule's capability** —
which is the more dangerous kind of error, because the surrounding precision is what earns a reader's trust. A
reviewer skimming correct arithmetic will not re-read a prose sentence.

**The second possibility is the one worth escalating**, and it needs someone who knows the action schema's intent
(Dad / the pack owner), not a heartbeat guess.

## Recommended options, in order of preference

1. **Rename or re-describe the rule** to what it does ("credential-store read", no ordering claim), and leave the
   correlated criticals to `read_after_shell` and the identity-change rules. Cheapest, loses nothing that exists.
2. **Add `prior_event`** once someone defines the prompt-observed class — then the name becomes true.
3. **Do both**: keep this as the path-level detector (honest description), and add a *new* rule id for the true
   ordering. No compatibility break for anyone matching on the existing id.

**Do not** simply add `process.exec`/2m to it — that is option 2's shape with option 1's risk hidden: it quietly
removes the only uncorrelated CRITICAL credential-read alert in the pack.

## Note on method

I first grepped `prior_event` with `grep -B8` and concluded "zero of 20 rules use it". That was **wrong** — the
`-B8` context window spanned into the *following* rule's block and the count came out as 3 hits attributed to the
wrong ids. Re-parsed by splitting on `id:` blocks instead of by line context: 3 of 20 use it, and the flagship is
not among them. **A context-window grep is not a block parser** — the same lesson as beat 644's quote-stripping
bug, in a new costume.

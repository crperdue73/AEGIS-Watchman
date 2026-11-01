# FINDING — 2026-11-01i: an `agent_id:` rule loads clean and can never fire in a multi-agent deployment

**Status:** measured from source. **LATENT false negative — 0 occurrences in the shipped pack.** **NOT APPLIED.**
**Instrument:** `scripts/agentclaim.py`.
**One line:** `AgentKey` is a pair — I checked the host half for three beats and never asked about the agent half.

## The measurement

```go
// internal/rules/engine.go:99
if m.AgentID != "" {
    if agent == nil || agent.ID != m.AgentID { return false }
}

// cmd/collector/main.go:108
var l3agent *schema.Agent
if len(collected) == 1 {
    l3agent = &schema.Agent{ID: filepath.Base(collected[0].Root), Root: collected[0].Root}
}
```

The nil check is **correct** — it fails closed, which is the right direction and I will not pretend otherwise.

The problem is upstream. `l3agent` is bound **only when exactly one agent root is collected**. Watch two agents
and it stays nil, `Engine.Match` receives nil, and **every `agent_id:` rule returns false for every action** —
forever, silently, in the configuration the collector is documented to support.

`behavior.go:211` then does `a := schema.Agent{}` and substitutes it, so central receives events that genuinely
concern an agent **labelled with a zero-valued one**. The agent half of the baseline key becomes `""` for all
events in that mode.

## What validation does about it

`rule.go:261` rejects a match block with no predicates — and `isZeroMatch` counts `agent_id` as a predicate. So
`match: {agent_id: X}` alone is **valid**, loads green, and cannot fire in multi-agent mode. Nothing warns at load;
nothing warns at run.

The error string on the rejection is *"empty match block (would never fire)"* — **the file already rejects rules
for being unfireable.** It just has no model of *capabilities*, so it cannot see that `agent_id` is unfireable in
precisely the mode the collector is running.

## Honest about what was already known

`main.go:107` already says: *"Multi-agent attribution needs a per-agent identity file and is deferred."*

**The limitation is documented.** What is not documented is the consequence: a rule opting into the one predicate
that comment admits is deferred **loads green and then never fires**. That is the difference between a known gap
and a silent one — and from the alert log they look identical, because both produce nothing.

## Honest about what is live

`grep -rn agent_id rules/` → **0 hits.** The shipped pack uses none. This is **latent, not active**, and I am
recording the zero rather than dressing a scenario up as an incident. It becomes live the first time someone
writes that rule — and the pack is exactly where a future author would, because `agent_id` reads like the most
natural way to scope a rule to one agent.

## Where this sits against 724/726/727

Those three were *silences* — a name that means less than it looks like, a clock that isn't what it seems, a
fallback that over-matches. This one is a **false negative with a validation layer standing right next to it that
could have caught it.** Same family as `isZeroMatch`, one step further: it asks *"does this rule have any
predicates?"* and never asks *"can this rule use them here?"*

## Options — NOT applied, all Dad's call

1. **Fail at load.** Refuse to start multi-agent Layer 3 with `agent_id:` rules present. Loudest, strictest;
   breaks a configuration that runs (bent) today.
2. **Warn once at startup** from the rule census the engine already computes. Cheap and honest; also easy to
   ignore, which is a real objection.
3. **Ignore `agent_id` in multi-agent mode** (treat as "any agent"). **I would argue against this one** — it turns
   a silent *no-fire* into a silent *over-fire*, and in a detector an over-fire is the worse failure. A rule that
   quietly widens its scope is a false alarm generator wearing a specific rule's name.

## What I am NOT claiming

- **Not** an active bug. It is latent; the pack has zero uses (measured).
- **Not** that the nil check is wrong. It is right, and it is why this fails closed instead of misattributing.
- **Not** a Go measurement. Source reads; no toolchain here (beat 642).
- **Not** that option 1 is correct. I genuinely do not know whether a fleet should prefer "refuse to start" or
  "start and warn" — that is a deployment-philosophy call, and guessing it on a heartbeat is how a tool acquires
  a policy nobody chose.

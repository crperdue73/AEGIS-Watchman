# Rule packs

Layer 3 rules match on **actions**, not files. Rules are versioned text files —
the Snort rule model: shareable, auditable, community-publishable.

**Format:** YAML, one pack per file (`name`, `version`, `rules: [...]`). A rule
carries an `id` (namespaced), `severity`, optional `technique`/`description`, and
a `match` block over action fields. See `carbonato.yaml` for the seed pack and
`internal/rules/` for the parser/evaluator.

```yaml
name: carbonato
version: "1"
rules:
  - id: carbonato.exfil.egress_after_change
    title: Outbound egress to a chat/C2 endpoint from a build context
    severity: high
    technique: phase3/exfiltration
    match:
      type: net.egress
      endpoint_glob_any: ["*slack.com*", "*api.telegram.org*"]
```

A rule fires when **every** predicate in `match` matches the observed action
(logical AND). `*_any` lists fire if ANY element matches. Globs use `path.Match`
semantics but are also tried against each path suffix, the basename, and a
substring fallback for domain-style globs, so `*/.ssh/*` matches
`/root/.ssh/id_ed25519` and `*slack.com*` matches a full URL.

Initial rule-set target: ~20–30 rules. Seed ideas from the brief (Part 3):

- Agent process spawns a shell or interpreter
- Agent makes outbound calls to a chat service (Slack/Discord/Telegram) from a
  build/CI context, or to a blockchain RPC endpoint
- Agent reads a credential store it has never touched before (novelty)
- Agent requests a NEW tool/scope not in its baseline
- Agent reads a model-endpoint/API-key file (the CARBONATO ordering)
- Agent's identity file changed within N minutes of an outbound call
- Agent process survives its parent user session (persistence)
- Container launched privileged with host mounts (the 2375 entry vector)

> **Status:** the format, parser, and evaluator are implemented in
> `internal/rules/`; `carbonato.yaml` is the seed pack (v6 — 19 rules, including
> three correlation rules). The telemetry adapters that turn real host activity
> (execve, sockets, file opens, container starts) into `action` events live in
> `internal/telemetry/`, and the engine is wired into the collector. Pack
> parsing is strict (unknown keys, malformed globs, and duplicate ids across the
> loaded set are all load errors), and `agentids-rules verify` runs that same
> parser over a pack or directory — see [`../docs/rules.md`](../docs/rules.md).

## Correlation rules (temporal predicates)

Most rules match a single action. A few of the most valuable ones are
**correlations in time** — the brief's canonical example is "the agent's
identity file changed within N minutes of an outbound call." A rule adds a
`prior_event` predicate to say that:

```yaml
  - id: carbonato.exfil.egress_after_identity_change
    severity: critical
    match:
      type: net.egress
      endpoint_glob_any: ["*slack.com*", "*discord.com*"]
      prior_event:
        class: identity.drift   # an event class (or action type)
        within: 5m              # look-back window, required, positive
```

The predicate is decided against a per-host `Timeline` (a small bounded ring of
recent `(time, class)` observations, see `internal/rules/timeline.go`). The
collector feeds **both** halves in: actions are recorded as they are matched,
and Layer 1/2 drift events are recorded via `BehaviorEngine.ObserveDrift`. A
correlation rule fires only when a timeline is attached and the reference time
is non-zero; without them it stays silent rather than guessing. `within: 0s` or
a missing `class` is a pack load error.

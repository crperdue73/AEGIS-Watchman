# Rule packs

Layer 3 rules match on **actions**, not files. Rules are versioned text files —
the Snort rule model: shareable, auditable, community-publishable.

**Format: TBD** (Phase 3 deliverable). Draft direction: YAML, one file per rule,
each rule carries an id, a severity, a match block over the action event fields,
and a description tying it to a technique where possible.

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

> **Status:** empty until Phase 3. This directory exists so the layout is public
> from day one and the rule-pack contract has a home.

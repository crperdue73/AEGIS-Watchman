# agentids — Agent-layer Intrusion Detection

> **Working name.** Placeholder until Rob picks one (see `PHASE0-DISCOVERY.md` §0.5 Q1).

**Snort for AI agents.** A fleet-wide detection tool for agent-layer intrusions —
identity drift, supply-chain tampering, and behavioral rules that name *intent*,
not payloads.

---

## Why this exists

The [CARBONATO](https://www.trendmicro.com/en_us/research/26/i/carbonato-ai-agent-botnet.html)
botnet has **no malicious byte**. It enters through an unauthenticated Docker
daemon (port 2375), installs a legitimate open-source agent, and then overwrites
the agent's persona file — a fifteen-second markdown edit. The objective order is
inverted from history: **AI API keys first, SSH credentials second.**

Generic tooling assumes two things that both break here:

1. EDR/AV assume malware exists as *code*. Here, the "malware" is a config edit
   to a trusted tool.
2. SIEM tooling assumes human-shaped traffic. An agent pursuing an objective
   looks like legitimate automation until it doesn't.

So scan for the *action*, not the package name. If you can't grep your fleet for
someone else's agent identity, you don't have agent hygiene.

## Three detection layers

| Layer | What it detects | Detection value | Cost |
|---|---|---|---|
| **1 — Identity drift** | Content hash of every persona/skill/config file; any change pages an operator, attributed to host + user. | HIGH | LOW |
| **2 — Provenance** | Signature/attestation per agent file; unsigned or unaccounted-for skills flag as untrusted. | HIGH (supply chain) | MEDIUM |
| **3 — Behavioral rules** | Rules that match **actions**, not files — the actual "Snort for agents." | HIGH — the reason this exists | HIGHER |

Layer 3 is the moat. File signatures are evadable by design; you cannot steal
API keys or take C2 over Slack without an action a rule can name. Snort's lesson
exactly: signatures name the payload, good rule engines name the intent.

## Architecture

```
┌───────────────────── HOST ─────────────────────┐
│  Agent process(es) · identity/skill/config files │
│  ┌────────────────────────────────┐              │
│  │  COLLECTOR (one per host)      │              │
│  │  ─ Layer 1: file hasher/watcher│              │
│  │  ─ telemetry adapters:         │              │
│  │      exec · net · file · scope │              │
│  └───────────────┬────────────────┘              │
└──────────────────┼───────────────────────────────┘
                   │  mTLS, batched events
                   ▼
      ┌─────────────────────────────────┐
      │  CENTRAL SERVICE                │
      │  ─ baseline store (per agent)   │
      │  ─ drift compute + diff         │
      │  ─ rules engine (Layer 3)       │
      │  ─ fleet view / dashboard       │
      │  ─ alerting: webhook + email    │
      │  ─ rule pack registry (ver.)    │
      └─────────────────────────────────┘
```

Details: [`PHASE0-DISCOVERY.md`](PHASE0-DISCOVERY.md).

## Status

**Phase 0 — Discovery.** Target environments enumerated, telemetry inventory
drafted, architecture sketched, Layer-1 collector spec + event schema drafted.

**Phase 1 (next)** — Layer 1 MVP: a markdown edit to an agent's persona file
produces an `identity.drift` event that reaches the central service and fires a
webhook **within seconds**. That alone matches the CARBONATO TTP.

## Event schema

The public contract, versioned from day one. See
[`internal/schema/`](internal/schema/) and [`docs/event-schema.md`](docs/event-schema.md).

## Layout

```
cmd/collector/      per-host collector entrypoint
cmd/central/        central rule/alert service entrypoint
internal/schema/    versioned event wire format (the public contract)
internal/collector/ Layer 1 hashing/watching + telemetry adapters
internal/central/   baseline store, drift compute, alerting
internal/rules/     Layer 3 rule format + engine
rules/              example rule packs
deploy/docker/      collector + central containers
docs/               architecture + schema docs
examples/           sample collector config, sample events
testdata/           fixtures
```

## License

Apache-2.0 — see [`LICENSE`](LICENSE).

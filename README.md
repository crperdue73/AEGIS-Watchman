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

Details: [`PHASE0-DISCOVERY.md`](PHASE0-DISCOVERY.md) (the original design pass)
and [`PHASE0-COVERAGE.md`](PHASE0-COVERAGE.md) (what is covered today, what is
not, and the adapter roadmap).

## Status

**Phase 1 — Layer 1 MVP, working end to end.** The full path is real and
verified with the shipped binaries: a markdown edit to an agent's persona file
produces an `identity.drift` event carrying a unified diff, the baseline
survives a central restart, and the event fires a webhook within seconds. That
is the CARBONATO TTP caught, with the payload visible in the alert.

Built so far:

- **Collector** — globbed agent roots, include/exclude, sha256 inventory, HTTP
  submission, poll loop, `-once` / `-dry-run`.
- **Drift** — added/removed/changed file detection, stable ordering, severity
  per file class, and a bounded **unified diff** attached to each event.
- **Central** — `Store` interface with an in-memory and a durable JSON-file
  backend (atomic writes, `0600`, survives restart).
- **Alerting** — `Alerter` interface with three sinks, composed via
  `MultiAlerter`: a **webhook** (JSON POST), the **service log**, and **email**
  (SMTP, port 465 implicit TLS or STARTTLS). Alerts are batched one per ingest
  cycle. Configured in `central.yaml`; delivery failures are logged, never fatal.
- **Transport security** — **mutual TLS** on the collector→central hop
  (`internal/mtls/`): clients require and verify central's cert, and central
  requires and verifies every collector's cert (`RequireAndVerifyClientCert`, so
  an unauthenticated client is refused at the handshake). TLS 1.2 minimum.
  `deploy/gen-certs.sh` generates the CA and leaf material.
- **Layer 2 — provenance / attestation** (`internal/provenance/` + central
  wiring): an **Ed25519-signed approval manifest** of `path -> sha256` per agent
  root, and `agentids-manifest keygen/sign/verify` for the operator workflow.
  Catches what Layer 1 cannot: a **brand-new dropped skill** (nothing drifted,
  but the file was never ours) as `provenance.untrusted`, plus in-place
  modification and deletion. Because the source of truth is *signed*, it is not
  evadable by rewriting a baseline hash. Central verifies every ingest against
  the manifest for the agent's root; `manifests.require_approval` makes an agent
  with no manifest loud instead of silent.
- **Layer 3 — behavior rules** (`internal/rules/` + `internal/telemetry/` +
  `rules/`): a shareable YAML rule pack (Snort model — `id`, `severity`,
  `technique`, a `match` block over action fields) and a deterministic evaluator
  that turns matching actions into `behavior.rule_match` events. Telemetry
  adapters read real host activity (auditd `execve` → `process.exec`, auditd
  `connect` → `net.egress`, auditd `bind`/`listen` → `net.listen`) so rules
  match what an agent *does*, not just what a file looks like. A fourth source,
  the Docker events adapter (`container.start`), watches for a container
  *appearing* on the host with host-level access — the CARBONATO entry vector,
  caught even when it was created via the unauthenticated API on `:2375` rather
  than the `docker` CLI. The seed pack `rules/carbonato.yaml` (v4, 18 rules)
  encodes the CARBONATO chain — secret-store reads, SSH-key argv, chat/C2 egress,
  shell spawn, novel scope, persistence, privileged/host-mounted containers —
  including **correlation rules** decided against a per-host timeline, e.g. an
  outbound call within 5m of an identity-file change. Matches ship to central's
  `/api/v1/actions` and, when a `behavior.spool` path is set, are queued on disk
  first so a central outage costs latency, never evidence.
- **Alert readability** — central resolves numeric actor uids to usernames at
  ingest (`uid_map`, a passwd-style file), so an alert names a person
  (`user=deploy uid=1000`) instead of `uid:1000`. Best-effort: an unknown uid
  stays numeric, and the numeric id is always preserved alongside the name.

The public event schema stays additive-only across all of this.

## Architecture

[`docs/architecture.md`](docs/architecture.md) is the as-built data-flow: the
one-method `Source` contract every telemetry adapter implements, how
`MultiSource` composes them without a single point of failure, how the Engine
and Timeline decide correlation rules, the durable spool, and every budgeted
degradation. It is the document to read before adding a source or a rule.

The **Next** work is a durable fleet view on central plus the kernel-dependent
second half of the eBPF adapter (the CO-RE object and loader behind a build
tag); its kernel-free pieces — `MultiSource` and the build-tagged stub with
 graceful fallback — are already built and wired. Design:
[`docs/R2-EBPF-DESIGN.md`](docs/R2-EBPF-DESIGN.md).

## Event schema

The public contract, versioned from day one. See
[`internal/schema/`](internal/schema/) and [`docs/event-schema.md`](docs/event-schema.md).

## Deploying

[`docs/deploy-collector.md`](docs/deploy-collector.md) covers the Layer 3
operator setup: the auditd rules per action type, the optional eBPF
`file.access` source (kernel requirements + how it degrades), the Docker events
source (the CARBONATO entry vector — a container appearing with host access),
the central `uid_map`, and a post-deploy checklist. Through-line: everything
degrades, nothing there is a hard dependency.

## Layout

```
cmd/collector/      per-host collector entrypoint
cmd/central/        central rule/alert service entrypoint
cmd/manifest/       Layer 2 provenance approval tool (keygen/sign/verify)
internal/schema/    versioned event wire format (the public contract)
internal/collector/ Layer 1 hashing/watching + telemetry adapters
internal/central/   baseline store, drift compute, alerting, mTLS wiring
internal/mtls/      mutual-TLS config loaders (shared by both ends)
internal/provenance/ Layer 2 signed approval manifests + verification
internal/rules/     Layer 3 rule format, parser, and action-matching engine
rules/              published rule packs (carbonato.yaml seed pack)
deploy/docker/      collector + central containers
docs/               architecture + schema docs
examples/           sample collector + central config, sample events
testdata/           fixtures
```

## License

Apache-2.0 — see [`LICENSE`](LICENSE).

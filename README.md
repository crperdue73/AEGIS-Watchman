# watchman — Agent-layer Intrusion Detection

[![CI](https://github.com/crperdue73/AEGIS-Watchman/actions/workflows/ci.yml/badge.svg)](https://github.com/crperdue73/AEGIS-Watchman/actions/workflows/ci.yml)

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

## Deployment shape

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
      │  ─ fleet view: GET / + /api/v1/fleet │
      │  ─ alerting: webhook + email    │
      │  ─ rule pack registry (ver.)    │
      └─────────────────────────────────┘
```

Details: [`PHASE0-DISCOVERY.md`](PHASE0-DISCOVERY.md) (the original design pass),
[`PHASE0-COVERAGE.md`](PHASE0-COVERAGE.md) (what is covered today, what is not,
and the adapter roadmap), and [`PHASE1-PLAN.md`](PHASE1-PLAN.md) (the ordered v1
backlog with acceptance criteria).

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
  root, and `watchman-manifest keygen/sign/verify` for the operator workflow.
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
  `connect` → `net.egress`, auditd `bind`/`listen` → `net.listen`, auditd file
  watches → `file.write`) so rules
  match what an agent *does*, not just what a file looks like. A fourth source,
  the Docker events adapter (`container.start`), watches for a container
  *appearing* on the host with host-level access — the CARBONATO entry vector,
  caught even when it was created via the unauthenticated API on `:2375` rather
  than the `docker` CLI. The seed pack `rules/carbonato.yaml` (v7, 20 rules)
  encodes the CARBONATO chain — secret-store reads, SSH-key argv, chat/C2 egress,
  chain-RPC C2 (a public blockchain RPC provider — the Graphalgo C2 leg), shell
  spawn, novel scope, persistence, privileged/host-mounted containers —
  including **correlation rules** decided against a per-host timeline, e.g. an
  outbound call within 5m of an identity-file change. Matches ship to central's
  `/api/v1/actions` and, when a `behavior.spool` path is set, are queued on disk
  first so a central outage costs latency, never evidence. Rule author's
  reference — the format, every predicate, glob semantics, correlation, and the
  full pack table: [`docs/rules.md`](docs/rules.md).
- **Alert readability** — central resolves numeric actor uids to usernames at
  ingest (`uid_map`, a passwd-style file), so an alert names a person
  (`user=deploy uid=1000`) instead of `uid:1000`. Best-effort: an unknown uid
  stays numeric, and the numeric id is always preserved alongside the name.
- **Fleet view** — every detected event is retained in a durable record store
  (`records`), and central exposes `GET /api/v1/fleet`: recent events rolled up
  by host — total, max severity, last-seen, breakdown by severity, technique,
  signal source, and rule pack version (`by_pack`, e.g. `carbonato@7`) — ordered
  worst-first, with optional `host`, `since`, and
  `min_severity` filters (a single `RecordQuery` every backend serves). `GET /`
  renders the same rollup as a plain
  server-rendered HTML page (no JS framework, no build step), so the console is
  human-readable and scriptable from one source of truth. It answers "which hosts
  are alerting, and with what" without grepping logs, and it works with alerting
  off, because history and paging are separate concerns. Retention is bounded by
  both a time window and a hard cap, so a severity storm can't grow the store
  unbounded. Backends are pluggable behind the `RecordStore` interface: the
  default atomic-JSON file needs no database; a pure-Go SQLite backend
  (`record_backend: sqlite`, built with `-tags sqlite` — **not** CGO) covers scale
  and concurrent readers; Postgres is scoped. See
  [`docs/P2.1C-RECORD-STORE-BACKENDS.md`](docs/P2.1C-RECORD-STORE-BACKENDS.md).
  Operator guide (endpoint, filters, the HTML page, config):
  [`docs/fleet-view.md`](docs/fleet-view.md). To stand central up — the two
  stores, TLS, provenance, alerting, retention — see
  [`docs/deploy-central.md`](docs/deploy-central.md).
- **Agent roster** — `GET /api/v1/agents` is the identity half of the console:
  every agent baseline central holds (host, root, and each identity file's path,
  sha256, size, mtime — never the file body), filterable by `host` and `root`,
  each file carrying a Layer 2 `trust` verdict (`ok` / `modified` / `unsigned` /
  `unverified`) against its approved manifest. It reads the durable **baseline
  store**, so it answers "grep my fleet for every agent's identity" — *what every
  agent is, and whether it is approved* — **before any event has fired**: the
  check the CARBONATO counter is built around, and the brief's *"every agent, its
  baseline"* differentiator made queryable. Details: [`docs/fleet-view.md`](docs/fleet-view.md).

## Getting started

In a hurry? [`docs/quickstart.md`](docs/quickstart.md) is the one-page path:
build → run the demo → see an alert, with links out for depth.

Four binaries: `watchman-collector` (one per host), `watchman-central` (the
service), `watchman-manifest` (Layer 2 approvals), and `watchman-rules`
(Layer 3 pack tooling). Build them all from source:

```sh
git clone <repo> && cd watchman
make                              # builds cmd/{collector,central,manifest,rules} into ./bin
go build -tags sqlite ./cmd/...   # optional: adds the pure-Go SQLite record backend (no CGO)
go test ./...                     # no root, no live daemon required
```

### The one prerequisite that bites people first: auditd

Layer 3 matches *actions*, and the actions come from auditd — which does **not**
audit `connect`, `bind`, or `listen` out of the box. Enable them before you
expect any behavioral rule to fire:

```
# /etc/audit/rules.d/watchman.rules
-a always,exit -F arch=b64 -S execve   -k watchman
-a always,exit -F arch=b64 -S connect  -k watchman
-a always,exit -F arch=b64 -S bind     -k watchman
-a always,exit -F arch=b64 -S listen   -k watchman
```

```sh
sudo augenrules --load        # or: sudo service auditd restart
```

Then point the collector at the log:

```yaml
behavior:
  rules_dir: /etc/watchman/rules
  audit_log: /var/log/audit/audit.log
```

**If you skip this, the collector runs and the rules load — and nothing fires.**
That silence is the most common first-run confusion. `execve` alone is the
minimum; `connect` is what makes the flagship correlation rule
(`egress_after_identity_change`) decidable, and `bind`/`listen` feed the
listener rule. Full details and the per-rule breakdown:
[`docs/deploy-collector.md`](docs/deploy-collector.md) §1.

### Run it locally

From the repository root — the `.local` configs use only paths that exist in a
fresh clone and no `/etc/watchman/...` system paths. The collector's dry-run
needs nothing else running:

```sh
./bin/watchman-collector -config examples/collector.local.yaml -once -dry-run
```

To submit for real, start the service in another terminal first (it runs in the
foreground) and drop `-dry-run`:

```sh
./bin/watchman-central -config examples/central.local.yaml            # terminal 1
./bin/watchman-collector -config examples/collector.local.yaml -once  # terminal 2
```

`-dry-run` validates the config **and the rule pack** (the fail-loud checks
startup would run), prints the inventory, and ships nothing; `-once` does a
single pass instead of the poll loop. The operator templates with real system
paths, TLS, provenance, and alerting are `examples/central.yaml` and
`examples/collector.yaml`. Every binary answers `-version` (and
`watchman-manifest version`) with its version, git commit, and build date —
stamped at link time by `make build`.

**To see a detection with no root, no live auditd, and no Docker daemon, run the
demo:** `cd demo && ./run.sh` — it replays captured fixtures through the real
binaries, prints the alerts (including the critical container-entry-vector
match), then prints the fleet rollup from `GET /api/v1/fleet`. See
[`demo/README.md`](demo/README.md).

## Known blind spots

A public detection tool that hides its blind spots is a liability. What this
**cannot** see today, with the gap id, kept honest against
[`PHASE0-COVERAGE.md`](PHASE0-COVERAGE.md):

- **Managed / cloud-hosted agents (G6).** An agent that runs inside a provider's
  control plane emits no host telemetry the collector can read. No host, no
  auditd, no actions — it is invisible to this tool by construction. Owned here in
  the docs rather than implied-as-covered.
- **`file.access` on a stock build (G3, read half).** Reads of a secret file are
  caught today only when the read is visible in `argv` (e.g. `cat ~/.ssh/id_ed25519`). A
  process that reads a secret without naming it on a command line needs the eBPF
  source, whose kernel-dependent half is not built (P2.0). The **write** half is
  built (`file.write`, from auditd file watches — `carbonato.persist.file_write`),
  so a persistence write is caught from the kernel even when argv never names the
  file; the read half still needs eBPF.
- **`scope.change` is partial (G5).** "The agent changed its own scope" is
  detected heuristically from host actions, not from an agent-native control-plane
  log. The first slice lands as P1.3; the full signal is still open.
- **Container telemetry needs Docker enabled (G4 — closed when enabled).** The
  adapter is off by default; a host with it disabled gets no `container.start`
  events, and the container rules stay quiet. A config choice, not a gap — but a
  silent one, so it is named here.

Everything else in the forced-action matrix either fires end to end or is listed
with an owner in the coverage matrix.

## Architecture (as built)

[`docs/architecture.md`](docs/architecture.md) is the as-built data-flow: the
one-method `Source` contract every telemetry adapter implements, how
`MultiSource` composes them without a single point of failure, how the Engine
and Timeline decide correlation rules, the durable spool, and every budgeted
degradation. It is the document to read before adding a source or a rule.

The **Next** work is the kernel-dependent
second half of the eBPF adapter (the CO-RE object and loader behind a build
tag); its kernel-free pieces — `MultiSource` and the build-tagged stub with
 graceful fallback — are already built and wired. Design:
[`docs/R2-EBPF-DESIGN.md`](docs/R2-EBPF-DESIGN.md). The Postgres record backend
is scoped, on demand
([`docs/P2.1C-RECORD-STORE-BACKENDS.md`](docs/P2.1C-RECORD-STORE-BACKENDS.md)).

## Event schema

The public contract, versioned from day one. See
[`internal/schema/`](internal/schema/) and [`docs/event-schema.md`](docs/event-schema.md).

## Deploying

Watchman has two deployable halves, and each has its own operator guide.

**Central** — the service the whole fleet talks to — is
[`docs/deploy-central.md`](docs/deploy-central.md): the two stores (baseline vs.
record) and why they are separate, the mutual-TLS trust boundary and its
fail-closed validation, Layer 2 provenance, the `uid_map`, alerting sinks and
non-fatal delivery, and the retention/backend bounds.

**The collector** — one per host — is
[`docs/deploy-collector.md`](docs/deploy-collector.md): the auditd rules per
action type, the optional eBPF `file.access` source (kernel requirements + how
it degrades), the Docker events source (the CARBONATO entry vector — a
container appearing with host access), and a post-deploy checklist.
Through-line for both: everything degrades, nothing there is a hard dependency.
Every `collector.yaml` key, with its default, is in
[`docs/collector-config.md`](docs/collector-config.md); every `central.yaml` key
is commented in [`examples/central.yaml`](examples/central.yaml).

## Layout

```
cmd/collector/      per-host collector entrypoint
cmd/central/        central rule/alert service entrypoint
cmd/manifest/       Layer 2 provenance approval tool (keygen/sign/verify)
cmd/rules/          Layer 3 rule tooling (verify/list a pack set)
internal/schema/    versioned event wire format (the public contract)
internal/collector/ Layer 1 hashing/watching + telemetry adapters
internal/central/   baseline store, drift compute, alerting, mTLS wiring
internal/mtls/      mutual-TLS config loaders (shared by both ends)
internal/provenance/ Layer 2 signed approval manifests + verification
internal/rules/     Layer 3 rule format, parser, and action-matching engine
rules/              published rule packs (carbonato.yaml seed pack)
deploy/             systemd units, TLS cert helper, container image
docs/               architecture + schema docs
examples/           sample collector + central config, sample events
testdata/           fixtures
```

## Security

This is a security tool, so its own posture is part of the product. watchman is
**alert-only** — it never remediates — and no telemetry leaves a host except to
the configured central endpoint. Found a vulnerability in watchman itself?
Don't open a public issue; report it privately per [`SECURITY.md`](SECURITY.md),
which also names the known blind spots and what is by-design rather than a bug.

## Code of conduct

Participation in this project is covered by [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)
(Contributor Covenant 2.1). Be decent; report concerns the way that file
describes.

## Changelog

What's built so far, and what's deliberately not — see
[`CHANGELOG.md`](CHANGELOG.md). watchman is pre-1.0; changes land on `main`, and
the event wire format is versioned separately as `watchman.event/v1`.

## License

Apache-2.0 — see [`LICENSE`](LICENSE).

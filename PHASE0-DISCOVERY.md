# PHASE 0 — DISCOVERY

**Project:** Carbonato-class Agent Intrusion Detection Tool
**Brief:** `BRIEF.md`
**Status:** In progress — started Sept 27, 2026
**Owner:** Vivi (Software Developer)
**Repo:** GitHub access expected Sept 28, 2026 — PUBLIC, Apache-2.0

> **Update (Sept 28):** the engine is now functionally complete, so discovery
> was re-run *against the code* rather than against a guess. The result is
> [`PHASE0-COVERAGE.md`](PHASE0-COVERAGE.md) — a forced-action coverage matrix,
> a gap register (G1–G6), and a prioritised adapter roadmap (R1–R6). Read that
> for the current state; this file is the original, pre-code pass and is kept
> for how the architecture was decided.

---

## Purpose

Phase 0 answers ONE question before a line of product code gets written:

> **What actually runs agents, where do their identity/skill/config files live,
> and what telemetry already exists on those hosts?**

The answer decides the architecture (Part 4). Per the brief's Part 5 Q2, the
tentative lean is *integrate existing telemetry, own the rules layer* — so Phase 0
has to prove a real telemetry source exists for each of the three detection
layers, or that layer needs its own collector.

---

## 0.1 — Target environments

| Environment | How agents run | Where identity files live | Notes |
|---|---|---|---|
| Bare host (Linux) | agent process under a service account | `~/.openclaw/workspace-*/` (SOUL.md, IDENTITY.md, MEMORY.md, skills/) | Our own fleet shape. Cheapest to instrument. |
| Docker | container per agent / per workload | bind-mounted workspace volume | **CARBONATO entry vector.** Privileged + host mounts = the 2375 problem. |
| Kubernetes | pod per agent, or agent sidecar | ConfigMap / PVC / image layer | Harder: files may be in config maps, not on disk. |
| CI/CD runners | agent invoked in a build step | checked-in skill/persona files in repo | Graphalgo vector: hidden trigger conditions. |
| Managed/cloud agents | vendor-hosted | vendor console / API | Out of scope for v1 collector; note as a gap. |
| Developer workstation | interactive agent CLI | local dotfiles / workspace dir | The most common real-world case. Easy to forget. |

**Takeaway:** if v1 targets "one binary/container per host" (per the brief),
then bare-host + Docker cover the majority of a small fleet. K8s and CI are
Phase-3+ adapters, not v1 blockers.

---

## 0.2 — Telemetry source inventory (per layer)

### Layer 1 — Identity drift (file state)
- **Needs:** content hash + mtime + owner of persona/skill/config files, plus a
  record of *who* changed them.
- **Sources that exist today:**
  - Filesystem walk + `sha256` — always available, no privileges needed. **v1 uses this.**
  - `inotify` / fanotify — real-time change events on Linux, no poll gap.
  - `auditd` watch rules on the file paths — gives the acting **uid** (attribution).
  - Docker: `docker events` + bind-mount inspection for container-launched agents.
- **Gap:** attribution (who) needs auditd or the collector running as root.
  A pure userspace hash-poller can detect *that* it changed but not *who*.
  **Decision to make:** ship collector as root-capable (attribution) or
  userspace-only v1 (drift without actor)?

### Layer 2 — Provenance / supply chain
- **Needs:** signature/attestation per agent file, approved-skill manifest.
- **Sources:** none exist yet — this layer *creates* the data. Signing scheme
  (GPG/minisign/sigstore) is a Phase-2 design decision, not a discovery blocker.
  Note: this tool should sign its OWN rule packs later (Phase 3), same primitives.

### Layer 3 — Behavioral rules engine
- **Needs:** process exec, network egress (with endpoint classification), file
  access, tool/scope change events — all attributed to an agent.
- **Sources that exist today:**
  - Process exec: `eBPF` (best, kernel-level, no agent cooperation) or auditd.
  - Network egress: eBPF / conntrack / netflow — needs endpoint classification
    (is this "a chat service"? "a blockchain RPC"? — see rule set below).
  - File access: `fanotify` / eBPF LSM / auditd.
  - Tool/scope changes: **agent-native** — read from the agent's own tool
    registry/logs. May need a small agent-side shim. This is the one source
    that isn't pure OS telemetry.
- **Gap:** eBPF needs a modern kernel + caps; auditd is universal but noisier.
  **v1 should abstract behind a "telemetry adapter" interface** so a host can use
  eBPF where available and auditd elsewhere.

---

## 0.3 — Architecture sketch (first cut, to be refined)

> **Superseded:** this first-cut sketch was written before any code. The
> as-built data-flow — the one-method `Source` contract, `MultiSource`
> composition, the Engine/Timeline, the durable spool, and every budgeted
> degradation — now lives in [`docs/architecture.md`](architecture.md). Read
> that for how it actually works; this section is kept only as the original
> intent for the record.

```
┌───────────────────── HOST ─────────────────────┐
│  Agent process(es)                               │
│  Identity/skill/config files  ──────────┐        │
│                                          │        │
│  ┌────────────────────────────────┐     │        │
│  │  COLLECTOR (one per host)      │◄────┘ hash+diff
│  │  ─ Layer 1: file hasher/watcher│              │
│  │  ─ telemetry adapters:         │◄── eBPF/auditd/conntrack
│  │      exec · net · file · scope │              │
│  │  ─ local rule eval (opt.)      │              │
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

**Design principles for the public repo:**
- Collector is a single static binary (Go likely — small, cross-compiles,
  good eBPF + cloud-native ecosystem) + a container image.
- Central service stateless-ish, store behind an interface (SQLite for dev,
  Postgres for prod).
- Events are a versioned schema from day one — public repo, other people build
  on this. Wire format stability matters more than early features.
- Alert-only in v1. No auto-remediation, ever, without an explicit opt-in design.

---

## 0.4 — Layer-1 collector spec (draft)

**Responsibility:** inventory + hash identity/persona/skill/config files for
every agent on the host; report changes with attribution.

**Config (per host, declarative e.g. `collector.yaml`):**
```yaml
agent_roots:
  - path: /home/*/.openclaw/workspace-*
    include: ["SOUL.md", "IDENTITY.md", "MEMORY.md", "skills/**/*.md", "*.config.json"]
    exclude: ["**/*.tmp", "**/.git/**"]
hash: sha256
watch:
  mode: inotify        # or: poll (fallback)
  poll_interval: 60s
report:
  endpoint: https://central.example/api/v1/events
  interval: 30s
attribution:
  source: auditd       # optional; enables "who changed it"
```

**Event schema (v1 draft — this is the public contract):**
```json
{
  "schema": "agentids.event/v1",
  "event": "identity.drift",
  "ts": "2026-09-28T00:00:00Z",
  "host": {"id": "...", "name": "..."},
  "agent": {"id": "...", "root": "/home/x/.openclaw/workspace-x"},
  "file": {"path": "...SOUL.md", "sha256_before": "...", "sha256_after": "...",
           "size_before": 1234, "size_after": 1250, "diff": "@@ ... @@"},
  "actor": {"uid": 1000, "user": "x", "pid": 4242},   // null if unavailable
  "severity": "high"
}
```

**Acceptance for Phase 1:** a markdown edit to an agent's SOUL.md produces an
`identity.drift` event that reaches the central service and fires a webhook
**within seconds** — the CARBONATO TTP, caught.

---

## 0.5 — Decisions needed from Rob (Part 5, restated)

1. **Name.** Repo name depends on it. (WATCHMAN / WARDEN / other)
2. **Build vs. integrate telemetry.** Discovery lean: integrate (eBPF/auditd),
   own the rules engine. Confirm.
3. **v1 target:** our own fleet first (recommended) vs. a client pilot.
4. **Open-source the rule packs?** (lean: yes; engine + fleet UI stay paid)
5. **License:** public repo + Apache-2.0 is set. Confirm no CLA / DCO preference.

**Blocking for repo creation tomorrow:** Q1 (name). Everything else can be
answered in parallel.

---

## Log
- **2026-09-27** — Phase 0 started. Environments enumerated, telemetry inventory
  drafted, first architecture sketch + Layer-1 event schema written. Awaiting
  GitHub repo access + name decision.

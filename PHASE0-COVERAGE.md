# PHASE 0 — COVERAGE MATRIX & ADAPTER ROADMAP

**Project:** Carbonato-class Agent Intrusion Detection Tool
**Companion to:** `PHASE0-DISCOVERY.md` (the original discovery pass)
**Status:** Started Sept 28, 2026 — against the *finished* Layer 3 engine
**Owner:** Vivi (Software Developer)

---

## Why this document exists

`PHASE0-DISCOVERY.md` was written before any code existed: it guessed at what
telemetry *might* be available and what the architecture *should* be. That was
the right first pass. But the engine is now functionally complete — Layers 1,
2, and 3 all run end to end, verified by 150 tests and a live e2e on real
binaries.

So the discovery question changes. It is no longer *"what could we build on?"*
It is now:

> **For every action a CARBONATO-class attacker must take, is there a working
> telemetry source that emits it today — or is it a named gap with an owner?**

This document answers that question line by line, against the code, and turns
the answer into a prioritised adapter roadmap. It is the bridge from "engine's
whole" to "what do we ship first after the repo goes public."

---

## 0.1 — The attacker's forced-action chain

An intrusion of this class has no malicious byte, but it is *not* free of
actions. To get from "unauthenticated Docker" to "API keys exfiltrated over
Slack," the operator must necessarily do these things. Each is a detection
opportunity; each is tagged with the layer that owns it and whether we can see
it **today**.

| # | Forced action | Layer | Telemetry needed | Have it? |
|---|---|---|---|---|
| A1 | A persona/skill/config file is **edited or dropped** on an agent host | 1 / 2 | file hash + mtime + owner | ✅ shipped |
| A2 | The edit is made by someone we can **attribute** | 1 | acting uid/pid | ⚠️ partial (see G1) |
| A3 | A skill/persona file is **not ours** (unsigned, unmanifested) | 2 | signed approval manifest | ✅ shipped |
| A4 | The agent **spawns a process** (shell, cat, curl, installer) | 3 | execve + argv | ✅ shipped (auditd) |
| A5 | The agent **reads a secret** (SSH key, model key, env) | 3 | argv/file access | ✅ via argv · ❌ via file.read (see G3) |
| A6 | The agent makes an **outbound connection** (chat / C2 / RPC) | 3 | connect + peer classification | ✅ shipped (auditd) |
| A7 | The agent **opens a listener** (C2 / reverse shell) | 3 | bind/listen | ✅ shipped (R1, auditd bind/listen) |
| A8 | The intrusion **persists** past session end (cron, rc, unit) | 3 | file write to known paths | ✅ via argv heuristics · ❌ via file.write (see G3) |
| A9 | The agent **escapes its container** (privileged + host mount) | 3 | container runtime events | ❌ gap (see G4) |
| A10 | The agent **changes its own scope** (new tool, new API key) | 3 | agent-native control-plane log | ❌ gap (see G5) |

**Reading of the matrix:** 7 of 10 forced actions are detected today on real
telemetry. One more (A10) has rule and schema support but no live feed.
Two (A2 attribution, A9 container escape) are genuine gaps with a clear owner
and a clear adapter — nobody is currently claiming they're covered.

That is the honest Phase 0 statement: **70% covered end to end, 10% half-wired,
20% named gaps.** For a tool that has not yet shipped v1, that is a strong
position — and more importantly, none of the gaps are *architectural*. They are
all "write the next adapter behind the `telemetry.Source` interface."

---

## 0.2 — Implemented telemetry, by source type

Grounded in the actual packages, not the plan.

| Source | Package | Emits | Status |
|---|---|---|---|
| auditd `execve`/`execveat` | `internal/telemetry` (`text.go`, `telemetry.go`) | `process.exec` + argv, pid, uid, cwd | ✅ live, tested |
| auditd `connect` | `internal/telemetry/egress.go` | `net.egress` + peer addr, port | ✅ live, tested |
| auditd file-watch | (planned) | `file.access` (writes to watched paths) | ❌ not built |
| filesystem walk + sha256 | `internal/collector/drift.go` | `identity.drift` + unified diff | ✅ live, tested |
| Ed25519 approval manifest | `internal/provenance`, `cmd/manifest` | `provenance.untrusted` | ✅ live, tested |
| text stream (tests/demo) | `internal/telemetry/text.go` | `process.exec` | ✅ test/demo only |
| eBPF | (planned) | all four action types, kernel-level | ❌ not built |
| Docker events | (planned) | container lifecycle, privileged mounts | ❌ not built |
| agent control-plane log | (planned) | `scope.change`, tool registration | ❌ not built |

**The seam that makes the gaps cheap:** every source above implements one
interface —

```go
type Source interface {
    Actions() ([]Event, error)
}
```

Adding a source is adding a file that satisfies `Actions()`. The rules engine,
the correlation timeline, the spool, the central service, and the integration
tests do not change. **This is the single most important discovery finding: the
remaining work is adapter work, not architecture work.**

---

## 0.3 — Gap register

Each gap gets an ID, an owner layer, and a priority. Priority is value ÷ cost,
biased toward what the brief calls out as the *reason the tool exists*.

### G1 — Actor attribution is best-effort numeric, not resolved
- **What's missing:** `Actor.User` is `"uid:1000"` (see `AuditRecord.user()`),
  not a name. Human-readable attribution ("the deploy user changed SOUL.md")
  needs either `/etc/passwd` resolution at central, or auditd's `auid` mapped.
- **Why it matters:** the brief's Layer 1 value prop is *"attributed to a host
  and a user."* A numeric uid is a host; it barely qualifies as a user.
- **Priority:** HIGH (cheap). Resolve at central, from an ingested passwd map or
  the event's `auid`. Does not touch the collector hot path.
- **Owner:** central.

### G2 — Listener/bind telemetry — ✅ CLOSED (R1, Sept 28)
- **Was:** `carbonato.net.new_listener` existed but no adapter emitted a
  listener-shaped action — dead code.
- **Fix:** `net.listen` action from auditd `bind`/`listen`; rule
  `carbonato.net.listener_bound` (pack v3). Ships with tests.
- **Owner:** collector (`internal/telemetry`).

### G3 — No kernel file-access telemetry (`file.access`)
- **What's missing:** secret *reads* are caught today only when the path is
  visible in argv (`cat ~/.ssh/id_ed25519`). A process that opens a key file
  without naming it on the command line is invisible to the execve adapter.
- **Why it matters:** "read the model key" is the whole point of the intrusion.
  argv heuristics are evadable by one `python -c` wrapper.
- **Priority:** HIGH. This is the strongest argument for eBPF.
- **Owner:** collector — new eBPF adapter (see roadmap R2).

### G4 — No container-runtime telemetry
- **What's missing:** the CARBONATO *entry vector* is unauthenticated Docker on
  :2375 with privileged, host-mounted containers. We can detect what the agent
  does *inside* the host, but not that a privileged container was launched at
  all — the earliest possible signal.
- **Why it matters:** catching it at container-launch is catching it before any
  agent even runs. This is the difference between "we saw the exfil" and "we
  stopped the intrusion."
- **Priority:** MEDIUM-HIGH. `docker events` is a cheap first step (no kernel
  work); it emits `container create` with the privileged/host-mount flags.
- **Owner:** collector — new Docker events adapter (roadmap R3).

### G5 — No agent-native scope/tool telemetry
- **What's missing:** `scope.change` and tool registration are agent-internal.
  No OS telemetry carries them; they need a small agent-side shim or a read of
  the agent's own control-plane log.
- **Why it matters:** "novel scope" is a first-class CARBONATO rule
  (`carbonato.tool.scope_new`) with no data behind it yet.
- **Priority:** MEDIUM. Depends on which agent framework a client runs;
  design-parallel, not a v1 blocker.
- **Owner:** collector + agent integration.

### G6 — Managed/cloud agents are entirely out of band
- **What's missing:** vendor-hosted agents expose no host filesystem and no
  execve stream. Out of scope for the v1 collector by original decision.
- **Why it matters:** should be **stated plainly in the README** as a known
  blind spot, so no operator assumes coverage they don't have. Honesty in the
  public docs is a feature.
- **Priority:** LOW (documentation, not code).
- **Owner:** docs.

---

## 0.4 — Adapter roadmap (post-Layer-3, prioritised)

Ordered by value ÷ cost. Each is a new `telemetry.Source` (or a branch in an
existing one) — no engine work.

| # | Adapter | Closes | Source of truth | Cost |
|---|---|---|---|---|
| **R1** | auditd `bind`/`listen` → `net.listen` action | G2 | auditd (already enabled if `-S connect` is) | ✅ DONE |
| **R2** | eBPF `file.access` (open/openat on secret paths) | G3, A5, A8 | eBPF, kernel-level | HIGH |
| **R3** | Docker events adapter (privileged + host-mount detection) | G4, A9 | `docker events` stream | MEDIUM |
| **R4** | central-side uid→name resolution | G1 | ingested passwd map / `auid` | LOW |
| **R5** | agent control-plane shim → `scope.change` | G5, A10 | agent-native log | MEDIUM |
| **R6** | README "known blind spots" section | G6 | docs | LOW |

**Recommended next after the public cut: R4 (trivial, makes every alert
human-readable), then R4-adjacent R2 scoping.** R1 is done (G2 closed, matrix at
7/10). R2 (eBPF) is the strategic bet and should be scoped as its own phase, not
a heartbeat step.

---

## 0.5 — Discovery conclusions (for the public README / v1 statement)

1. **The architecture in `PHASE0-DISCOVERY.md` held.** The `telemetry.Source`
   seam absorbed `connect` as a second telemetry shape with zero engine change.
   That validates the "integrate existing telemetry, own the rules layer" lean.
2. **auditd is sufficient for v1.** It carries execve and connect today with no
   kernel module and no caps beyond the audit subsystem. eBPF is the *upgrade*,
   not the prerequisite.
3. **The gaps are adapter-shaped, not architectural.** 6/10 forced actions are
   covered end to end today; the other 4 are named, owned, and each is a new
   file behind one interface.
4. **Attribution quality is the quiet risk.** Detection is strong; *"who did
   it"* is best-effort numeric. Fixing G1 is cheap and should precede any demo
   to a human operator — an alert that names a uid instead of a person reads as
   unfinished.
5. **Two blind spots must be documented, not hidden:** managed/cloud agents
   (G6) and container-runtime events (G4) until R3 lands. Stating them is a
   trust feature for a public security tool.

---

## Log
- **2026-09-28** — **R1 done.** G2 closed: auditd `bind`/`listen` → `net.listen`
  action, rule `carbonato.net.listener_bound` (pack v3). Matrix moves 6/10 →
  7/10 detected. Next: R4 (uid→name).
- **2026-09-28** — Coverage matrix + adapter roadmap written against the
  finished engine. 10 forced actions mapped to layers and telemetry; 6 covered
  live, 2 half-wired (rule/schema exists, no feed), 2 named gaps. Gap register
  G1–G6 with owners and priorities; roadmap R1–R6. Next concrete adapter: R4
  (uid→name) then R1 (auditd bind/listen). Blocking: repo access + NAME.

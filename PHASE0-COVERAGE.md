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
| A2 | The edit is made by someone we can **attribute** | 1 | acting uid/pid | ✅ shipped (G1 closed) |
| A3 | A skill/persona file is **not ours** (unsigned, unmanifested) | 2 | signed approval manifest | ✅ shipped |
| A4 | The agent **spawns a process** (shell, cat, curl, installer) | 3 | execve + argv | ✅ shipped (auditd) |
| A5 | The agent **reads a secret** (SSH key, model key, env) | 3 | argv/file access | ✅ via argv · ❌ via file.read (see G3) |
| A6 | The agent makes an **outbound connection** (chat / C2 / RPC) | 3 | connect + peer classification | ✅ shipped (auditd) |
| A7 | The agent **opens a listener** (C2 / reverse shell) | 3 | bind/listen | ✅ shipped (R1, auditd bind/listen) |
| A8 | The intrusion **persists** past session end (cron, rc, unit) | 3 | file write to known paths | ✅ argv heuristics · ✅ `file.write` (kernel; G3 write half) |
| A9 | The agent **escapes its container** (privileged + host mount) | 3 | container runtime events | ✅ via `docker events` (`container.start` rules, G4 closed) |
| A10 | The agent **changes its own scope** (new tool, new API key) | 3 | agent-native control-plane log | ❌ gap (see G5) |

**Reading of the matrix:** 8 of 10 forced actions are covered end to end today
on real telemetry. The two that are not are *partial*, not missing: **A5** (a
secret read) is caught via argv but not via a kernel file-open — the eBPF half,
still gated on the toolchain (P2.0) — and **A10** (scope change) has rule and
schema support and a live *heuristic* feed, but not yet an agent-native one
(G5). No forced action lacks a rule; every remaining gap is a telemetry source
behind the `telemetry.Source` interface.

That is the honest Phase 0 statement: **80% covered end to end, 20%
half-wired** — both half-wired items with a named next adapter, neither
architectural.

---

## 0.2 — Implemented telemetry, by source type

Grounded in the actual packages, not the plan.

| Source | Package | Emits | Status |
|---|---|---|---|
| auditd `execve`/`execveat` | `internal/telemetry` (`text.go`, `telemetry.go`) | `process.exec` + argv, pid, uid, cwd | ✅ live, tested |
| auditd `connect` | `internal/telemetry/egress.go` | `net.egress` + peer addr, port | ✅ live, tested |
| auditd file-watch | `internal/telemetry/filewatch.go` | `file.write` + the watched path (nametype, watch key) | ✅ live, tested |
| filesystem walk + sha256 | `internal/collector/drift.go` | `identity.drift` + unified diff | ✅ live, tested |
| Ed25519 approval manifest | `internal/provenance`, `cmd/manifest` | `provenance.untrusted` | ✅ live, tested |
| text stream (tests/demo) | `internal/telemetry/text.go` | `process.exec` | ✅ test/demo only |
| Docker events | `internal/telemetry/docker.go` | `container.start` + identity/risk (HostConfig via `docker inspect`) | ✅ live, tested (opt-in, off by default; G4 closed) |
| eBPF | (planned) | `file.access` at the kernel — the G3 *read* half | ❌ not built (P2.0, toolchain-gated) |
| agent control-plane log | (planned) | `scope.change`, tool registration (native) | ❌ not built — a `scope.change` *heuristic* shim ships (`internal/telemetry/scope.go`; G5/R5) |

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

### G1 — Actor attribution — ✅ CLOSED (R4, Sept 28)
- **Was:** `Actor.User` was `"uid:1000"`, not a name. The brief's Layer 1 value
  prop is *"attributed to a host and a user"* — a numeric uid is barely a user.
- **Fix:** central-side `UidResolver` (`internal/central/uidmap.go`) maps a
  passwd-style `uid_map` to names at ingest, applied to every event before
  alerting. Best-effort: unknown uids stay numeric; the id is always kept.
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
- **Write half closed (2026-09-30):** `file.write` from auditd file watches
  (`internal/telemetry/filewatch.go`) now catches a persistence write from the
  kernel — the path the kernel reported, not the command line — with no eBPF
  object at all. A cron drop or an rc edit by a wrapped interpreter fires
  `carbonato.persist.file_write`. Only the **read** half still needs R2.
- **Priority:** HIGH. This is the strongest argument for eBPF.
- **Owner:** collector — new eBPF adapter (see roadmap R2) for the read half.

### G4 — No container-runtime telemetry ✅ CLOSED (R3)
- **What was missing:** the CARBONATO *entry vector* is unauthenticated Docker on
  :2375 with privileged, host-mounted containers. We could detect what the agent
  does *inside* the host, but not that a privileged container was launched at
  all — the earliest possible signal.
- **Why it mattered:** catching it at container-launch is catching it before any
  agent even runs. This is the difference between "we saw the exfil" and "we
  stopped the intrusion."
- **Closed by:** `internal/telemetry/docker.go` — a `DockerSource` reading
  `docker events` (daemon-emitted, so it does not need the `docker` CLI to have
  been typed — it catches the API-on-:2375 path) emitting `container.start`
  actions with identity + risk detail, optionally enriched with HostConfig via
  `docker inspect`. Wired into the collector like eBPF (additive, degrades), and
  matched by three new critical rules in `rules/carbonato.yaml` v4
  (`privileged_start`, `host_root_mount`, `after_identity_change`). Rule engine
  gained `detail_non_empty_any` / `detail_glob_any` to express them.
- **Owner:** collector — Docker events adapter (roadmap R3). **Done.**

### G5 — No agent-native scope/tool telemetry — ⚠️ PARTIAL (R5, P1.3)
- **What was missing:** `scope.change` and tool registration are agent-internal.
  No OS telemetry carries them; they need a small agent-side shim or a read of
  the agent's own control-plane log.
- **Partial fix (P1.3):** the collector's `scope` shim (`internal/telemetry/scope.go`)
  *derives* `scope.change` from first-use telemetry we already have — a new
  listener, a new outbound endpoint, a new executable under an agent-owned path.
  Every derived action is labelled `detail.source="heuristic"` so a reader can
  tell a derivation from ground truth; `carbonato.tool.scope_new` fires on it
  (pack v5).
- **Still open:** it sees first *use*, not a held-but-unused capability (no
  inventory to diff against). The full fix remains an agent-native control-plane
  adapter emitting `detail.source="agent"` — the same shape, so the same rule
  fires unchanged.
- **Restart behaviour (updated):** the seen-capability set is now **durable** —
  persisted beside the spool (`scope.seen`, else `<spool>.scope-seen`) and capped
  (`DefaultScopeSeenMax`), so a collector restart no longer re-reports the host's
  established capabilities as novel. An unreadable cache is discarded and
  rebuilt, never fatal (derived telemetry degrades, D6).
- **Priority:** MEDIUM. The heuristic shim closes the *rule* end of A10 for v1;
  the authoritative feed depends on which agent framework a client runs.
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
| **R2** | eBPF `file.access` (open/openat on secret paths) | G3, A5, A8 | eBPF, kernel-level | HIGH — in progress: design + R2.0 (`MultiSource`) done, see `docs/R2-EBPF-DESIGN.md` |
| **R3** | Docker events adapter (privileged + host-mount detection) | G4, A9 | `docker events` stream | ✅ DONE — `internal/telemetry/docker.go`, pack v4 |
| **R4** | central-side uid→name resolution | G1 | passwd-style `uid_map` file | ✅ DONE |
| **R5** | agent control-plane shim → `scope.change` | G5, A10 | agent-native log | MEDIUM — ⚠️ partial (P1.3): `internal/telemetry/scope.go` derives `scope.change` from first-use telemetry, labelled `heuristic`; authoritative feed still open |
| **R6** | README "known blind spots" section | G6 | docs | ✅ DONE — README "Known blind spots" |

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
3. **The gaps are adapter-shaped, not architectural.** 8/10 forced actions are
   covered end to end today; the other 2 are partial (A5 via argv not kernel,
   A10 via a heuristic not the control plane), each a new file behind one
   interface.
4. **Attribution quality is the quiet risk.** Detection is strong; *"who did
   it"* is best-effort numeric. Fixing G1 is cheap and should precede any demo
   to a human operator — an alert that names a uid instead of a person reads as
   unfinished.
5. **Blind spots must be documented, not hidden:** managed/cloud agents (G6) and
   the still-open pieces of scope telemetry. Stating them is a trust feature for
   a public security tool. (G4 — the container entry vector — is now *closed*,
   not a blind spot; see R3 in the roadmap.)

---

## Log
- **2026-09-30** — **Fleet view surfaces pack provenance.** The rollup gains a
  `by_pack` count per host (`carbonato@7`) and a **By pack** column on the HTML
  page, derived from `rule.pack`/`rule.version` already on every behavior event
  (new `Record.RulePack`/`RuleVersion`; SQLite gains two columns). It is the
  operator-visible half of the versioned-pack story: a pin guarantees the bytes
  a host runs, `by_pack` shows what those bytes called themselves, so pack drift
  across a fleet is visible without diffing configs. Coverage matrix brought
  current: A2 (G1) and A8 (`file.write`, G3 write half) now read closed; the
  summary is 8/10 end to end, 2 partial.
- **2026-09-30** — **G3's write half closed.** `internal/telemetry/filewatch.go`
  emits `file.write` from auditd file watches (`-w <path> -p wa`): CREATE/DELETE
  nametypes and strictly-write syscalls are writes; an open-family record counts
  only when it carries a watch key, so a bare read-open is dropped, not guessed.
  Rule `carbonato.persist.file_write` (pack v7, 20 rules) catches the persistence
  write the argv rule cannot see. Read half (eBPF) unchanged, still P2.0.
- **2026-09-29** — **R5 partial (P1.3) + P1.4 quality pass.** `scope.change`
  heuristic shim derives novel-capability signals from first-use telemetry
  (`internal/telemetry/scope.go`, labelled `detail.source=heuristic`); A10 now
  has a live (if heuristic) feed. P1.4 fixed a real dead-rule class: the argv
  matcher never tried the **space-joined command line**, so every multi-word
  glob (`*pip install*`, `*nc -l*`, `*openssl enc*`, …) was silently inert across
  5 rules — now fixed in `anyArgvMatches`. Two false-positive lookalikes
  tightened (`*.profile*`, bare `*wget*`) with a benign-corpus test. Pack v5;
  209 tests green. Phase 1 complete.
- **2026-09-28** — **R2 scoped + R2.0 shipped.** Design doc `docs/R2-EBPF-DESIGN.md`
  (eBPF additive, collector degrades not fails); `MultiSource` built and wired
  into the collector, no kernel needed. Next: R2.1 (build-tagged stub).
- **2026-09-28** — **R4 done.** G1 closed: uid→name at ingest (`uid_map`).
  Alerts now name a person, not `uid:1000`. Next: re-scope R2 (eBPF).
- **2026-09-28** — **R1 done.** G2 closed: auditd `bind`/`listen` → `net.listen`
  action, rule `carbonato.net.listener_bound` (pack v3). Matrix moves 6/10 →
  7/10 detected. Next: R4 (uid→name).
- **2026-09-28** — Coverage matrix + adapter roadmap written against the
  finished engine. 10 forced actions mapped to layers and telemetry; 6 covered
  live, 2 half-wired (rule/schema exists, no feed), 2 named gaps. Gap register
  G1–G6 with owners and priorities; roadmap R1–R6. Next concrete adapter: R4
  (uid→name) then R1 (auditd bind/listen). Blocking: repo access + NAME.

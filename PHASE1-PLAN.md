# PHASE 1 — BUILD PLAN (v1)

Phase 0 answered *what are we building and against what threats*; the
architecture is as-built (`docs/architecture.md`) and the coverage matrix
(`PHASE0-COVERAGE.md`) names every gap with an owner. This is the plan that turns
that into an ordered, testable backlog for a public v1.

It is deliberately not a wish list. Each item has an **acceptance criterion** —
the observable thing that must be true for it to count as done — and is ordered
by *value ÷ cost* with a hard rule: **nothing ships to `main` that a stranger
cannot run and verify.** The repo is public under Apache-2.0 from the first cut,
so "works on my host" is not done.

## The v1 definition of done

v1 ships when, on a fresh Linux host, a stranger can:

1. build the collector and central from source (`make` — no docs archaeology);
2. run central locally and point one collector at it;
3. reproduce **at least one end-to-end detection** from the shipped rule pack —
   the canonical CARBONATO chain (secret read → egress → …) *and* the container
   entry vector — using only artifacts in the repo;
4. read why the alert says what it says (the rule id and the fields that matched).

Everything below serves that. Items that don't are v2.

## Now (the push to the first public cut)

These close the remaining *cheap* gaps and make the repo honest enough to publish.

### P1.0 — Declare the auditd prerequisite plainly (docs) ✅ DONE
- **Why:** the single most likely first-run failure is "I ran it and no rules
  fired" because `connect` audit is off by default.
- **Done:** README gained a **Getting started** section — build, the auditd
  prerequisite as a copy-paste block *before* any command that expects
  telemetry, what silence means, and a `-dry-run`/`-once` local run.
- **Cost:** LOW. **Owner:** docs.

### P1.1 — README "known blind spots" section (closes G6) ✅ DONE
- **Why:** a public security tool that hides its blind spots is a liability.
- **Done:** README gained a **Known blind spots** section naming each uncovered
  action class with its gap id — managed/cloud agents (G6), `file.access` on a
  stock build (G3), partial `scope.change` (G5) — and calling out that the Docker
  adapter is off by default (G4 is closed *when enabled*). Checkable against the
  coverage matrix.
- **Cost:** LOW. **Owner:** docs.

### P1.2 — Reproducible end-to-end demo (the "verify it yourself" path) ✅ DONE
- **Why:** requirement 3 of the definition of done. Today a reader can read
  tests but not *see* an alert.
- **Done:** `demo/` with captured fixtures (an auditd stream and a `docker
  events` stream), a fake `docker` CLI so the daemon-side path including
  HostConfig enrichment is exercised with no Docker installed, and `run.sh` —
  one command that starts the real central, runs two real collector passes, and
  prints the alerts. Verified: **six matches**, two critical, including the
  privileged/host-mounted container (the entry vector) and, on the second pass,
  the drift → container-after-identity-change correlation. No root, no daemon,
  deterministic. Linked from the README quick-start.
- **Cost:** MEDIUM. **Owner:** collector + docs.

### P1.3 — `scope.change` shim, first slice (partially closes G5/A10) ✅ DONE
- **Why:** the last *open* gap that is adapter-shaped. "The agent changed its own
  scope (new tool, new API key)" is the CARBONATO reordered-objective signal.
  Full coverage needs an agent-native log; a first slice can derive
  `scope.change` from what we already see (a new listening socket, a new tool
  binary appearing under an agent root, a new outbound endpoint under an agent
  account) and be honest that it is a heuristic.
- **Acceptance:** at least one `scope.change` action type emitted from existing
  telemetry, matched by ≥1 rule, with a quiet-on-benign test; the doc says
  plainly which slice is covered and which still needs the agent-native log.
- **Cost:** MEDIUM. **Owner:** collector + rules.

### P1.4 — Rule pack review pass (quality, not quantity) ✅ DONE
- **Why:** 18 rules exist; a public pack earns trust by being precise. Every
  rule should have a description that explains the *why*, a severity that is
  defensible, and a test proving it fires on the attack and stays quiet on the
  benign lookalike.
- **Acceptance:** every rule in `rules/carbonato.yaml` has (a) a
  fire-test and (b) a quiet-test where a benign lookalike exists; the pack
  validates; a table in the docs lists rule → technique → the action it keys on.
- **Cost:** MEDIUM. **Owner:** rules.

## Next (scoped, not scheduled)

### P2.0 — eBPF `file.access`, kernel-dependent half (R2.2)
- **Status:** the kernel-free pieces ship (`MultiSource`, build-tagged stub,
  graceful fallback). The CO-RE object + loader do **not**.
- **Blocker:** a host with `clang` + `libbpf-dev` headers. This box has a modern
  kernel (6.12) and BTF but neither compiler nor headers.
- **Acceptance:** builds with `-tags ebpf` on a capable host; loads on a modern
  kernel; emits `file.access` for secret-path opens; degrades to a single log
  line and auditd-only on a host that cannot load it; a test proves both paths.
- **Cost:** HIGH (its own phase). **Owner:** telemetry.

### P2.1 — Durable fleet view on central
- **Why:** central ingests and presents per-alert today; a fleet view (which
  hosts are alerting, rollup by technique) is the operator's actual console.
- **Acceptance:** a central page/endpoint listing hosts with recent alerts,
  grouped by rule/technique, backed by a store interface (SQLite dev, Postgres
  prod).
- **Status:** **P2.1a DONE; P2.1b DONE; P2.1c-1 DONE (all 2026-09-29).** Durable event
  `RecordStore` (memory + atomic JSON file, retention window + cap) retained on
  both ingest paths; `GET /api/v1/fleet` rollup by host, worst-first, now with
  `host`/`since`/`min_severity` filters via a widened `Query`; `GET /` renders
  the same rollup as a plain server-rendered HTML page (no SPA); config keys
  `records`/`record_retention_days`/`record_max`. Remaining: SQLite/Postgres
  backends behind `RecordStore` — designed in
  [`docs/P2.1C-RECORD-STORE-BACKENDS.md`](docs/P2.1C-RECORD-STORE-BACKENDS.md),
  gated behind `-tags sqlite` when a fleet needs them.
- **Design:** [`docs/P2.1C-RECORD-STORE-BACKENDS.md`](docs/P2.1C-RECORD-STORE-BACKENDS.md).
- **Cost:** MEDIUM-HIGH. **Owner:** central.

## Explicitly not v1

- **Auto-remediation.** Alert-only, ever, without a separate explicit opt-in
  design. A detection tool that can *act* is a different risk class.
- **Windows/macOS collectors.** Linux first (auditd/eBPF are the chosen sources).
- **A dashboard SPA.** A plain fleet view (P2.1) is enough to be useful; polish
  is v2.

## Dependency order (the critical path)

```
P1.0 (auditd prereq docs)  ✅
P1.1 (blind spots docs)    ✅
P1.2 (demo)                ✅
P1.3 (scope shim slice)    ✅
P1.4 (rule pass)           ✅   ──► all of Phase 1 complete ──► P2.1 (fleet view)
P2.0 (eBPF) — independent, gated on toolchain; runs in parallel when a host allows.
```

### P2.1 progress

```
P2.1a durable event record + GET /api/v1/fleet   ✅  (2026-09-29)
P2.1b human page (GET /, no SPA)                 ✅  (2026-09-29)
P2.1c-1 widen RecordStore to Query(...)          ✅  (2026-09-29)
P2.1c-2 contract tests parameterised over impls  ✅  (2026-09-29)
P2.1c-3 SQLite backend (-tags sqlite)            ▶ gated, on demand
P2.1c-4 Postgres backend (pgx)                   ⏳ scoped, on demand
```

## Open decision for Dad (Rob)

One item needs a call, not a code change:

- **R2.2 toolchain:** should I `apt install clang libbpf-dev` on this host to
  unblock the kernel-dependent eBPF half, or leave it for a purpose-built test
  host? It is the only remaining strategic gap and it is gated entirely on that
  tooling. (Asking first — installing system packages is not mine to decide.)

## Log

- **2026-09-28 (Mon), ~22:45 ET** — Plan written from the coverage matrix
  (`PHASE0-COVERAGE.md`) and the as-built architecture (`docs/architecture.md`).
  Ordered as: four "Now" items to reach the public cut (auditd-prereq docs,
  blind-spots docs, reproducible demo, scope-change first slice, plus a rule-pack
  quality pass), two "Next" items scoped (eBPF kernel half, fleet view), and an
  explicit not-v1 list. One decision escalated to Rob (eBPF toolchain install).
- **2026-09-29 (Tue), ~00:45 ET** — **P1.0 and P1.1 DONE.** README gained a
  **Getting started** section (build; the auditd prerequisite as a copy-paste
  block before anything that expects telemetry, plus what silence means) and a
  **Known blind spots** section naming G6/G3/G5 and the Docker-off-by-default
  case (closes R6/G6). Critical path updated.
- **2026-09-29 (Tue), ~01:40 ET** — **P1.2 DONE.** The runnable demo (`demo/`)
  reproduces six rule matches through the real binaries with no root/daemon:
  auditd + docker-events fixtures, a fake `docker` CLI for the enriched
  container path, and `run.sh`. Second pass shows the drift → container
  after-identity-change correlation. Linked from the README. Three of four
  "Now" items are done (P1.3 remains).

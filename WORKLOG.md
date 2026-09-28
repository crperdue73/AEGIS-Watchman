# Carbonato Detection Tool — Work Log

## 2026-09-27 (Sun), ~20:20 ET — Heartbeat 1
- Read `BRIEF.md` in full.
- Wrote `PHASE0-DISCOVERY.md` — Phase 0 kickoff:
  - Enumerated 6 target environments (bare host, Docker, K8s, CI, cloud-managed, workstation).
  - Telemetry inventory per layer (L1 file hash/inotify/auditd; L2 create-our-own; L3 eBPF/auditd/conntrack + agent-native scope events).
  - First architecture sketch: per-host collector → central service, mTLS.
  - Drafted Layer-1 collector config + `agentids.event/v1` event schema (the public contract).
  - Restated the 5 decisions needed from Rob.
- **Next step (heartbeat 2):** repo skeleton. Structure, LICENSE (Apache-2.0),
  README, CONTRIBUTING, Go module scaffold for collector + central. Blocked on
  NAME (Q1) — if no answer, use placeholder `agentids` and rename later.
- **Blocking:** GitHub repo access (expected Sept 28) + name decision.

## 2026-09-27 (Sun), ~20:35 ET — Heartbeat 2
- Built the **repo skeleton** with placeholder name `agentids` (rename when Rob
  picks; Q1 still open). Go module root written, not `git init`-ed yet
  (no Go toolchain on this box to `go mod tidy` — that comes once the repo lands).
- Files created:
  - `README.md` — product framing, three layers table, architecture diagram,
    CARBONATO "no malicious byte" why-it-exists section, layout.
  - `LICENSE` — Apache-2.0, © 2026 CRPerdue Technologies, LLC.
  - `CONTRIBUTING.md` — ground rules (schema is a contract; alert-only v1;
    every rule needs a fixture; no telemetry exfil).
  - `Makefile` — build/test/vet/fmt/clean for both binaries.
  - `.gitignore` — binaries, local configs, secrets, local DBs.
  - `go.mod` — `module github.com/CRPerdue/agentids`, go 1.22.
  - `cmd/collector/main.go`, `cmd/central/main.go` — runnable scaffolds with
    flag surfaces (`-config`, `-version`) + Phase-1 TODOs.
  - `internal/schema/schema.go` — **the public contract as Go structs**: Event
    envelope, event-class + severity constants, `NewEnvelope()`.
  - `internal/collector/`, `internal/central/`, `internal/rules/` — package
    stubs with scoped TODO breadcrumbs.
  - `agentids.event.schema.json` — machine-readable JSON Schema (draft 2020-12)
    for `agentids.event/v1`.
  - `docs/event-schema.md` — envelope field table, per-layer semantics,
    versioning policy (breaking = bump version segment; additive = no bump).
  - `examples/collector.yaml`, `examples/identity.drift.json` — sample config +
    a realistic CARBONATO-shaped drift event.
  - `rules/README.md` — rule-pack home + seeded rule ideas from the brief.
  - `deploy/docker/Dockerfile` — multi-stage, distroless, both binaries.
  - `testdata/` — fixture home for Phase-1 tests.
- **Next step (heartbeat 3):** Phase-1 Layer-1 core — implement the file walker
  + sha256 hasher behind a `Hasher`/`Walker` in `internal/collector`, with the
  first unit test against a `testdata/` fixture. Then the change-diff → event
  builder. (Go toolchain may still be absent; can author code + fixtures and
  verify on the repo box.)
- **Blocking:** GitHub repo access (expected Sept 28) + NAME (Q1). Everything
  built under `agentids` placeholder; a rename is a find/replace on module path
  + docs.

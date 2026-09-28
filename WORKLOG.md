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

## 2026-09-27 (Sun), ~21:35 ET — Heartbeat 3
- **Phase 1 / Layer 1 core implemented and TESTED.** Installed Go 1.22.12 to
  `/usr/local/go` (was absent) so I could verify for real instead of asserting.
- New code in `internal/collector/`:
  - `collector.go` — `FileState`, `Inventory`, `HashFile` (sha256+size),
    `WalkRoot` (walks agent roots, matches include/exclude globs, hashes,
    records mtime), + a dependency-free globstar matcher.
  - `drift.go` — `Drift(baseline, current)` → `[]schema.Event` for
    changed/added/removed files (persona files default to `high` severity),
    and `UnifiedDiff` (tiny line-level differ, byte-truncatable).
  - `collector_test.go` + `drift_test.go` — 8 tests.
- **Verification (real, not claimed):** `go vet ./...` clean; `make build`
  produces both binaries; `go test ./...` → **8/8 PASS**.
- **Bugs caught because I compiled it:**
  1. unused `io` import in the test file (vet failure);
  2. globstar matcher had a `HasPrefix(rel, "")` bug that made **every**
     excluded pattern match **every** file — SOUL.md silently vanished from the
     inventory. Fixed with a proper prefix/tail split + suffix matching.
     (This is exactly why "it looks right" isn't verification.)
- **Next step (heartbeat 4):** `internal/central` — baseline store interface
  (SQLite dev), receive `[]Event`, persist a baseline per host+agent, and the
  HTTP ingest endpoint `POST /api/v1/events`. Then wire a 2nd `cmd/collector`
  mode that walks + diffs against a local baseline file so we have an
  end-to-end demo before the central service is fully built.
- **Blocking:** same — repo access + NAME. Placeholder `agentids` still in use.

## 2026-09-27 (Sun), ~22:35 ET — Heartbeat 4
- **Central service (Phase 1) implemented, tested, AND demoed end-to-end.**
- New code:
  - `internal/central/store.go` — `AgentKey{host,root}`, `Store` interface
    (SQLite/Postgres later), `MemoryStore` (concurrency-safe, returns copies).
  - `internal/central/service.go` — `Service.Ingest`: diffs an incoming
    inventory against the stored baseline, re-records the baseline, returns
    sorted `[]schema.Event`. First sighting = silent enrollment (no false alarm).
  - `internal/central/http.go` — `POST /api/v1/events` (1 MiB cap, validates
    host.id + body, 10 s timeout) and `GET /healthz`.
  - `internal/central/central_test.go` — 6 tests (first-seen silent, drift +
    rebaseline, path-ordered events, store copy-safety, HTTP round trip, bad input).
  - `cmd/central/main.go` — real server on `:8080` (flag `-addr`), read header timeout.
- **Verification:** `gofmt` clean, `go vet` clean, both binaries build,
  **14/14 tests pass** across collector + central.
- **Live end-to-end demo (real HTTP, not mocked):** started `agentids-central`,
  POSTed an inventory → `first_seen:true, events:[]`. Edited SOUL.md (the
  CARBONATO TTP) → POSTed again → got a `severity:high` `identity.drift` event
  with before/after sha256 (`5885b1…` → `9300fe…`), size 53→64. Identical
  resubmission → silent. **This satisfies the Phase-1 acceptance criterion in
  PHASE0-DISCOVERY.md §0.4.**
- **Still open on the demo:** the event has the diff field empty and `actor:null`
  — the collector does not yet read file contents for the unified diff, and
  auditd attribution is not wired. Those are the next two Layer-1 items.
- **Next step (heartbeat 5):** make the collector a real client — add a config
  loader for `examples/collector.yaml`, walk the roots, and POST to central on
  an interval; attach the `UnifiedDiff` content into the event before sending.
  Then a `cmd/collector -dry-run` that prints the inventory without a server.
- **Blocking:** same — repo access + NAME.

## 2026-09-27 (Sun), ~23:35 ET — Heartbeat 5
- **The collector is a real client now — full two-process system, demoed live.**
- New code:
  - `internal/collector/config.go` — YAML config loader (`gopkg.in/yaml.v3`,
    the project's first dependency) with defaults + validation (requires ≥1
    agent_root, sha256 only, sensible poll/report intervals).
  - `internal/collector/run.go` — `CollectAll`: globs each `agent_root` path
    (`/home/*/.openclaw/workspace-*` → N roots), walks each, deterministic order;
    errors if a glob matches nothing (catches misconfig).
  - `internal/collector/client.go` — HTTP `Client.Submit` → POST inventory,
    decode `IngestResult`, error on non-2xx.
  - `cmd/collector/main.go` — real binary: `-config`, `-host-id`, `-host-name`,
    `-dry-run` (prints inventory, contacts nothing), `-once` (one cycle),
    default = poll loop on `report.interval`, graceful SIGINT/SIGTERM shutdown.
    Logs enrollments and `DRIFT` lines.
  - Tests: `config_test.go` (5) + `client_test.go` (2).
- **Verification:** gofmt + vet clean; both binaries build; **21/21 tests pass.**
- **Live end-to-end with REAL binaries (collector → central over HTTP):**
  - `-dry-run` printed the inventory of both agent roots, no server contact.
  - `-once` enrolled agent-1 (2 files) + agent-2 (1 file), no false alarms.
  - Edited agent-1 `SOUL.md` (CARBONATO TTP) → `-once` logged
    `DRIFT SOUL.md identity.drift (severity=high)`.
  - Unchanged resubmission → silent.
- **Next step (heartbeat 6):** attach the unified diff — the collector should
  read changed files and populate `file.diff` before submitting (Drift currently
  leaves it empty). Add a fixture-based test asserting the diff text appears.
  Then: SQLite `Store` so baselines survive a central restart (MemoryStore is
  lost on restart — the biggest gap for a real deployment).
- **Blocking:** same — repo access + NAME.

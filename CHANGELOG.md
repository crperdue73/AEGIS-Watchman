# Changelog

All notable changes to agentids are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Versioning stance

agentids is **pre-1.0**: there are no tagged releases yet, and changes land on
`main`. When the first public cut is tagged it will appear here as `0.1.0`. The
**event wire format is versioned separately** — `agentids.event/v1` — and its
change policy lives in [`docs/event-schema.md`](docs/event-schema.md): breaking
changes bump the schema version; additive optional fields do not.

## [Unreleased]

The build so far: three detection layers, an ingest/alert central, a durable
fleet view, the tooling to run and verify it, and the docs to publish it. No
part of it acts on what it observes — **alert-only, ever**, without a separate
opt-in design.

### Added

**Layer 1 — identity (persona/skill file integrity)**
- File inventory and hashing over one or more agent roots, with drift detection
  against a durable baseline (`internal/collector`, `internal/central`).
- Unified diff attached to each `identity.drift` event (`max_diff_bytes` caps it;
  `0` = hash-only).
- Durable JSON-file baseline store, written atomically at mode `0600`, surviving
  a central restart.

**Layer 2 — provenance (is this file ours?)**
- Ed25519-signed approval manifests of `path -> sha256` per agent root, and the
  `agentids-manifest` CLI (`keygen` / `sign` / `verify`) for the operator
  workflow (`internal/provenance`, `cmd/manifest`).
- Central verification of every ingest against the signed manifest; catches a
  brand-new dropped skill (`provenance.untrusted`) that nothing has drifted from.
  The source of truth is signed, so it is not evadable by rewriting a hash.
  `manifests.require_approval` makes a manifest-less agent loud instead of silent.

**Layer 3 — behavior (what the agent does)**
- Rule format, parser, and deterministic matching engine over *actions*
  (`internal/rules`), with rules carrying an ATT&CK-style `technique` that now
  rides onto the emitted event.
- Correlation rules decided against a per-host timeline (e.g. an outbound call
  within 5m of an identity-file change).
- Telemetry adapters reading real host activity: auditd `execve` → `process.exec`,
  auditd `connect` → `net.egress`, auditd `bind`/`listen` → `net.listen`.
- A Docker **events** adapter — `container.start` on a host, catching the
  CARBONATO entry vector even when the container was created via the
  unauthenticated API on `:2375` rather than the `docker` CLI.
- A `scope.change` **heuristic shim** deriving "the agent reached for new scope"
  (new listener, new outbound endpoint, new executable under an agent root) from
  existing telemetry; `detail.source="heuristic"` marks it as a derivation.
- Seed rule pack `rules/carbonato.yaml` (v6, 19 rules) encoding the CARBONATO
  chain, with a fire-test and a quiet-test per rule.
- Durable action spool in the collector: matches are appended before they ship,
  so a central outage costs latency, never evidence.

**Central — ingest, alert, and console**
- Ingest endpoints for file inventories and behavior events, over **mutual TLS**
  on the collector→central hop (central requires and verifies every collector
  cert; TLS 1.2 minimum); `deploy/gen-certs.sh` generates the CA and leaves.
- Alerting: webhook, service log, and email (SMTP) sinks composed via
  `MultiAlerter`; batched one alert per ingest cycle; delivery failures are
  logged, never fatal.
- `uid_map`: numeric actor uids resolved to usernames at ingest (best-effort;
  the numeric id is always preserved).
- **Fleet view**: a durable, retention-bounded `RecordStore` written on every
  ingest, `GET /api/v1/fleet` (rollup by host, worst-first, with `host`/`since`/
  `min_severity` filters and a JSON response), and `GET /` rendering the same
  rollup as plain server-rendered HTML (no JS framework, no build step). One
  source of truth, two views.
- Pluggable record backends behind a single `RecordStore`/`Query` contract,
  parameterised contract tests, and a pure-Go **SQLite** backend behind
  `-tags sqlite` (no CGO). Postgres is scoped, not shipped.

**Telemetry / platform**
- `Source` interface and `MultiSource`: auditd, eBPF, and Docker sources are
  peers; a broken source is skipped, never fatal.
- eBPF `file.access`: config, a build-tagged stub, and graceful fallback to
  auditd-only. The CO-RE object + loader are **not** shipped — see Gaps.

**Tooling, demo, and docs**
- `make` build/test/vet/fmt/lint for all four binaries; one build dependency, no
  CGO.
- `agentids-rules` — Layer 3 pack tooling: `verify` runs the collector's own
  strict parser over a pack file or directory (summary + non-zero exit on any
  error), and `list` prints every rule in a set, sorted by id. It lets a rule
  author check a pack with no host, no auditd, and no daemon, and gives a
  community rules repo a `verify` to wire into CI.
- Build provenance: every binary answers `-version` with the product version, git
  commit, and build date, stamped at link time by `make build` (and `make
  version` prints the stamp). Distinct from the event wire format version.
- A reproducible end-to-end demo (`demo/run.sh`): builds, starts the real
  central, runs two real collector passes through captured fixtures with no root,
  no live auditd, and no Docker daemon, prints the alerts, then prints the fleet
  rollup.
- Docs: a one-page quickstart (build → run the demo → see an alert), as-built
  architecture and event schema, a collector deploy guide with the auditd
  prerequisite, an eBPF design note, a fleet-view operator guide, a rule-authoring
  reference (format, every predicate, glob semantics, correlation, and the full
  pack table), a collector config reference, a Phase-0 coverage matrix (every gap
  named with an owner), and a Phase-1 plan with acceptance criteria.
- `SECURITY.md` (private vulnerability disclosure + the tool's own posture),
  `CODE_OF_CONDUCT.md`, and `scripts/check-links.py` wired into `make lint`.

### Changed

- **Rule packs parse strictly.** `ParsePack` now rejects *any* unknown key at
  any level, so a misspelled predicate (`argv_anyy`) is a load error naming the
  bad key instead of a line quietly ignored. Every glob (`argv_any`,
  `path_glob_any`, `endpoint_glob_any`, `detail_glob_any`) is compiled at load
  and a **malformed pattern is a load error** rather than a silent never-match.
  `LoadPacks` rejects a rule `id` that appears in **more than one pack**, so a
  stray pack cannot ship duplicate alerts under an existing id. A silently
  dropped predicate or dead glob is the worst failure mode for a detector — the
  rule under-matches while the operator believes they are covered — and strict
  parsing also keeps a pack forward-safe, since an older binary refuses a pack
  it does not fully understand rather than running a weakened version of it. No
  shipped pack or test fixture changed.
- **An empty `behavior.rules_dir` is now a startup error.** A directory that
  exists but holds no rules used to start Layer 3 with zero rules — the host
  looked protected while nothing could fire. The collector now refuses to start
  and says so, and logs the rule/pack count it loaded. `agentids-rules verify`
  likewise refuses a path with no rules instead of reporting a vacuous `ok`.

### Known gaps (not changed: these are the open work)

- **eBPF `file.access`, kernel-dependent half** (P2.0) — gated on a host with
  `clang` + `libbpf-dev`; ships as a stub and degrades to auditd-only meanwhile.
- **Postgres record backend** (P2.1c-4) — scoped, built only when a fleet needs it.
- **Managed/cloud agents** (G6) — no agent-native log source yet, so `scope.change`
  is heuristic, not ground truth.
- **Windows/macOS collectors** — Linux first.

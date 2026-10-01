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
  auditd `connect` → `net.egress`, auditd `bind`/`listen` → `net.listen`,
  auditd file watches (`-w <path> -p wa`) → `file.write`.
- A **file-watch** adapter for the write half of file telemetry (G3): an auditd
  watch on the persistence paths emits `file.write`, so a cron drop or an rc
  edit is caught from the kernel even when the command line never names the file.
  Feeds `carbonato.persist.file_write`; needs no eBPF object — only the watch
  rules in `docs/deploy-collector.md`.
- A Docker **events** adapter — `container.start` on a host, catching the
  CARBONATO entry vector even when the container was created via the
  unauthenticated API on `:2375` rather than the `docker` CLI.
- A `scope.change` **heuristic shim** deriving "the agent reached for new scope"
  (new listener, new outbound endpoint, new executable under an agent root) from
  existing telemetry; `detail.source="heuristic"` marks it as a derivation. Its
  seen-capability set is **durable** — persisted beside the spool
  (`scope.seen`, else `<spool>.scope-seen`) and bounded — so a collector restart
  does not re-report the host's established capabilities as novel. An unreadable
  cache is discarded and rebuilt, never fatal.
- Seed rule pack `rules/carbonato.yaml` (v7, 20 rules) encoding the CARBONATO
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
- **Derived signals are marked in the fleet view.** Central copies a behavior
  event's `action.detail.source` onto the retained record as `action_source`, so
  the rollup carries a `by_source` count per host and the HTML page badges each
  affected event. An operator triaging `carbonato.tool.scope_new` can therefore
  tell that the signal rests on the heuristic shim rather than a control-plane
  fact — the unmarked-signal-vs-derivation distinction the shim promises, made
  visible where operators actually look. Empty means unmarked, not verified.
- **The fleet view shows pack provenance.** Every behavior event carries the
  pack and version that produced it, and the rollup gains a `by_pack` count per
  host (`carbonato@7`) plus a **By pack** column on the HTML page. It is the
  operator-visible half of the versioned-pack story: a pin guarantees the bytes a
  host runs, and this shows what those bytes identified themselves as, so pack
  drift across a fleet is visible without diffing configs by hand.
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
- **Rule-pack pinning** (`agentids-rules pin` / `verify --pins`): a pin file
  (`rules/packs.pins`) is a byte-exact record of the approved pack set — one
  line per pack with its `name@version` and sha256. `verify --pins` fails
  non-zero on any drift (a **changed**, **missing**, or **unpinned** pack) and
  names each one; `make pin` regenerates it; CI rejects a pack edited without
  re-pinning. The pin describes the same files a collector would load.
- **Signed pins** (`agentids-rules sign-pins`, `verify --pins --pubkey`): a pin
  can carry a detached Ed25519 signature over its exact bytes, reusing the same
  key format as Layer 2's agent manifests (`internal/provenance`). `verify
  --pubkey` checks the signature before the drift, so a fleet can trust a
  third-party pack set, not only one it built. A tampered pin, a wrong key, or a
  missing signature all fail non-zero; the public key may be given as either the
  `.pub` file or the private key (the public half is derived).
- **Collector pin enforcement** (`behavior.pins` / `behavior.pin_pubkey`): a
  collector can require a pin, so "we run the packs we approved" becomes a
  runtime guarantee rather than a CI-only claim. When `pins` is set it compares
  `rules_dir` against the pin at startup and **refuses to start Layer 3** on any
  drift, naming each off pack; `pin_pubkey` additionally requires the pin's
  detached signature to verify first. Requires `rules_dir`, and the failure mode
  matches the empty-rules-dir / missing-audit-log guards.
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
- **`scripts/ebpf-preflight.sh`** — reads this host's eBPF build/load
  prerequisites (clang, libbpf headers, kernel floor, BTF, CAP_BPF/CAP_PERFMON,
  bpffs) and prints a BUILD/LOAD verdict with nothing changed on the host. Wired
  into `make ebpf-preflight`; `--require-build`/`--require-load` give it an exit
  code for CI. Referenced from `docs/deploy-collector.md` §2 so "does this host
  qualify?" is a command, not a hand-typed probe.
- **A pinned test for the eBPF *degrade* half of P2.0** —
  `TestBehaviorEngine_EBPFUnavailableDegradesToAuditdOnly` proves that a
  collector configured with `behavior.ebpf.enabled: true` on a host that cannot
  provide the source (the default build, or an old kernel) logs one clear line
  and keeps running on auditd, rather than refusing to start; and that a disabled
  config is silent. The other half of the acceptance (the object loads and emits
  `file.access`) stays gated on the toolchain; this half runs on every host.
- **`scripts/quickstart-check.sh`** (`make quickstart-check`, and a CI job) —
  runs exactly the commands `docs/quickstart.md` tells a stranger to run: build,
  the dry-run, then the two-terminal local flow, asserting each exit code and
  that central accepts the collector's submission. The demo job guarded the demo
  path; nothing guarded the quickstart path, which is how three referenced files
  went untracked without CI noticing. A doc's copy-paste block is only true if
  it is run.
- **`scripts/container-check.sh`** (`make container-check`, and a CI job) —
  builds `deploy/docker/Dockerfile` and asserts, on the real image, every claim
  the Dockerfile header and `deploy/README.md` make: the runtime uid is 65532
  (nonroot), the baked rule pack at `/etc/agentids/rules` loads, a bind mount at
  `/etc/agentids` really does shadow that pack (the reason the doc says to mount
  at `/config`), and the copied binaries are statically linked — the exact
  property whose absence shipped the image broken once (a dynamic binary in a
  distroless, no-libc runtime). The demo and quickstart jobs need no Docker and
  so could not catch that class; this one does. A container doc that is only
  read, never built, is how the image went out broken.
- **`scripts/publish-scan.sh`** (`make publish-scan`, and a CI job) — the repo is
  public under Apache-2.0 and was built in a private workspace, so a credential,
  a real operator home path, or private infrastructure left in a tracked file
  would ship. The scan greps the tracked tree for those three classes and fails
  the build. It found and cleared the last real-username fixture paths
  (a specific operator home directory in three test files and two worklog lines,
  neutralised to `/home/agent/...`). See [`docs/PUBLISH-READINESS.md`](docs/PUBLISH-READINESS.md)
  for the checklist and the decisions that are Rob's, not code.
- **Deployment artifacts for a real host** (`deploy/`): systemd unit templates
  for the collector and central — unprivileged, hardened, with a private state
  directory for the spool and cursor — plus a `deploy/README.md` install
  walkthrough, alongside the existing `gen-certs.sh` (mutual-TLS material) and
  the container `Dockerfile`. The collector unit grants no root and no
  capabilities; the exact `CAP_BPF`/`CAP_PERFMON` delta for the optional eBPF
  adapter is documented in a commented block. `docs/deploy-collector.md` gained
  §5 "Running as a service".

### Fixed

- **The systemd install walkthrough installed a config the service could not
  start.** `deploy/README.md`'s install block copied `examples/central.yaml` and
  `examples/collector.yaml` to `/etc/agentids` and started both units — but never
  created the paths those configs demand. Central exits 1 on a missing TLS cert
  (`tls: mtls: load key pair: open /etc/agentids/leaf.crt: no such file`) and on
  a missing `uid_map` file; the collector exits 1 on a missing `behavior.rules_dir`
  (`stat /etc/agentids/rules: no such file`). So a reader following the block got
  two services that would not start, with the prerequisites only implied by the
  configs. The block now adds the four missing steps — install the rule pack, load
  the auditd rules, generate the TLS material into `/etc/agentids` (with the
  service-account permissions), and provide the `uid_map` file — and says why each
  is fatal rather than optional. Verified at the real paths: with the four steps,
  central reaches `listening on :8080` + `mutual TLS required on the ingest
  listener`, and the collector validates `rules ok — 20 rule(s)`; without them,
  both exit 1 as above. Host left exactly as found (created paths removed).
- **A fresh clone was missing the quickstart's configs and the demo's fixture.**
  `.gitignore`'s operator patterns (`*.local.yaml`, `*.log`) matched three files
  that are part of the product, not secrets or runtime logs:
  `examples/collector.local.yaml` and `examples/central.local.yaml` (the file the
  quickstart tells a stranger to run) and `demo/fixtures/audit.log` (the auditd
  stream carrying the whole CARBONATO chain). None were tracked, so on a fresh
  clone `./bin/agentids-collector -config examples/collector.local.yaml` fails
  with `no such file`, and the demo loses pass 1 — the CI `demo` job would be red.
  Added explicit `!` un-ignores beside the patterns and committed the three
  files. Verified by cloning, building, and running the quickstart and the demo
  from the clone (12 matches, 1 drift, 5 critical, as promised).
- **The demo promised a "by severity" breakdown its console never printed.**
  `demo/README.md` said `run.sh` prints the fleet rollup "with a breakdown by
  severity, by ATT&CK-style technique, by signal source, and by rule pack
  version" — but the console printer emitted technique, source, and pack and
  silently dropped severity, even though the API carries `by_severity` and the
  HTML page (`GET /`) shows it. Anyone reading the demo doc ran it and saw three
  of the four promised breakdowns. `run.sh` now prints `by severity` first (the
  data was already in the response), and the same bullet's "by signal source" is
  corrected to "by provenance" — `by_source` counts the *derived-vs-authoritative*
  marker (`heuristic`), not the telemetry source, so the old wording read as
  auditd/docker/heuristic and misled. Caught by running the demo against its own
  promise (heartbeat 75), not by re-reading it.
- **The collector deploy guide's "short version" could not start the service.**
  `docs/deploy-collector.md` §5 installed the binary and the unit but not
  `/etc/agentids/collector.yaml`, so a reader copying the snippet got a collector
  that exits 1 on `read config: open ...: no such file or directory` (the
  collector, unlike central, treats a missing config as fatal). The snippet now
  creates `/etc/agentids`, installs the config, and says to edit it first. Its
  Scope line also named auditd and eBPF but omitted the Docker events adapter,
  which has its own §3.
- **The TLS guide omitted the one thing that stops central from starting.**
  Both systemd units run as `User=agentids`, but `deploy/gen-certs.sh` writes the
  private keys `0600` owned by whoever ran it — so an operator who generated them
  as root (the normal case, writing under `/etc/agentids`) got a **fatal** startup
  failure, `tls: mtls: load key pair: open .../leaf.key: permission denied`, with
  nothing in the docs to explain it. `deploy/README.md` §TLS and
  `docs/deploy-central.md` now carry the `chown`/`chmod` step, and `gen-certs.sh`
  prints the reminder at the moment it writes the keys. Verified by running
  central as a non-root uid against root-owned keys (fails) and after handing
  them to the service user (starts).
- **The systemd install left out two of the four binaries.** `deploy/README.md`
  installed `agentids-collector` and `agentids-central` only; Layer 2 needs
  `agentids-manifest` and rule pinning needs `agentids-rules`, both referenced by
  the guides an operator reads next. The install step now covers all four, with a
  note on when they are needed.
- **The startup log lied about record retention.** With a `records` path set and
  the retention fields left to their documented defaults, central logged
  `retain=0d, max=0` — the raw config, not what the store enforces. The store has
  always resolved those to `7d`/`10000`, so the log read as *unbounded* when the
  truth was the opposite. `Config.ResolvedRecordRetention()` now exposes the
  resolved values and both the store and the startup log use it, so the line an
  operator reads is the retention they actually get. Covered by two tests.
- **The collector called an `https://` endpoint "plaintext".** With no
  `report.tls` block, the startup warning fired regardless of scheme:
  `WARNING plaintext report -> https://central:8080/...`. The transport is TLS;
  what is missing is the client identity and pinned CA, and an mTLS central will
  reject it. The warning is now scheme-aware: an `http://` endpoint still says
  "plaintext", an `https://` one says `report TLS has no client identity`.
- **The container examples shadowed the baked rule pack.** The Dockerfile and
  `deploy/README.md` showed `-v /etc/agentids:/etc/agentids:ro`, a bind mount
  that *hides* the rule pack the same image bakes at `/etc/agentids/rules` —
  Layer 3 then fails to load (`rules_dir ... no such file or directory`). Both
  now mount configs at `/config`. Found by running the documented command.

### Changed

- **The systemd path is now verified by running it, not just by `systemd-analyze
  verify`.** Both units were installed and started as the unprivileged
  `agentids` user with mutual TLS on; the collector enrolled an agent root over
  the authenticated hop, and a one-line persona edit produced `identity.drift` on
  the collector and a matching alert on central on the next poll. The host was
  returned to its prior state afterward. `deploy/README.md` records this so the
  install section reads as a proven path rather than a hopeful one. Docs-only.
- **Central has an operator guide now.** The collector has had
  `docs/deploy-collector.md` since R2.3, but the stateful half — the service with
  the baseline store, the record store, mutual TLS, Layer 2 provenance,
  `uid_map`, alerting, and retention/backend bounds — had only the annotated
  `examples/central.yaml`. Added `docs/deploy-central.md`: the two stores and why
  losing the baseline is not like losing the record store, the mTLS trust
  boundary and its fail-closed validation (a cert with no CA is refused, not
  silently downgraded), the load-time stances (Layer 2 and a misconfigured
  `uid_map` fail startup; an unknown record backend falls back with a log line),
  alert batching with non-fatal delivery, and the two retention limits. Linked
  from the README, the quickstart, and `deploy/README.md`. Docs-only — no code
  change.
- **The container path now covers mutual TLS.** The Dockerfile and
  `deploy/README.md` showed a plain-HTTP `docker run` only; the TLS story for the
  container path was untested and undocumented. A collector + central pair was
  built and run over a Docker network with mutual TLS on — enrol succeeded, the
  baseline landed in the volume, and the two failure modes were confirmed to fail
  closed (no client cert; wrong `server_name`). `deploy/README.md` gains a
  "Mutual TLS in containers" section with the verified flags and the two things
  that bite (the `nonroot` uid must be able to read the mounted certs; the mount
  path must not shadow the baked pack).
- **The container image actually works now.** `deploy/docker/Dockerfile` was
  stale ("Phase 1 work in progress"): it shipped only the collector and central,
  carried no rule pack, and — because a Go build in a host with a C compiler
  enables cgo by default — produced **dynamically linked** binaries that died on
  the distroless runtime with `exec /agentids-collector: no such file or
  directory`. It now builds all four binaries **and** the shipped pack
  (`/etc/agentids/rules`), compiles `CGO_ENABLED=0` (required by the no-libc
  base, and true to the project's no-CGO promise), pre-creates
  `/var/lib/agentids` owned by the unprivileged user so a mounted volume works,
  and adds a `.dockerignore`. Verified: the image builds; `-version` reports
  `docker`; the baked pack verifies (20 rules); a containerized collector
  `-dry-run` loads the rules; and a collector + central pair over a Docker
  network enrolls an agent end to end.
- **The "run it yourself" step now works on a fresh clone.** The example configs
  the quickstart and README named (`examples/{central,collector}.yaml`) reference
  `/etc/agentids/...` and `/var/lib/agentids/...`, so the documented commands
  failed immediately on a host without those paths. Added
  `examples/{central,collector}.local.yaml` — no system paths, no alerting, no
  TLS, stores under `demo/out` — and the docs now point at them. `-dry-run` also
  **validates the rule pack** (via `collector.CheckBehaviorRules`, the same load
  + pin-enforcement path startup uses) rather than checking config alone, so the
  most common first-run failure — a malformed, empty, or pin-mismatched pack —
  surfaces before anything looks protected; its help text and the docs were
  corrected to describe what it actually does.
- **Shipped fixtures and examples are neutral, and committed in their pre-run
  state.** The demo's stand-in persona (`demo/agent-root/SOUL.md`) is committed
  *clean* — the attacker edit is applied by `run.sh` at run time and restored
  afterwards, never baked into the repo — and the example event file and CLI
  usage now use a neutral agent (`demo-agent`, `/home/agent/...`) rather than a
  real workspace path. A regression test
  (`internal/collector/demo_assets_test.go`) fails the build if the persona is
  ever committed in its post-run state, which would both ship demo output and
  make the demo append a second attacker edit on every run. Two stale package
  TODOs (`internal/rules/rules.go`, `internal/central/central.go`) that described
  work already finished were replaced with accurate package docs — a comment
  claiming unfinished work that is done is its own small lie in a repo anyone
  can read.
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
- **A configured `behavior.audit_log` that does not exist is now a startup
  error.** With `rules_dir` set, the collector would load its rules, log
  "Layer 3 enabled", and then tail an audit log that is not there — reading
  nothing while the host looked protected. That is the same silent blind spot as
  an empty `rules_dir`, so a *set* `audit_log` now must exist. Leaving it empty is
  still legal: that is the documented "rules loaded but idle" mode for validating
  a pack with no live feed, and `audit_log` is not checked at all when `rules_dir`
  is empty (Layers 1/2 only).
- **A primary source that disappears mid-run is now loud once.** The startup
  `audit_log` check guarantees the file was there to begin with; if it is deleted
  or renamed while the collector runs, Layer 3 keeps polling, reads nothing, and
  used to go quiet about it. The collector now logs the loss of the auditd feed
  on the transition and logs again when the source returns — once each, never
  once per poll, so log rotation and a steady source stay silent. (The additive
  eBPF/Docker adapters keep their degrade-by-design behavior; this tracks only
  the primary source.)

### Known gaps (not changed: these are the open work)

- **eBPF `file.access`, kernel-dependent half** (P2.0) — gated on a host with
  `clang` + `libbpf-dev`; ships as a stub and degrades to auditd-only meanwhile.
- **Postgres record backend** (P2.1c-4) — scoped, built only when a fleet needs it.
- **Managed/cloud agents** (G6) — no agent-native log source yet, so `scope.change`
  is heuristic, not ground truth.
- **Windows/macOS collectors** — Linux first.

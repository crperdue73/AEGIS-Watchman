# Changelog

All notable changes to watchman are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Versioning stance

watchman is **pre-1.0**. `0.1.0` is the initial public import — the tree at the
first push, 2026-10-01; work since lands on `main` and is listed under
**Unreleased** below. **Published tags are never re-pointed**: a tag is a claim
about a fixed set of bytes, so moving one would break every clone, mirror, and
pin that already resolved it — the exact failure this project exists to catch.
A new cut gets a **new** version. The
**event wire format is versioned separately** — `watchman.event/v1` — and its
change policy lives in [`docs/event-schema.md`](docs/event-schema.md): breaking
changes bump the schema version; additive optional fields do not.

## [Unreleased]

The build so far: three detection layers, an ingest/alert central, a durable
fleet view, the tooling to run and verify it, and the docs to publish it. No
part of it acts on what it observes — **alert-only, ever**, without a separate
opt-in design.

### Changed
- **Renamed to Watchman** (2026-10-01). The project, module path
  (`github.com/crperdue73/AEGIS-Watchman`), the four binaries (`watchman-collector`,
  `watchman-central`, `watchman-manifest`, `watchman-rules`), the deploy unit
  files, and the event schema file (`watchman.event.schema.json`, wire version
  `watchman.event/v1`) all carry the new name. No behavior change, and nothing
  had been published under the old name — no compatibility surface.

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
  `watchman-manifest` CLI (`keygen` / `sign` / `verify`) for the operator
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
- **The identity roster — `GET /api/v1/agents`.** The fleet view rolls up
  *events*; the roster answers the other half of the brief's differentiator,
  *"every agent, its baseline"*: every agent baseline central holds, with host,
  root, and each identity file's `path`/`sha256`/`size`/`mod_time` — never
  content, so the endpoint cannot be used to read an agent's `SOUL.md` —
  filterable by `host` and `root`. It reads the durable **baseline** store, not
  the record store, so it answers "grep my fleet for every agent's identity"
  before any event has fired (Riley's agent-hygiene line). New `ListBaselines`
  on the `Store` interface, implemented by both the memory and file backends.
- **The roster joins Layer 2.** Each file in `GET /api/v1/agents` carries a
  `trust` verdict against the approved manifest for its root — `ok`, `modified`,
  `unsigned`, or `unverified` (no manifest on record) — and each row a `manifest`
  bool. One query then answers both halves of agent hygiene: *what every agent
  is* and *whether it is approved.*
- **The roster gets a page — `GET /agents`.** The identity roster now has the
  same human face the fleet view has: a plain server-rendered HTML table (no JS,
  no build step) rendered from the exact `agentsView` the JSON endpoint returns,
  so the page and the endpoint cannot disagree. Each agent block spans its files
  — path, shortened sha256 (full hash in the `title`), human-readable size, UTC
  mtime, and a colour-coded `trust` pill — with a dashed `no` badge for a root
  that has no approval manifest (a state to notice, not an alarm). It honours the
  same `host=`/`root=` filters and links to both `/api/v1/agents` and `/`.
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
- `watchman-rules` — Layer 3 pack tooling: `verify` runs the collector's own
  strict parser over a pack file or directory (summary + non-zero exit on any
  error), and `list` prints every rule in a set, sorted by id. It lets a rule
  author check a pack with no host, no auditd, and no daemon, and gives a
  community rules repo a `verify` to wire into CI.
- **Rule-pack pinning** (`watchman-rules pin` / `verify --pins`): a pin file
  (`rules/packs.pins`) is a byte-exact record of the approved pack set — one
  line per pack with its `name@version` and sha256. `verify --pins` fails
  non-zero on any drift (a **changed**, **missing**, or **unpinned** pack) and
  names each one; `make pin` regenerates it; CI rejects a pack edited without
  re-pinning. The pin describes the same files a collector would load.
- **Signed pins** (`watchman-rules sign-pins`, `verify --pins --pubkey`): a pin
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
  (nonroot), the baked rule pack at `/etc/watchman/rules` loads, a bind mount at
  `/etc/watchman` really does shadow that pack (the reason the doc says to mount
  at `/config`), and the copied binaries are statically linked — the exact
  property whose absence shipped the image broken once (a dynamic binary in a
  distroless, no-libc runtime). The demo and quickstart jobs need no Docker and
  so could not catch that class; this one does. A container doc that is only
  read, never built, is how the image went out broken.
- **`scripts/publish-scan.sh`** (`make publish-scan`, and a CI job) — the repo is
  public under Apache-2.0 and was built in a private workspace, so a credential,
  a real operator home path, or private infrastructure left in a tracked file
  would ship. The scan greps **both the tracked tree and the commits a push
  would newly publish** (`@{upstream}..HEAD`) for those three classes and fails
  the build. The history half exists because the tree is not the whole of what
  ships: `git push` publishes commits, a `git rm`-ed file stays retrievable from
  any clone, and the earlier tree-only pass had neutralised the operator home
  paths (`/home/agent/...` in three test files and two worklog lines) while the
  real values stayed in the commits — a clean tree over a dirty history. A
  second, informational scope is **`make publish-audit`**
  (`scripts/publish-scan.sh --public-audit`): the whole reachable history,
  reported but not fatal, because content already on a pushed branch cannot be
  un-published by a local fix — remediation (a history rewrite before it is
  public, or accepting the exposure) is a human call, not a build failure. See
  [`docs/PUBLISH-READINESS.md`](docs/PUBLISH-READINESS.md) for the checklist and
  the decisions that are Rob's, not code.
- **`scripts/publish-scan-check.sh`** (`make publish-scan-check`, wired into
  preflight and the CI `publish` job) — exercises the history scope in a
  throwaway repo: a secret committed *and then removed* must still fail the
  gate, an absent upstream must be tolerated, and already-public content must
  warn rather than fail. It exists so the history coverage cannot silently
  regress — disabling the history scope makes its removed-secret case pass, and
  the check then fails on it.
- **`scripts/cut-release.sh`** (`--go <version>`) — the cut,
  as a script rather than a paragraph: it enforces the tag rules and refuses on a
  dirty tree, a version that already exists, or a failed preflight, then pushes
  `main` and tags the tip it just pushed, so the tag and the pushed commit are
  the same bytes by construction. **`scripts/cut-release-check.sh`**
  (`make cut-release-check`, run in preflight and the CI `publish` job) runs a
  clean cut *and fires every refusal* end to end in a throwaway clone, so a broken
  refusal cannot wait until a real cut to reveal itself. See [`docs/PUBLISH-READINESS.md`](docs/PUBLISH-READINESS.md).
- **`scripts/preflight.sh`** (`make preflight`) — runs every CI job's local
  equivalent, in order, stopping at the first failure, so "push-ready" is one
  verdict instead of eight claims made from memory. Its last step is a
  **fresh-clone check** (`scripts/clone-check.sh`): CI runs on a clean
  checkout, so this verifies the committed tree from a fresh clone — the failure
  a working-tree build cannot see. It also runs the release-tooling self-check
  (`make cut-release-check`), so the script that performs the cut is exercised by
  the very verdict that gates it. A companion **`scripts/ci-parity-check.sh`**
  (`make ci-parity-check`, in `lint`) asserts preflight still reaches every
  command CI runs, so the two cannot drift apart (the class that bit once, when
  the rule-pack pin went red on the first CI run while every local claim was
  individually true).
- **Fuzz targets for the untrusted-input parsers and the matcher**
  (`internal/telemetry/fuzz_test.go`, `internal/rules/fuzz_test.go`;
  `make fuzz` via `scripts/fuzz.sh`, and a CI job). Every stream this tool reads
  is attacker-influenced — auditd `argv`, `docker events` JSON off a socket, a
  community rule pack — so a parser or matcher that panics on crafted input is a
  denial of service against the *detector*. The targets assert only that these
  return on hostile input, never panic. The seed corpus runs under an ordinary
  `go test`; a live fuzz found no crash across ~5M executions, and any future
  crash writes a reproducer to `testdata/fuzz/` that `make test` re-checks.
- **`scripts/deps-report.sh`** (`make deps-report`, and a CI job) — lists every
  module compiled into the binaries (both build modes), with its version and
  license read from the module cache, and fails on a copyleft or unrecognised
  license. A security tool shipping under Apache-2.0 must not link a copyleft
  dependency, and that risk only ever arrives through a transitive upgrade. The
  compiled set is small — nine modules, all MIT/BSD/Apache — and the check reads
  only what is built, not the wider module graph (which includes test-only
  requirements that are never distributed).
- **Deployment artifacts for a real host** (`deploy/`): systemd unit templates
  for the collector and central — unprivileged, hardened, with a private state
  directory for the spool and cursor — plus a `deploy/README.md` install
  walkthrough, alongside the existing `gen-certs.sh` (mutual-TLS material) and
  the container `Dockerfile`. The collector unit grants no root and no
  capabilities; the exact `CAP_BPF`/`CAP_PERFMON` delta for the optional eBPF
  adapter is documented in a commented block. `docs/deploy-collector.md` gained
  §5 "Running as a service".

### Docs

- **Repo-recovery runbook** ([`docs/RECOVERY.md`](docs/RECOVERY.md)). The procedure
  for the case this project has now actually hit: the working tree survives but `.git`
  does not. It says to reconcile **to** the public baseline by SHA (never a
  post-incident "N commits ahead" memory), to let `git status` — not a file listing —
  decide the delta (an ignored file is not work to replay), to verify with the non-git
  guards before replaying, and to treat replay-vs-squash as an owner decision because
  it changes public history. Linked from the README's Maintenance note.

- **README figure set** (`assets/*.jpg`). Seven chapter-break graphics across the
  why / layers / deployment / rules / console / blind-spots sections. The plates
  are generated **text-free**, and every label is composited from a single
  grounded source (`graphics/watchman/LABELS.md`), so no fabricated metadata can
  reach the page — the failure mode (a baked-in `confidence:` field, invented
  rule ids) that kept the first plate set out of the repo. Palette and wording
  traceable to `internal/schema/`, `rules/carbonato.yaml`, and this README.

### Fixed

- **The systemd install walkthrough installed a config the service could not
  start.** `deploy/README.md`'s install block copied `examples/central.yaml` and
  `examples/collector.yaml` to `/etc/watchman` and started both units — but never
  created the paths those configs demand. Central exits 1 on a missing TLS cert
  (`tls: mtls: load key pair: open /etc/watchman/leaf.crt: no such file`) and on
  a missing `uid_map` file; the collector exits 1 on a missing `behavior.rules_dir`
  (`stat /etc/watchman/rules: no such file`). So a reader following the block got
  two services that would not start, with the prerequisites only implied by the
  configs. The block now adds the four missing steps — install the rule pack, load
  the auditd rules, generate the TLS material into `/etc/watchman` (with the
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
  clone `./bin/watchman-collector -config examples/collector.local.yaml` fails
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
  `/etc/watchman/collector.yaml`, so a reader copying the snippet got a collector
  that exits 1 on `read config: open ...: no such file or directory` (the
  collector, unlike central, treats a missing config as fatal). The snippet now
  creates `/etc/watchman`, installs the config, and says to edit it first. Its
  Scope line also named auditd and eBPF but omitted the Docker events adapter,
  which has its own §3.
- **The TLS guide omitted the one thing that stops central from starting.**
  Both systemd units run as `User=watchman`, but `deploy/gen-certs.sh` writes the
  private keys `0600` owned by whoever ran it — so an operator who generated them
  as root (the normal case, writing under `/etc/watchman`) got a **fatal** startup
  failure, `tls: mtls: load key pair: open .../leaf.key: permission denied`, with
  nothing in the docs to explain it. `deploy/README.md` §TLS and
  `docs/deploy-central.md` now carry the `chown`/`chmod` step, and `gen-certs.sh`
  prints the reminder at the moment it writes the keys. Verified by running
  central as a non-root uid against root-owned keys (fails) and after handing
  them to the service user (starts).
- **The systemd install left out two of the four binaries.** `deploy/README.md`
  installed `watchman-collector` and `watchman-central` only; Layer 2 needs
  `watchman-manifest` and rule pinning needs `watchman-rules`, both referenced by
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
  `deploy/README.md` showed `-v /etc/watchman:/etc/watchman:ro`, a bind mount
  that *hides* the rule pack the same image bakes at `/etc/watchman/rules` —
  Layer 3 then fails to load (`rules_dir ... no such file or directory`). Both
  now mount configs at `/config`. Found by running the documented command.

### Changed

- **The systemd path is now verified by running it, not just by `systemd-analyze
  verify`.** Both units were installed and started as the unprivileged
  `watchman` user with mutual TLS on; the collector enrolled an agent root over
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
  the distroless runtime with `exec /watchman-collector: no such file or
  directory`. It now builds all four binaries **and** the shipped pack
  (`/etc/watchman/rules`), compiles `CGO_ENABLED=0` (required by the no-libc
  base, and true to the project's no-CGO promise), pre-creates
  `/var/lib/watchman` owned by the unprivileged user so a mounted volume works,
  and adds a `.dockerignore`. Verified: the image builds; `-version` reports
  `docker`; the baked pack verifies (20 rules); a containerized collector
  `-dry-run` loads the rules; and a collector + central pair over a Docker
  network enrolls an agent end to end.
- **The "run it yourself" step now works on a fresh clone.** The example configs
  the quickstart and README named (`examples/{central,collector}.yaml`) reference
  `/etc/watchman/...` and `/var/lib/watchman/...`, so the documented commands
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
  and says so, and logs the rule/pack count it loaded. `watchman-rules verify`
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

## [0.1.0] — 2026-10-01

Initial public import. The three detection layers — **identity drift** (content
hashes and unified diffs), **provenance** (Ed25519-signed approval manifests),
and **behavior rules** over real host telemetry — plus the central service with
its `RecordStore` fleet view and agent roster, the `watchman-manifest` /
`watchman-rules` operator tooling, and the docs (quickstart, architecture,
rules, deploy, event schema). The **event wire format** ships as
`watchman.event/v1`. The `[Unreleased]` section above is the build narrative as
written before this tag; it describes the same tree and the work landed since,
and is not yet split by release.

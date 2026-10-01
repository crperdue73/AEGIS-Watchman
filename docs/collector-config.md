# Collector configuration reference

One collector runs per host. It is configured by a single YAML file
(`collector.yaml`), passed with `-config`; a missing file means defaults, but a
collector with no `agent_roots` is a load error. The fully-commented sample is
[`examples/collector.yaml`](../examples/collector.yaml); this page is the
exhaustive key reference.

> Source of truth: [`internal/collector/config.go`](../internal/collector/config.go)
> and the telemetry blocks it embeds (`internal/telemetry/`).

## Top level

| Key | Type | Required | Meaning |
|---|---|---|---|
| `agent_roots` | list | ✅ | One or more globbed agent workspaces to inventory (Layer 1). |
| `hash` | string | — | Hash algorithm. Only `sha256` (the default). |
| `watch` | mapping | — | How file changes are noticed. |
| `report` | mapping | — | How inventories and matches reach central. |
| `attribution` | mapping | — | Optional "who changed it" enrichment. |
| `behavior` | mapping | — | Layer 3 rule evaluation. Omit → Layers 1/2 only. |

## `agent_roots`

A list of workspaces. Each entry globs a `path` and filters with `include` /
`exclude` patterns (shell-style, `**` for recursive).

```yaml
agent_roots:
  - path: /home/*/.openclaw/workspace-*
    include: [SOUL.md, IDENTITY.md, MEMORY.md, skills/**/*.md]
    exclude: ["**/*.tmp", "**/.git/**"]
```

| Field | Required | Meaning |
|---|---|---|
| `path` | ✅ | Glob for the workspace root (e.g. `workspace-*`). |
| `include` | — | Patterns to keep. Empty → everything not excluded. |
| `exclude` | — | Patterns to drop. Applied after `include`. |

At least one entry is required, and each `path` must be non-empty.

## `hash`

`sha256` (default). Any other value is a load error — the format exists so the
choice is explicit and a future algorithm is a deliberate change, not an
accident.

## `watch`

| Key | Default | Meaning |
|---|---|---|
| `mode` | `poll` | `inotify` (cheap, Linux) or `poll` (fallback for non-Linux / restricted hosts). |
| `poll_interval` | `60s` | Poll cadence; only meaningful in `poll` mode. A non-positive value resets to `60s`. |

## `report`

| Key | Default | Meaning |
|---|---|---|
| `endpoint` | — | Central ingest URL, e.g. `https://central.example/api/v1/events`. Layer 3 matches go to the sibling `/actions` path. |
| `interval` | `30s` | Reporting cadence. Non-positive resets to `30s`. |
| `tls` | — | Mutual TLS to central (see below). Absent → plain HTTP, fine for loopback dev, **never** for a real fleet. |

`tls` takes `cert`, `key`, `ca`, and `server_name` (must match the certificate
SAN). Generate material with `deploy/gen-certs.sh`. With `tls` set, the
collector requires and verifies central's certificate; central requires and
verifies the collector's.

## `attribution`

| Key | Meaning |
|---|---|
| `source` | `auditd` enables "who changed it" via the audit log; empty (the default) leaves drift detected but unattributed. |

## `behavior` — Layer 3

Omit the block, or leave `rules_dir` empty, to run Layers 1/2 only.

| Key | Default | Meaning |
|---|---|---|
| `rules_dir` | — | Directory of `*.yaml` rule packs, loaded **sorted by name** for determinism. Must exist if set, and must contain **at least one rule** — a dir that exists but is empty is a startup error, not a silent no-op, so a mis-pointed or emptied dir cannot leave Layer 3 looking enabled while it detects nothing. |
| `audit_log` | — | auditd log to tail for `process.exec` / `net.egress` / `net.listen` / `file.write`. Empty → rules load but stay idle (useful for validating a pack with no live feed). If **set**, the path must exist — a named-but-absent log is a startup error, so a mis-pointed path cannot leave Layer 3 looking enabled while it reads nothing. If it later **disappears at runtime** (a delete or rename, a dropped mount, auditd stopped and its log rotated away), the collector keeps running but logs the loss of the auditd feed **once**, and once again when it returns — never every poll. |
| `spool` | — | File where matches are queued **before** shipping, so a central outage costs latency, not evidence. Empty → best-effort ship, dropped on failure. |
| `spool_max_bytes` | `16777216` (16 MiB) | Bound on the spool; oldest entries are dropped past it. `0` → the default. |
| `cursor` | — | Persisted audit-log read offset, so a restart resumes instead of replaying the log. Empty → derived as `<spool>.cursor` when a spool is set, else not persisted. |
| `pins` | — | Path to a rule-pack **pin file** (see [rules.md § Publishing and pinning](rules.md#publishing-and-pinning-a-pack-set)). When set, the collector checks `rules_dir` against it at startup and **refuses to start Layer 3 on any drift** — a changed, missing, or unpinned pack. Requires `rules_dir`. |
| `pin_pubkey` | — | Ed25519 public key file. With `pins`, the pin's detached signature (`<pins>.sig`) must verify **before** the pin is trusted, so a pin fetched from elsewhere is authenticated, not merely read. Requires `pins`. |

### `behavior.ebpf`

The optional kernel-side `file.access` adapter (the **read** half of G3; the
write half — `file.write` — comes from auditd file watches and needs no kernel
object, see [deploy-collector.md](deploy-collector.md) §1). **Additive to
`audit_log`, never a replacement** — it catches a secret read that never names
the path on the command line (a `python -c` wrapper), which argv rules cannot.
Needs a modern kernel + `CAP_BPF` and a binary built with `-tags ebpf`.

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | Turn the adapter on. Even then, a build without the tag or a host that cannot load the object degrades to auditd-only with one log line. |
| `object_path` | *(empty)* | Compiled CO-RE object to load. Empty → the object embedded in the binary. |
| `watch_paths` | *(secret-store defaults)* | Path **prefixes** the kernel-side filter keeps; only opens under these reach userspace. Empty → `/.ssh/`, `/.aws/`, `/.config/gcloud/`, `/.azure/`, `/.kube/`, `/.openclaw/`, `/etc/shadow`. Add your own key directories. |
| `ringbuf_bytes` | `1048576` (1 MiB) | Per-CPU ring buffer size. Must be ≥ 0. |

See [`R2-EBPF-DESIGN.md`](R2-EBPF-DESIGN.md) for the design and
[`deploy-collector.md` §2](deploy-collector.md) for the deploy path.

### `behavior.docker`

The optional container-runtime adapter (the CARBONATO entry vector). Like eBPF it
is additive and degrades: a host with no Docker daemon simply has nothing to
report. It reads `docker events` and emits `container.start` — catching a
privileged / host-mounted container even when it was created straight against the
Docker API on `:2375`, where no `docker` command was ever typed.

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | The daemon must be reachable; the operator opts in. |
| `bin` | `"docker"` | The docker binary to run. |
| `events_args` | `[events --format {{json .}}]` | Override the arguments to `bin` (tests / non-standard runtimes). |
| `inspect` | `false` | Enrich `container.start` with `HostConfig` (privileged, host PID/net, host-root mounts) via `docker inspect`. Off → events still fire with identity only. The sample config turns this on. |

See [`deploy-collector.md` §3](deploy-collector.md).

### `behavior.scope`

The `scope.change` heuristic shim (partial coverage of the "agent reached for new
scope" signal). It is a **derivation**, not a source: it wraps the composed
stream and infers novelty from the actions already emitted, marking its output
`detail.source=heuristic`.

| Key | Default | Meaning |
|---|---|---|
| `enabled` | on when Layer 3 is on | Tri-state. Unset → enabled exactly when `rules_dir` is set. `true` / `false` overrides. |
| `seen` | derived | File where the seen-capability set is persisted, so a **collector restart does not re-report the host's established capabilities as novel**. Empty → derived as `<spool>.scope-seen` when a spool is set, else in-memory only (reset on restart). Bounded (`DefaultScopeSeenMax`); an unreadable file is discarded and rebuilt, never fatal. |

## Validation

`LoadConfig` fails fast rather than run misconfigured. It rejects: no
`agent_roots`; an `agent_roots` entry with an empty `path`; an `hash` other than
`sha256`; a `rules_dir` that does not exist or holds no rules; a `behavior.audit_log`
that is set but does not exist; a `behavior.pins` without a `rules_dir` or whose
file does not exist; a `behavior.pin_pubkey` without `pins`; a `tls` block that does not validate;
and a `behavior.ebpf` / `behavior.docker` block with contradictions (checked even
when the adapter is disabled, so a typo is caught at startup, not at first poll).

The `audit_log` check only applies when `rules_dir` is set: with no `rules_dir`
the collector runs Layers 1/2 only and `audit_log` is not evaluated, so it is
neither required nor checked.

**One caveat:** the loader does **not** reject unknown keys — a mistyped
top-level key is ignored, not flagged. Check spelling against this page when a
setting seems to have no effect.

## See also

- [`deploy-collector.md`](deploy-collector.md) — auditd rules, eBPF/Docker
  requirements, degradation, permissions.
- [`rules.md`](rules.md) — writing the rule packs that `behavior.rules_dir` loads.
- [`architecture.md`](architecture.md) — how the collector's sources compose.

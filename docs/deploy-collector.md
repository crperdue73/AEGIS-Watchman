# DEPLOY — COLLECTOR (LAYER 3 TELEMETRY)

**Audience:** the operator standing up an `agentids` collector on a host.
**Scope:** enabling the Layer 3 telemetry sources — auditd (every host), the
optional eBPF `file.access` adapter (modern kernels), and the Docker events
adapter (hosts running a daemon). Layers 1/2 need none of this; see the README.

> For the **every-key reference** to `collector.yaml` (all fields, defaults, and
> what the loader rejects), see [`collector-config.md`](collector-config.md).
> This page is the *why* and the *deploy*; that one is the *what*.

This document exists so an operator can answer one question before they grant
anything: **what does this collector need from my kernel, and what happens if it
doesn't get it?**

The answer, up front: **it degrades, it never fails to start.** A collector with
no telemetry source is worse at its job but still runs, still checks identity
files, still checks provenance. Nothing here is a hard dependency.

---

## 1. auditd — the baseline source (all hosts)

Layer 3 rules match *actions*: process execution, outbound connections,
listeners. Those come from auditd. Enable the rules the collector can use:

```
# /etc/audit/rules.d/agentids.rules
-a always,exit -F arch=b64 -S execve   -k agentids
-a always,exit -F arch=b64 -S connect  -k agentids
-a always,exit -F arch=b64 -S bind     -k agentids
-a always,exit -F arch=b64 -S listen   -k agentids
# 32-bit variants too on a mixed-arch host:
-a always,exit -F arch=b32 -S execve   -k agentids
-a always,exit -F arch=b32 -S connect  -k agentids
-a always,exit -F arch=b32 -S bind     -k agentids
-a always,exit -F arch=b32 -S listen   -k agentids

# File watches (optional) — the WRITE half of file telemetry. A write to a
# persistence path is caught even when the command line never names the file
# (a script, a wrapped interpreter). Watch with -p wa (write + attribute
# change); do NOT add -p r to the same watch — an open is read/write-ambiguous
# and a read-watch would report reads as writes.
-w /etc/cron.d             -p wa -k agentids
-w /etc/cron.daily         -p wa -k agentids
-w /etc/cron.hourly        -p wa -k agentids
-w /etc/crontab            -p wa -k agentids
-w /etc/systemd/system     -p wa -k agentids
-w /root/.bashrc           -p wa -k agentids
-w /root/.profile          -p wa -k agentids
-w /root/.ssh/authorized_keys -p wa -k agentids
```

Apply with `augenrules --load` (or `service auditd restart`).

Then point the collector at the log:

```yaml
behavior:
  rules_dir: /etc/agentids/rules
  audit_log: /var/log/audit/audit.log
```

**What each rule buys you:**

| Rule | Action emitted | Rules it feeds |
|---|---|---|
| `execve` | `process.exec` | shell spawn, secret reads via argv, installer, obfuscation, persistence |
| `connect` | `net.egress` | chat/C2 egress, and the correlation `egress_after_identity_change` |
| `bind` / `listen` | `net.listen` | `carbonato.net.listener_bound` — a foothold opening a socket |
| `-w <path> -p wa` | `file.write` | `carbonato.persist.file_write` — a write to a persistence path, named by the kernel, not by argv |

Without `connect`, `egress_after_identity_change` — the rule this project was
built for — cannot fire. Without `bind`/`listen`, the kernel-side listener rule
stays idle. `execve` alone is the minimum; all four is the intended set.

**The file watches are the exception worth adding deliberately.** `execve`
reports the command line, so a persistence write is only seen when the command
names the file (`carbonato.persist.cron_or_rc`). A `python -c` or a script that
drops a cron entry without naming it walks past that. The kernel, however,
always knows which file was written — so a watch on the persistence paths turns
that into `file.write` and fires `carbonato.persist.file_write`. It is the write
half of coverage gap G3; the read half still needs the eBPF source (§2). Watch
only the paths that matter (a `-w` on `/` is a flood); the set above is the
minimum that covers the common persistence locations.

**Permissions:** reading `/var/log/audit/audit.log` needs membership in the
`adm`/`systemd-journal` group (varies by distro) or root. The collector does not
need root otherwise. Prefer the group.

**Cost note:** `execve` on a busy host is a lot of events. auditd's own rate
limiting (`-r`) and the collector's rule pack keep this bounded; the collector
reads only what it needs and does not ship raw auditd lines anywhere.

---

## 2. eBPF `file.access` — the optional source (modern kernels)

### Why you'd want it
The auditd `execve` source catches a secret read **only when the path is in the
command line** — `cat ~/.ssh/id_ed25519`. One `python -c "open(...)"` wrapper
walks past it. eBPF reports the file that was actually *opened*, whatever the
argv said. That closes the evasion on the rule this threat class turns on.

### What it needs
| Requirement | Why | How to check |
|---|---|---|
| Kernel ≥ 5.8 | ring buffer + CO-RE | `uname -r` |
| BTF | CO-RE type info | `ls /sys/kernel/btf/vmlinux` |
| `CAP_BPF` + `CAP_PERFMON` | load + attach probes | `capsh --print` |
| a binary built with `-tags ebpf` | the object + loader are compiled in | `go build -tags ebpf ./cmd/collector` |
| `clang` + libbpf headers | only to *build* the object | build host only, not the fleet |

You do not have to check those by hand. `scripts/ebpf-preflight.sh` (or
`make ebpf-preflight`) reads them all and prints a BUILD / LOAD verdict, and it
changes nothing on the host:

```
$ scripts/ebpf-preflight.sh
...
  -> BUILD possible          NO
  -> LOAD possible           YES
verdict: LOAD ready; BUILD needs clang + libbpf headers
```

Use `--require-build` or `--require-load` in a script or CI step to fail when a
capability you depend on is absent (exit 1) instead of parsing the output. The
same command on the build host is how you confirm a `-tags ebpf` build has what
it needs.

**It is additive.** eBPF rides alongside auditd; it never replaces it. A host
that cannot load it runs auditd-only and logs one line saying so.

### Build
The default `go build ./...` needs **no** clang and no libbpf — the eBPF source
is behind the `ebpf` build tag. To include it:

```
go build -tags ebpf -o bin/agentids-collector ./cmd/collector
```

(Until R2.2 lands the object + loader, an `-tags ebpf` build compiles and
reports "eBPF source not yet implemented" at runtime — it will not pretend to
work. Watch `docs/R2-EBPF-DESIGN.md` for the state.)

### Configure

```yaml
behavior:
  ebpf:
    enabled: true
    object_path: ""            # empty = the object embedded in the binary
    watch_paths: []            # empty = built-in secret-store defaults
    ringbuf_bytes: 0           # 0 = 1 MiB per CPU
```

**`watch_paths` matters.** The kernel-side filter keeps only opens under these
prefixes — a host opens thousands of files a second, and shipping them all is a
self-inflicted flood and a privacy problem. Empty uses the defaults:

```
/.ssh/  /.aws/  /.config/gcloud/  /.azure/  /.kube/  /.openclaw/  /etc/shadow
```

Add your own key directories. A wrong prefix is worse than none — the defaults
are the conservative, universal ones.

### What "degrades" looks like
One log line at startup, and the collector keeps running:

```
collector: behavior: eBPF file.access adapter unavailable, running auditd-only: telemetry: eBPF support not compiled in (build with -tags ebpf)
```

That is the expected outcome on a stock build or an old kernel. It is not an
error condition; it is the design (D1, D6 in the design doc). `file.access`
rules simply do not fire on that host.

### Permissions
Grant `CAP_BPF` and `CAP_PERFMON`, plus a mounted bpffs. Avoid granting
`CAP_SYS_ADMIN` wholesale — the design deliberately does not need it.

---

## 3. Docker events — the container-runtime source (hosts running Docker)

### Why you'd want it

This is the only source that sees the CARBONATO **entry vector**: a container
appearing on the host with host-level access. Every other source watches what an
agent does *inside* the box; this one watches a new workload *arrive*. Catching
the intrusion at container-launch is catching it before any agent runs.

Crucially, it reads `docker events` — emitted by the **daemon**, not the shell.
So it fires whether the container was created with `docker run` in a terminal or
by a script talking straight to an unauthenticated Docker API on `:2375`
(the real attack) where no `docker` command was ever typed. The argv-based rule
alone would miss the second case entirely.

### What it needs

Just the host's `docker` binary and a running daemon the collector can reach.
No kernel work, no capabilities. The collector's user must be able to talk to
the daemon socket (be in the `docker` group, or point at a socket it can read).

### Configure

```yaml
behavior:
  docker:
    enabled: true
    inspect: true        # enrich each event with the container's HostConfig
```

`inspect: true` runs `docker inspect --format '{{json .HostConfig}}'` per event
so the alert names the container's actual `privileged` / host-PID / host-network
flags and its bind mounts. It is a nice-to-have, not load-bearing — see below.

### What it detects

Three `container.start` rules in `rules/carbonato.yaml` (all critical):

- `carbonato.container.privileged_start` — any host-access flag: privileged,
  host PID namespace, or host network.
- `carbonato.container.host_root_mount` — a bind of `/:/host` or the Docker
  socket itself (`/var/run/docker.sock`).
- `carbonato.container.after_identity_change` — a dangerous container within 10m
  of an agent identity-file change: the full timed spine of the intrusion.

An ordinary container (no host access, ordinary mounts) fires **nothing** — the
rules are built to stay quiet unless it is actually a risk.

### What "degrades" looks like

Two independent failure modes, both non-fatal:

- **No Docker daemon / no binary** → one log line at startup, collector runs on
  its other sources:
  ```
  collector: behavior: docker events adapter unavailable, continuing without it: ...
  ```
- **Daemon unreachable for enrichment** (`inspect` on, but the socket is gone) →
  the event **still fires**, just with identity only (image + name), no risk
  flags. A container you cannot inspect is still a container that appeared —
  report it, never drop it. This is the whole point of a detection tool.

### Permissions

No capabilities needed. Access is the Docker socket. If the collector must reach
another host's daemon, treat that socket as the credential it is — a readable
Docker socket is root-equivalent on that host.

---

## 4. The `uid_map` — readable alerts (central, not collector)

The collector reports a numeric uid; a name is a presentation concern and lives
on central. Point central at a passwd-style file:

```yaml
# central.yaml
uid_map: /etc/agentids/passwd
```

Copy `/etc/passwd` from the fleet's base image. An alert then reads
`user=deploy uid=1000` instead of `uid:1000`. Best-effort: an unknown uid stays
numeric — a wrong name is worse than no name — and the numeric id is always
kept alongside the name.

---

## 5. Running as a service

The [quickstart](quickstart.md) runs the collector once. To run it persistently,
[`deploy/`](../deploy/README.md) ships **systemd unit templates** for both the
collector and central — unprivileged, hardened, with a private state directory
for the spool and cursor. The short version:

```sh
sudo useradd --system --no-create-home --shell /usr/sbin/nologin agentids
sudo usermod -aG adm agentids          # read /var/log/audit/audit.log (systemd-journal on RHEL)
sudo install -m 0755 bin/agentids-collector /usr/local/bin/
sudo install -d -m 0750 -o agentids -g agentids /etc/agentids
sudo install -m 0640 -o agentids -g agentids examples/collector.yaml /etc/agentids/collector.yaml
sudo install -m 0644 deploy/agentids-collector.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now agentids-collector
```

**Edit `/etc/agentids/collector.yaml` before enabling the unit** — set your
`agent_roots`, and the `behavior` block if you turned on Layer 3. The collector
**fails to start** if the config file is missing (`read config: open
/etc/agentids/collector.yaml: no such file or directory`) — unlike central, whose
missing config falls back to defaults. `/var/lib/agentids` (the spool + cursor)
is created by the unit's `StateDirectory=`, so you do not make it by hand.

The `agentids` user needs read access to the audit log and to each `agent_roots`
path — and nothing else. The full walkthrough, the capability deltas for the
eBPF and Docker adapters, and the mutual-TLS step are in
[`deploy/README.md`](../deploy/README.md).

---

## 6. Sanity checklist

After deploying a collector:

- [ ] `agentids-collector -once -dry-run` exits clean (config parses, roots
      exist, rules load).
- [ ] Editing an agent's `SOUL.md` produces an `identity.drift` event (Layer 1).
- [ ] A `bash -c "cat ~/.ssh/id_ed25519"` in the audit log fires
      `carbonato.secret.read_order_follows_constraint` (Layer 3, argv path).
- [ ] If file watches are enabled: a write to a watched persistence path fires
      `carbonato.persist.file_write` (Layer 3, the path from the kernel, no argv).
- [ ] If eBPF is enabled: startup logs either "adapter attached" or exactly one
      "unavailable" line — never a crash.
- [ ] If Docker is enabled: starting an ordinary container fires nothing;
      `docker run --privileged alpine true` fires
      `carbonato.container.privileged_start`.
- [ ] Central logs alerts with a **name**, not `uid:N` (if `uid_map` is set).

## Log
- **2026-09-30** — **Re-read as the operator (heartbeat 71); two fixes.** (1) §5's
  "short version" installed the binary and the unit but **not** the config, so a
  reader copying it got a collector that fails to start (`read config: ... no such
  file or directory` — fatal, exit 1, unlike central, whose missing config falls
  back to defaults). The snippet now creates `/etc/agentids`, installs
  `collector.yaml`, and says to edit it before enabling the unit. (2) The Scope
  line named auditd and eBPF but not the Docker events adapter, which has its own
  §3. Every other claim was checked against the tree and held: the action-name
  table, the two adapter degrade strings, the three `container.start` rule ids,
  the `-tags ebpf` build (compiles; the stub reports the documented
  "not yet implemented"), and the §6 checklist rule ids all exist in
  `rules/carbonato.yaml`.
- **2026-09-30** — **§5 "Running as a service" added.** The quickstart runs the
  collector once; nothing showed an operator how to run it persistently. Added
  systemd unit templates (`deploy/agentids-{collector,central}.service`) and a
  `deploy/README.md` install walkthrough (unprivileged `agentids` user, the
  exact read access it needs, the capability deltas for eBPF/Docker, TLS). Both
  units verified with `systemd-analyze verify` (clean apart from the expected
  "binary not yet installed"). The collector unit needs no root and no
  capabilities by default.
- **2026-09-28** — Written (R2.3). Covers auditd rules per action type, the
  optional eBPF source with its kernel requirements, the `uid_map`, and a
  post-deploy checklist. The through-line: everything degrades, nothing here is
  a hard dependency.

# DEPLOY — COLLECTOR (LAYER 3 TELEMETRY)

**Audience:** the operator standing up an `agentids` collector on a host.
**Scope:** enabling the Layer 3 telemetry sources — auditd (every host) and the
optional eBPF `file.access` adapter (modern kernels). Layers 1/2 need none of
this; see the README.

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

Without `connect`, `egress_after_identity_change` — the rule this project was
built for — cannot fire. Without `bind`/`listen`, the kernel-side listener rule
stays idle. `execve` alone is the minimum; all four is the intended set.

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

## 3. The `uid_map` — readable alerts (central, not collector)

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

## 4. Sanity checklist

After deploying a collector:

- [ ] `agentids-collector -once -dry-run` exits clean (config parses, roots
      exist, rules load).
- [ ] Editing an agent's `SOUL.md` produces an `identity.drift` event (Layer 1).
- [ ] A `bash -c "cat ~/.ssh/id_ed25519"` in the audit log fires
      `carbonato.secret.read_order_follows_constraint` (Layer 3, argv path).
- [ ] If eBPF is enabled: startup logs either "adapter attached" or exactly one
      "unavailable" line — never a crash.
- [ ] Central logs alerts with a **name**, not `uid:N` (if `uid_map` is set).

## Log
- **2026-09-28** — Written (R2.3). Covers auditd rules per action type, the
  optional eBPF source with its kernel requirements, the `uid_map`, and a
  post-deploy checklist. The through-line: everything degrades, nothing here is
  a hard dependency.

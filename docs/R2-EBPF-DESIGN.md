# R2 — eBPF `file.access` ADAPTER (DESIGN)

**Project:** Carbonato-class Agent Intrusion Detection Tool
**Closes:** G3 (no kernel file-access telemetry) — the last high-value gap
**Status:** Design — Sept 28, 2026
**Owner:** Vivi (Software Developer)
**Prereqs:** none shipped; this is scoped as its own phase, not a heartbeat step

---

## Why this is the strategic bet

Every other adapter reads what a process *chose to say*:
- `execve` argv is the command line — evadable by one `python -c` wrapper.
- auditd `connect`/`bind` are honest, but only network.

G3 is the gap that matters because the CARBONATO objective is **read the model
key**. Today we catch it only when the path appears in argv
(`cat ~/.ssh/id_ed25519`) — `carbonato.secret.read_order_follows_constraint`
fires on that string. A process that opens a key file *without naming it* is
invisible. That is a one-line evasion, and the rule that carries the most weight
rests on it.

`file.access` from the kernel closes the evasion: it reports the file that was
opened, whatever the argv said. The rule type, the schema, and the pack entry
already exist (`rules/carbonato.yaml`, `type: file.access`) — **only the source
is missing.** This document is how we add it without taking the fleet down.

## The seam we already have

```go
type Source interface {
    Actions() ([]Event, error)
}
```

Every adapter is one file satisfying `Actions()`. The engine, timeline, spool,
central, and tests do not change. eBPF is no exception — but it differs from the
auditd tail in one way that shapes the whole design: **auditd is a file we read;
eBPF is a program we load into the kernel.** That is a new class of risk, and
the design has to respect it.

---

## Design decisions

### D1 — eBPF is an *additional* source, never a replacement
The collector keeps `FileSource` (auditd) and adds an eBPF source alongside.
A host can run either, both, or neither. Reasons:
- eBPF needs a modern kernel (5.8+ for ringbuf, 4.18+ for BTF), root/CAP_BPF,
  a mounted `debugfs`/`bpffs`, and a compile-time or CO-RE artifact. auditd
  needs a package.
- A fleet is heterogeneous. The tool must degrade, not fail.
- Two sources overlapping on one event is fine: matches are idempotent by rule
  id + action, and the spool is at-least-once by design.

**Consequence:** `file.access` may be reported by eBPF on modern hosts and by a
future `auditd -F path=` watch on older ones. Same action type, same rules.

### D2 — A single multiplexing `Source`, so the collector stays dumb
Rather than teach the collector about every backend, add a `MultiSource` that
composes several `Source`s and concatenates their batches:

```go
type MultiSource struct{ srcs []Source }
func (m *MultiSource) Actions() ([]Event, error) {
    var out []Event
    for _, s := range m.srcs {
        evs, err := s.Actions()        // one backend failing must not blind the rest
        if err != nil { m.log(err); continue }
        out = append(out, evs...)
    }
    return out, nil
}
```

This is the cheapest possible change to `run.go` and it is testable without a
kernel. **Ship this first** — it is useful even before eBPF lands (auditd log +
a second auditd source + a text source in a demo all compose).

### D3 — Compile eBPF as a separate, optional build tag
The default `go build ./...` must not require clang, libbpf, or a kernel
headers package. Put the eBPF source behind `//go:build linux && ebpf` and a
vendored CO-RE object (`.o`) checked in as release artifact, so a stock checkout
builds on any machine and an operator opts in.

```go
//go:build linux && ebpf

package telemetry
// NewEBPFSource returns a source that loads the embedded CO-RE object and
// streams file-open events from a ring buffer.
func NewEBPFSource(cfg EBPFConfig) (*EBPFSource, error) { ... }
```

A non-tagged file supplies a stub `NewEBPFSource` that returns
`ErrEBPFUnsupported`, so callers always compile and a misconfigured host gets a
clear log line, not a build error.

### D4 — What the program hooks: `openat`/`openat2`, filtered in-kernel
- Hook `sys_enter_openat` (and `openat2`) — the modern open path; skip `open`.
- Attach a **kprobe/tracepoint**, not an LSM hook, for v1: LSM needs a BPF-LSM
  kernel and lockdown cooperation, which is a support burden we do not want on
  the first cut.
- **Filter in the kernel** by path prefix (an approved-paths map) so the ring
  buffer carries secret-store opens, not every file the host touches. A host
  opens thousands of files a second; shipping them all to userspace is a
  self-inflicted flood and a privacy problem. The map holds the prefixes that
  matter: `~/.ssh`, `~/.aws`, `~/.config/gcloud`, `/etc/shadow`, agent key dirs.
- Emit: pid, uid, comm, the resolved path (from a per-pid cwd + path cache), and
  a read/write flag. The collector joins pid→agent the same way the auditd
  adapter does.

### D5 — Resolve the path in userspace, not the probe
Kernel-side path resolution is version-fragile (`d_path` signatures differ).
The probe emits the raw path + pid; a small userspace cache composes it with
the pid's cwd (tracked from a `chdir` hook) and emits `file.access`. If the path
cannot be resolved, drop the event — **a wrong path is worse than no event**,
the same principle as uid resolution.

### D6 — Fail open and loud, never fail closed
If the eBPF object cannot load (old kernel, no CAP_BPF, lockdown, verifier
rejection), the collector logs one clear line and the auditd source keeps
running. `file.access` simply stops being reported. A detector that refuses to
start because a *second* telemetry source is unavailable is worse than one that
runs degraded and says so.

### D7 — No privileges beyond BPF
CAP_BPF + CAP_PERFMON (or root) plus a mounted bpffs. No CAP_SYS_ADMIN sprawl.
Document the exact capability set in the deploy docs — operators want to know
what they are granting.

---

## Rule impact (why this is worth doing)

`rules/carbonato.yaml` already has the target:

```yaml
- id: carbonato.secret.read_order_follows_constraint   # severity: critical
  match:
    type: file.access
    path_glob_any: ["*/.ssh/*", "*/.aws/*", "*id_rsa*", ...]
```

Once the source exists, that rule stops depending on argv and fires on the open
itself. The `path_glob_any` predicate and the timeline correlation
(`carbonato.secret.read_after_shell`) are already implemented and tested against
synthetic `file.access` actions — they will light up real.

**Acceptance for the eBPF phase:** a process that opens `~/.ssh/id_ed25519` via
`python -c "open(...)"` — argv never naming the path — produces a
`file.access` action, and `carbonato.secret.read_order_follows_constraint`
fires. That is the evasion closed.

---

## Deliverable order

| Step | Deliverable | Kernel needed? | Ship independently? |
|---|---|---|---|
| R2.0 | `MultiSource` + tests | No | ✅ yes — do this first |
| R2.1 | `EBPFConfig` + build-tagged stub + `ErrEBPFUnsupported` wiring | No | ✅ yes |
| R2.2 | The CO-RE `.o` + loader + ring buf → `file.access` | Yes | then |
| R2.3 | Deploy docs: caps, bpffs, kernel floor, the "it silently degrades" note | No | with R2.2 |
| R2.4 | e2e: the argv-free secret read, caught | Yes | closes G3 |

**R2.0 and R2.1 need no kernel and no privileges** — they can be built and
tested on this host today, and they leave the tree strictly better even if the
eBPF object never lands. Recommended next step (heartbeat 23): R2.0.

---

## Environment probe (this host, Sept 28)

Before writing R2.2, checked whether this host could build and load an eBPF
object:

| Check | Result |
|---|---|
| Kernel | 6.12.85 (≥ 5.8, so ringbuf + CO-RE fine) |
| BTF (`/sys/kernel/btf/vmlinux`) | present — CO-RE will work |
| `CAP_BPF` / `CAP_PERFMON` | present in the bounding set |
| `clang` | **absent** |
| `bpftool` | **absent** |
| libbpf headers (`bpf_helpers.h`) | **absent** |

**Conclusion:** the kernel-side is ready, the toolchain is not. Building the
CO-RE object needs `clang` + libbpf headers (a `apt install clang llvm
libbpf-dev bpftool` class of change, and a root install). That is a heavier
step than a heartbeat should take unilaterally on a shared box.

**Decision:** do **R2.3 (deploy docs)** next — it needs no kernel and no
toolchain, and it is the artifact an operator needs *regardless* of when R2.2
lands. Then proceed to **R3 (Docker events)**, also kernel-free, and leave R2.2
for a host with the toolchain available (or an explicit go-ahead to install it).
This keeps every heartbeat shipping something real without a risky install.

## Log
- **2026-09-28** — Environment probed: kernel + BTF + CAP_BPF ready, clang /
  bpftool / libbpf headers absent. R2.2 (the object) is blocked on a toolchain
  install; R2.3 (deploy docs) and R3 (Docker events) are not.
- **2026-09-28** — **R2.1 shipped.** `EBPFConfig` + `Validate` +
  `DefaultWatchPaths`; build-tagged stub (`!ebpf`) / entry point (`ebpf`); the
  disabled→(nil,nil), enabled-without-support→`ErrEBPFUnsupported` contract; and
  the collector wiring that attaches it or logs-and-degrades. No kernel needed.
- **2026-09-28** — Design written. eBPF scoped as its own phase; the seam is the
  existing `Source` interface. Seven decisions (D1–D7) fixing the two things
  that shape everything: eBPF is additive (D1), and the collector must degrade
  not fail (D6). Deliverable order R2.0–R2.4 set; R2.0 (`MultiSource`) needs no
  kernel and is the recommended next step.
- **2026-09-30** — **`scripts/ebpf-preflight.sh` shipped** (kernel-free,
  read-only). The environment probe that answered "can this host build/load it?"
  was a hand-typed shell check re-trusted from scratch each time; it is now one
  command with a BUILD/LOAD verdict and `--require-build`/`--require-load` exit
  codes for CI. Re-run on this host: **LOAD ready** (kernel 6.12, BTF,
  CAP_BPF/CAP_PERFMON as root, bpffs mounted), **BUILD not** (no clang, no
  libbpf headers). Nothing changes on the host. R2.2 stays gated on the toolchain
  decision, but the ask now names a command instead of a paragraph.

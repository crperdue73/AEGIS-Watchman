# FINDING 2026-10-26f — on an auditd-only host, five of the pack's six critical rules are dark

**Status:** OPEN — deployment posture note. No code changed.
**Found:** beat 602
**Method:** read `behavior.go:245-262`, `InertRules` transcription in `scripts/inertreach_test.py`.

## The fact

`available[]` is built from what actually attached:

| class | attached when |
|---|---|
| `process.exec`, `net.egress`, `net.listen`, `file.write` | `cfg.Behavior.AuditLog != ""` |
| `file.access` | `ebpfAttached` |
| `container.start` | `dockerAttached` |
| `scope.change` | `scopeAttached` |

On a host with auditd and **nothing else** — the default the collector degrades to, and the one it
explicitly logs (`"eBPF file.access adapter unavailable, running auditd-only"`) — the pack loads **20 rules,
13 of which can fire**. The other seven:

| rule | type | severity |
|---|---|---|
| `secret.read_order_follows_constraint` | `file.access` | **critical** |
| `secret.read_after_shell` | `file.access` | **critical** |
| `secret.read_model_key` | `file.access` | high |
| `container.privileged_start` | `container.start` | **critical** |
| `container.host_root_mount` | `container.start` | **critical** |
| `container.after_identity_change` | `container.start` | **critical** |
| `tool.scope_new` | `scope.change` | high |

Measured severity split:

| | critical | high | medium |
|---|---|---|---|
| **dead (7)** | **5** | 2 | 0 |
| **live (13)** | **1** | 5 | 7 |

Five of the pack's six critical rules are dark. The one that survives is
`exfil.egress_after_identity_change` — which finding 26c showed can only fire against two brands on three IP
ranges.

So the severity distribution inverts against capability: the more of the pack you can *see*, the less of the
pack you can *use*. A host running only auditd — a plain Linux VM, no eBPF toolchain, no Docker — loses five of
six critical rules to attachment, and the sixth to a hardcoded two-entry service map.

## Why this is not a defect in the code

`InertRules` handles this **correctly**. It names all seven, the log says so, and `behavior.go:179` logs the
eBPF failure and continues rather than aborting. That is good engineering: degrade, report, keep running.

The finding is about what the *report* means. A startup line reading

> 7 of 20 loaded rule(s) cannot fire with the attached sources

is true, is honest, and is easy to read as a footnote. It is not a footnote. It is "the two-thirds of the
severity scale that was not already covered is now entirely uncovered."

## Why it is worth writing down

The severity pack was authored against a **fully equipped** deployment — auditd + eBPF + Docker + scope shim.
It reads as a coherent security product. Distributed to anything less, the top of the severity scale does not
degrade gracefully; it switches off, and the seven rules that do it are precisely the ones an operator would
want most.

Two things would close this, both owner's call:

1. **A degrade-mode summary** at startup that leads with severity, not count: *"7 rules inert: 5 critical,
   2 high. Critical coverage on this host: 1 of 6."* Same data, ordered by what matters.
2. **A non-eBPF fallback for `file.access`.** auditd can carry it with a watch rule — this is the same
   prerequisite the pack already documents for `file.write` (`docs/deploy-collector.md`). Not free: audit
   watches on every secret path are noisy and expensive. But it would move three critical rules from dark to
   live on hosts that will never run eBPF.

Neither applied. This is a posture decision, not a bug fix.

## Guard

`scripts/inertreach_test.py` section 5 now pins the auditd-only verdict exactly: 7 inert, their IDs, the
severity split (5 critical / 2 high dead vs 1 critical / 5 high / 7 medium live). If the pack adds a rule keyed on a new class, or someone makes a
secret rule auditd-backed, this suite tells the next reader that the posture changed.

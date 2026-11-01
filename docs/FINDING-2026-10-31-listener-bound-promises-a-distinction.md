# FINDING — 2026-10-31: `carbonato.net.listener_bound` documents a distinction its matcher does not make

**Status:** measured. **The rule fires, the description does not.** Either the description is wrong or the matcher is.
**Severity of the gap:** medium — this is a `severity: medium` rule that fires on every listener, so its signal
is "an agent bound any socket", not "an agent bound a suspicious one".
**Instrument:** `scripts/packcheck.py` (the transcription of the engine's glob and `detail_*` predicates) — every
claim below re-runs through it. Predicate semantics cross-checked against `internal/rules/engine.go:148-175`.
**Written up:** beat 714. Rule at `rules/carbonato.yaml:379-395`.

## The rule as written

```yaml
- id: carbonato.net.listener_bound
  title: Process binds a listening socket
  severity: medium
  technique: phase3/command-and-control
  description: >
    The auditd bind syscall carries the local address a process is listening
    on, whether or not the command line says so. This is the kernel-side
    counterpart to carbonato.net.new_listener: it fires for a listener
    started by a wrapped or renamed binary, where argv_any would miss. A
    bind to a wildcard or a high port under an agent account is a foothold,
    not a service.
  match:
    type: net.listen
    detail_non_empty:
      - "listen"
```

## What the predicate actually means (from the engine, not from the description)

`internal/rules/engine.go:148-151`:

```go
for _, k := range m.DetailNonEmpty {
    if detailString(action, k) == "" {
        return false
    }
}
```

`detail_non_empty` is a **presence test**: the `listen` field must exist and be non-blank. Nothing about its value
is examined — no wildcard, no port, no interface, no account.

## Measured consequence

| detail | fires? | is it a foothold? |
|---|---|---|
| `{"listen": "127.0.0.1:22"}` | **True** | no — loopback only |
| `{"listen": "0.0.0.0:443"}` | **True** | no — a service |
| `{"listen": "0.0.0.0:4444"}` | **True** | yes |
| `{"listen": "[::]:27017"}` | **True** | maybe — public database |
| `{"listen": ""}` | False | — |
| `{}` | False | — |

**The rule separates "a listener exists" from "no listener", and nothing else.** Its description promises a
distinction it does not implement: *"a bind to a wildcard or a high port under an agent account is a foothold, not
a service."* As shipped, a legitimate `127.0.0.1:22` and a wildcard `0.0.0.0:4444` are the same alert.

## Why this matters more than one noisy rule

It is the same shape as beat 649's `argv_any` finding and beat 712's credentialed-URL finding, but **a new
variant**: not a family with no fixtures, and not a construct the language can't express — this one **is
expressible** and simply was not expressed. `detail_glob_any` exists (`engine.go:167`) and would do it:

```yaml
    detail_glob_any:
      listen:
        - "0.0.0.0:*"    # wildcard binds: reachable off-host
        - "[::]:*"
        - "*:4444"       # common backdoor ports
```

That is a **rules-only** change, no engine work, no schema change — the cheapest class of fix available, which is
why it is worth flagging precisely.

**Measured, so this is not another untested proposal** (through `packcheck.match_one`, the transcription of
`anyGlobMatches`):

```
127.0.0.1:22  -> False     (correct: loopback only)
0.0.0.0:443   -> True
0.0.0.0:4444  -> True
[::]:27017    -> True
0.0.0.0:22    -> True
```

**What that measurement shows and what it does not.** The globs *do* buy the headline distinction — loopback stops
firing, wildcard binds fire. But they do **not** separate `0.0.0.0:443` (a service) from `0.0.0.0:4444` (a
backdoor): both are wildcard binds, and the glob can see the interface but not "is this a service port". Getting
that half right needs a port allowlist, which is a genuine product decision (which ports are legitimate on this
fleet?) and not a glob anyone can derive from the rule text. **So the honest recommendation is a partial fix that
still reduces noise a lot, not a complete one.**

## What I am NOT claiming, and did not do

- **I did not edit the rule.** Narrowing a `severity: medium` detector changes what the product reports, and a
  false negative here (a real foothold that binds loopback then proxies) is worse than the current noise. The port
  list is a judgment call with a real trade-off, and it should be made deliberately with the glob list reviewed —
  not slipped in on a heartbeat.
- **I did not verify the intended semantics against a real auditd bind record.** I read the engine and simulated
  the predicate; I have not seen a live `net.listen` action on this host. The *matcher semantics* are certain
  (they are three lines of Go); what a real `listen` string looks like is an assumption I am marking as one.
- **No test was added.** A fixture asserting today's behaviour would pin the noise as correct.

## Recommended next step (small, and Dad's call)

Decide the rule's intent: **"any listener" (keep as-is, and fix the description to stop promising more)**, or
**"suspicious listener" (add `detail_glob_any`, keep `detail_non_empty` as the cheap pre-filter)**. Both are
defensible; leaving the prose promising one thing while the code does the other is the only option that isn't.

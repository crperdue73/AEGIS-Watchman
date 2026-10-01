# Quickstart — build it, run it, see an alert

One page: from nothing to a detection on screen. Every command here is
copy-pasteable and needs **no root, no live auditd, and no Docker daemon**.
Depth lives elsewhere, and this page links to it rather than repeating it.

> This is the *fastest path*. The full product overview is the
> [README](../README.md); the operator setup is split in two —
> [`deploy-central.md`](deploy-central.md) (the service, its stores, TLS,
> alerting) and [`deploy-collector.md`](deploy-collector.md) (auditd rules,
> eBPF, Docker events).

## 1. Build

```sh
git clone <repo> && cd watchman
make            # -> ./bin/watchman-{collector,central,manifest,rules}
```

Needs Go (one build dependency, no CGO). `make build` stamps version, commit,
and date into the binaries, so `./bin/watchman-central -version` is real. To add
the optional pure-Go SQLite record backend: `go build -tags sqlite ./cmd/...`.

## 2. See a detection in one command

The demo replays a captured CARBONATO-class intrusion through the **real**
binaries and prints the alerts, then the fleet rollup:

```sh
cd demo && ./run.sh
```

Expect a privileged, host-mounted container on the wire (critical — the entry
vector), a drift from the persona-file edit, and the correlation rule firing
after it. What each line means, and why it is built from fixtures:
[`demo/README.md`](../demo/README.md).

If you read nothing else, read that output once — it is the whole product in
one screen.

## 3. Run it yourself (local, harmless)

**The dry-run needs nothing running.** It validates the config *and* the rule
pack and ships nothing, so run it on its own first — from the repository root:

```sh
./bin/watchman-collector -config examples/collector.local.yaml -once -dry-run
```

To see an actual submission, start the service first. It runs in the
**foreground**, so give it its own terminal (or append `&`; bring it back with
`fg`) — then run the collector without `-dry-run` in another:

```sh
# terminal 1 — leave it running; Ctrl-C to stop
./bin/watchman-central -config examples/central.local.yaml

# terminal 2 — drop -dry-run to actually submit to the local central
./bin/watchman-collector -config examples/collector.local.yaml -once
```

The `.local` configs use only paths that exist in a fresh clone (the shipped
rule pack, the demo persona) and none of the `/etc/watchman/...` system paths,
so they work with nothing installed. `-dry-run` validates the config **and the
rule pack**, prints the inventory, and ships nothing. `-once` does a single pass
instead of the poll loop. The operator templates with real system paths are
`examples/central.yaml` and `examples/collector.yaml`; every `collector.yaml`
key, with its default, is in [`collector-config.md`](collector-config.md).

## 4. The thing that makes it actually fire

Layer 3 matches **actions**, and the actions come from auditd — which does
**not** audit `connect`/`bind`/`listen` out of the box. Enable them before you
expect any behavioral rule to trigger:

```sh
# /etc/audit/rules.d/watchman.rules
-a always,exit -F arch=b64 -S execve   -k watchman
-a always,exit -F arch=b64 -S connect  -k watchman
-a always,exit -F arch=b64 -S bind     -k watchman
-a always,exit -F arch=b64 -S listen   -k watchman

sudo augenrules --load
```

Then point the collector at the log:

```yaml
behavior:
  rules_dir: /etc/watchman/rules
  audit_log: /var/log/audit/audit.log
```

Skip this and the collector runs, the rules load, and **nothing fires** — that
silence is the most common first-run confusion. Per-rule breakdown:
[`deploy-collector.md`](deploy-collector.md) §1.

## 5. Where to go next

- **Write a rule** — format, predicates, correlation, the seed pack:
  [`rules.md`](rules.md).
- **Operate the collector** — auditd prerequisite, eBPF source, Docker events,
  post-deploy checklist: [`deploy-collector.md`](deploy-collector.md).
- **Operate central** — the two stores, mutual TLS, Layer 2 provenance,
  `uid_map`, alerting, retention/backends: [`deploy-central.md`](deploy-central.md).
- **Read the fleet view** — `GET /` (human page) and `GET /api/v1/fleet`
  (JSON): [`fleet-view.md`](fleet-view.md).
- **Understand the data flow** before adding a source or rule:
  [`architecture.md`](architecture.md). Event contract: [`event-schema.md`](event-schema.md).
- **Know what it can't see** — the blind spots, kept honest:
  [README § Known blind spots](../README.md#known-blind-spots).

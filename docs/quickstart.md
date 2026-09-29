# Quickstart — build it, run it, see an alert

One page: from nothing to a detection on screen. Every command here is
copy-pasteable and needs **no root, no live auditd, and no Docker daemon**.
Depth lives elsewhere, and this page links to it rather than repeating it.

> This is the *fastest path*. The full product overview is the
> [README](../README.md); the operator setup (auditd rules, central's TLS,
> alerting) is in [`deploy-collector.md`](deploy-collector.md).

## 1. Build

```sh
git clone <repo> && cd agentids
make            # -> ./bin/agentids-{collector,central,manifest}
```

Needs Go (one build dependency, no CGO). `make build` stamps version, commit,
and date into the binaries, so `./bin/agentids-central -version` is real. To add
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

```sh
./bin/agentids-central   -config examples/central.yaml       # the service
./bin/agentids-collector -config examples/collector.yaml -once -dry-run
```

`-dry-run` parses config and loads rules without shipping anything; `-once`
does a single pass instead of the poll loop. Drop `-dry-run` and the collector
submits to central's ingest endpoint. Every `collector.yaml` key, with its
default, is in [`collector-config.md`](collector-config.md).

## 4. The thing that makes it actually fire

Layer 3 matches **actions**, and the actions come from auditd — which does
**not** audit `connect`/`bind`/`listen` out of the box. Enable them before you
expect any behavioral rule to trigger:

```sh
# /etc/audit/rules.d/agentids.rules
-a always,exit -F arch=b64 -S execve   -k agentids
-a always,exit -F arch=b64 -S connect  -k agentids
-a always,exit -F arch=b64 -S bind     -k agentids
-a always,exit -F arch=b64 -S listen   -k agentids

sudo augenrules --load
```

Then point the collector at the log:

```yaml
behavior:
  rules_dir: /etc/agentids/rules
  audit_log: /var/log/audit/audit.log
```

Skip this and the collector runs, the rules load, and **nothing fires** — that
silence is the most common first-run confusion. Per-rule breakdown:
[`deploy-collector.md`](deploy-collector.md) §1.

## 5. Where to go next

- **Write a rule** — format, predicates, correlation, the seed pack:
  [`rules.md`](rules.md).
- **Operate it** — auditd prerequisite, eBPF source, Docker events, uid_map,
  post-deploy checklist: [`deploy-collector.md`](deploy-collector.md).
- **Read the fleet view** — `GET /` (human page) and `GET /api/v1/fleet`
  (JSON): [`fleet-view.md`](fleet-view.md).
- **Understand the data flow** before adding a source or rule:
  [`architecture.md`](architecture.md). Event contract: [`event-schema.md`](event-schema.md).
- **Know what it can't see** — the blind spots, kept honest:
  [README § Known blind spots](../README.md#known-blind-spots).

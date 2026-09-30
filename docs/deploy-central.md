# DEPLOY — CENTRAL (INGEST, ALERTING, FLEET VIEW)

**Audience:** the operator standing up `agentids-central` — the one service the
whole fleet talks to.
**Scope:** the decisions central forces on you: what it stores, how it
authenticates collectors, how it alerts, and how it stays bounded. The collector
has its own guide ([`deploy-collector.md`](deploy-collector.md)); this is the
other half.

> For the **every-key reference** to `central.yaml` (all fields, defaults, and
> what the loader rejects), the annotated [`examples/central.yaml`](../examples/central.yaml)
> is the source of truth — it carries a comment on every field. This page is the
> *why* and the *deploy*; that file is the *what*.

Central is the only stateful component. It has two jobs — **ingest and alert**
(the security function) and **keep the fleet view** (the operator's console) —
and both are backed by files on disk. The question this page answers is: *what
are those files, who may write to central, and what happens to an alert after it
fires?*

The answer, up front: **it runs with no config at all.** A missing `central.yaml`
uses loopback-safe defaults (`:8080`, a baseline store in the working directory,
log-only alerting). That is enough to see it work and not enough to deploy it,
which is what the rest of this page fixes.

---

## 1. The surface

One listener, five endpoints. Two are for collectors (write), three are for you
(read):

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/healthz` | Liveness. Always `{"status":"ok"}` — no store read, no dependencies. |
| `POST` | `/api/v1/events` | **Ingest an inventory.** Collector → central; returns drift, `202 Accepted`. |
| `POST` | `/api/v1/actions` | **Ingest Layer 3 behavior events.** Collector → central; returns matches, `202 Accepted`. |
| `GET` | `/api/v1/fleet` | The fleet rollup as JSON. Scriptable. |
| `GET` | `/` | The same rollup as a plain HTML page. |

`/api/v1/events` and `/api/v1/actions` are **`POST`-only** and cap the body at
**1 MiB** (a guard, not a limit — a fleet-scale inventory is far smaller); a
wrong method is `405`, malformed JSON is `400`, and a missing `host.id` is
rejected before anything is stored.

`/healthz` deliberately touches no disk: a health check that reads a store turns
a slow disk into a failed health check, and a failed health check into a
restart loop.

The read side — what the rollup contains, its query parameters
(`host`, `since`, `min_severity`), and the HTML page — is documented in full in
[`fleet-view.md`](fleet-view.md). This page only wires it up.

```yaml
# central.yaml
addr: ":8080"
```

Bind `:8080` only where collectors can reach it. If collectors run on other
hosts, that means a routable address **behind TLS** (§3) — not a public plaintext
port.

---

## 2. The two stores

Central persists two different things, and conflating them is the mistake this
section exists to prevent.

| | Baseline store | Record store |
|---|---|---|
| Config key | `store` | `records` |
| Holds | each agent's last-known file hashes — **the drift baseline** | a bounded, time-limited copy of every **detected event** |
| Backs | Layer 1 drift (`identity.drift`) | the fleet view (`GET /`, `/api/v1/fleet`) |
| Unset means | *(never)* — defaults to `agentids-baselines.json` | in-memory only; the view works but **resets on restart** |
| Written by | every ingest | every detected event |

**The baseline store is not optional and not cache.** It is the thing your
detection is measured against: if it is lost, the next ingest re-baselines and
every existing drift becomes a silent "first sight" instead of an alert. Put it
on durable storage.

**The record store is the console, and losing it is survivable.** It is
append-only and time-bounded, so it can be discarded and rebuilt from the next
events without re-baselining anything — which is exactly why it is a *separate*
file. Omit `records` and central keeps records in memory: the fleet view still
renders, but a restart empties it. Set it the moment you care about looking back
at an incident.

```yaml
# central.yaml
store:   /var/lib/agentids/baselines.json   # the drift baseline — durable
records: /var/lib/agentids/records.json     # the fleet view's data
```

Both files are created `0600`. The systemd unit gives central a private
`StateDirectory=agentids`, so `/var/lib/agentids` already exists, is owned by the
unprivileged `agentids` user, and is the only place central needs to write — see
§8.

### Retention is bounded by two independent limits

```yaml
record_retention_days: 7    # drop records older than this
record_max: 10000           # hard cap on retained records
```

Time **and** count, on purpose: the count cap is what stops a severity storm (a
rule misfire, a fleet-wide identity change) from growing the store without limit,
and the time cap is what stops a quiet fleet from keeping stale detail forever.
When both apply, the stricter one wins and the oldest records are trimmed first.

A baselines file has no such cap — it is one entry per agent file, not one per
event, and it only ever reflects the *current* state. That is why it can be small
and durable while the record store is large and disposable.

---

## 3. TLS — the collector hop is the trust boundary

Central's ingest carries the fleet's inventory: every agent persona file, its
hash, and its diff. That payload is a map of your fleet's soft spots, so the hop
is authenticated in **both** directions — a stolen collector cert still cannot
impersonate central, and a rogue endpoint cannot enroll as a collector.

```sh
./deploy/gen-certs.sh /etc/agentids/tls central.example.com
```

Point central's `tls` block and each collector's `report.tls` at the generated
material:

```yaml
# central.yaml
tls:
  cert: /etc/agentids/tls/leaf.crt   # central's own cert/key
  key:  /etc/agentids/tls/leaf.key
  ca:   /etc/agentids/tls/ca.crt     # verifies every collector's cert
```

When `tls` is set, central requires and verifies a client certificate on the
ingest listener; an unauthenticated client is refused at the handshake, before
any request is parsed. TLS 1.2 is the floor.

Three rules the loader enforces, so a half-configured hop fails at startup rather
than silently downgrading:

- Setting **any** of `cert`/`key`/`ca` enables TLS; you must then set **all
  three**. A cert with no CA is rejected — encrypting to an unauthenticated
  stranger is worse than plaintext because it looks safe.
- `server_name` overrides the name checked against certificates; it defaults to
  the URL host on the collector side. Only set it when the fleet reaches central
  by a name that differs from its cert.
- `insecure_skip_verify` exists only for tests and is named to be uncomfortable
  to set. Production code leaves it `false`.

**With `tls` unset, central speaks plain HTTP and logs a warning at startup:**

```
agentids-central: WARNING plaintext ingest (configure tls for a real fleet)
```

That warning is the whole point — a loopback test is fine, a fleet is not. If you
see it in a deployment log, the hop is unauthenticated.

---

## 4. Layer 2 — provenance (optional, high value)

Layer 1 catches a **change** to a file it already knew. Layer 2 catches a file
that was **never yours** — a brand-new dropped skill, which drifts from nothing
and would otherwise sail through.

Central is where Layer 2 is enforced: it compares each ingest against a
**signed** approval manifest (one `*.json` per agent root, produced with
`agentids-manifest sign`) and raises `provenance.untrusted` for anything
unapproved or modified. Because the source of truth is signed, it is not evadable
by rewriting a baseline hash.

```yaml
# central.yaml
manifests:
  dir: /etc/agentids/manifests            # one *.json per agent root; empty -> Layer 2 off
  trust_keys: /etc/agentids/trust-keys.json  # {"name": "base64-ed25519-pubkey"}
  require_approval: false                 # true -> an agent with NO manifest is fully untrusted
```

- An empty `dir` **disables** Layer 2 entirely (no manifests, no checks) — that
  is the default, and it is honest: an unsummarized fleet gets Layer 1, not a
  half-configured Layer 2.
- `trust_keys` may be omitted, in which case manifests are trusted as shipped
  (no signature check). Set it if you want the signing guarantee; a manifest
  signed by a key not in the file is **refused at load**.
- `require_approval` defaults **off** so a fleet that has not migrated yet is not
  drowned in alerts on day one. Turn it **on** once every agent has a manifest —
  then an approval-less agent is loud instead of silent.

Layer 2's load-time philosophy matches the rest of the project: a bad approval
list or an unreadable trust-key file **fails startup** rather than quietly
degrading, because "we checked provenance" and "we meant to" must not look the
same in a log.

---

## 5. Readable alerts — `uid_map`

The collector reports numeric uids. Turning a uid into a name is a presentation
concern, so it lives on central:

```yaml
# central.yaml
uid_map: /etc/agentids/passwd
```

A passwd-style file (`name:passwd:uid:gid:...`) — copy it from the fleet's base
image. An alert then reads `user=deploy uid=1000` instead of `uid:1000`.
Resolution is **best-effort**: an unknown uid stays numeric and the numeric id is
always kept alongside the name, because a *wrong* name is worse than no name.

Unlike Layers 2's fail-loud stance, a **configured** `uid_map` that cannot be
read **is** an error: you asked for names, so silently falling back to numerics
would hide a real misconfiguration. (Omit the key to leave uids numeric.)

---

## 6. Alerting — three sinks, composed

An alert is **one batch per ingest cycle**, not one message per event: a noisy
cycle produces a single notification, and the alert names its class — `3 drift(s)`,
`2 rule match(es)`, or mixed `4 event(s)` — because a Layer 3 rule match is a
different animal from a file drift and the person triaging a page needs to know
which they got.

```yaml
# central.yaml
alerts:
  webhook: https://hooks.example/agentids   # any endpoint that accepts a JSON POST
  log: true                                 # one line per alert to the service log (default on)
  email:                                    # delete the block to disable
    host: smtp.example.com:587              # 465 = implicit TLS; others use STARTTLS
    username: agentids
    password: change-me
    from: agentids@example.com
    to: [oncall@example.com]
```

- **`webhook`** — the body is an agentids `Alert`: `{when, host, agent, events[]}`.
  Slack incoming webhooks, PagerDuty Events API v2, a ticket bot, or a three-line
  shell receiver all work; central only does an HTTP `POST`.
- **`log`** — a one-line summary per alert via `Alert.Summary()`. Cheap, greppable,
  and the reason a fresh install alerts *somewhere* before you wire anything else.
- **`email`** — plain-text SMTP. Port `465` is implicit TLS; every other port
  uses STARTTLS. `allow_plaintext: true` is required to send without STARTTLS at
  all, and is not recommended outside a trusted local relay.

The three compose (`MultiAlerter`): all configured sinks are tried. **A delivery
failure is logged, never fatal** — central does not stop ingesting because Slack
was down for thirty seconds. That is deliberate: dropping an event to protect a
notification channel would invert the priority.

Validation the loader performs so email fails at startup, not at 03:00: if
`email.host` is set, both `from` and at least one `to` are required.

---

## 7. Backends and bounds at a glance

| Key | Default | What it does |
|---|---|---|
| `addr` | `:8080` | Listen address. Empty is rejected. |
| `store` | `agentids-baselines.json` | Durable drift baseline. Empty is rejected. |
| `records` | *(empty)* | Record store path. Empty → in-memory, view resets on restart. |
| `record_backend` | `file` | `file` (atomic JSON, zero-dep) or `sqlite` (needs `-tags sqlite`). Unknown → `file` + a log line, not a failed start. |
| `record_retention_days` | `7` | Drop records older than this. |
| `record_max` | `10000` | Hard cap on retained records. |
| `max_diff_bytes` | `4096` | Cap on the unified diff attached to each drift event. `0` = hash-only (no file bodies in alerts). Does not affect what is stored as baseline. |

`record_backend: sqlite` requires a build with `-tags sqlite` (pure-Go
`modernc.org/sqlite`, **no CGO**). The default build is one dependency; the
SQLite path trades that for scale and concurrent readers. An unknown backend
value **falls back to `file` with a log line** rather than refusing to start —
the view still works, and the log says why. The whole trade is written down in
[`P2.1C-RECORD-STORE-BACKENDS.md`](P2.1C-RECORD-STORE-BACKENDS.md).

`max_diff_bytes` is the one knob with a privacy edge: a drift alert is *useful*
because it shows the changed lines, and *dangerous* for the same reason if a
persona file holds a secret. `0` reduces every drift to a hash comparison —
you learn that something changed, not what.

Command-line flags (`-addr`, `-store`, `-webhook`) override the config file when
set, so an operator can re-point a running service without editing YAML.
`-version` prints the version, git commit, and build date and exits.

---

## 8. Running as a service

Central is the first unit to come up and the last to go down — collectors retry,
so a central restart costs latency, never evidence. The
[`deploy/`](../deploy/README.md) directory ships a systemd unit template
(`agentids-central.service`) that runs it **unprivileged** with a hardened
profile: `ProtectSystem=strict`, `PrivateTmp`, `NoNewPrivileges`,
`RestrictSUIDSGID`, address-family restrictions, and `ProtectHome=yes` (central
never reads agent workspaces — only the collector does). Its private
`StateDirectory=agentids` is the one write path, holding both stores.

```sh
sudo install -m 0755 bin/agentids-central /usr/local/bin/
sudo install -m 0640 -o agentids -g agentids examples/central.yaml /etc/agentids/central.yaml
sudo install -m 0644 deploy/agentids-central.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now agentids-central
```

The full install walkthrough — the `agentids` service account, both binaries, the
TLS step, and the capability deltas for the optional collector sources — is in
[`deploy/README.md`](../deploy/README.md). The startup log tells you the
configuration central actually resolved (store path, record backend and
retention, whether Layer 2 is on, whether uid resolution is on, and where alerts
go), so you can confirm the config took without reading it back:

```
agentids-central listening on :8080 (store=/var/lib/agentids/baselines.json)
agentids-central: fleet view records -> /var/lib/agentids/records.json (backend=file, retain=7d, max=10000)
agentids-central: layer 2 provenance enabled (manifest dir=/etc/agentids/manifests, require_approval=true)
agentids-central: uid->name resolution enabled (map=/etc/agentids/passwd)
agentids-central: webhook alerts -> https://hooks.example/agentids
agentids-central: mutual TLS required on the ingest listener
```

If a line is missing — or reads `disabled` / `in-memory only` / `WARNING
plaintext ingest` — that is your configuration, stated plainly.

> **In a container instead?** Central runs from the same image with
> `--entrypoint /agentids-central`. Two container-specific gotchas: mount the
> config directory at a path *other than* `/etc/agentids` (a bind mount there
> shadows the rule pack the image bakes), and — for mTLS — make the mounted cert
> files readable by the image's `nonroot` uid (`65532`). The exact `docker run`
> flags, verified end to end, are in
> [`deploy/README.md`](../deploy/README.md) §"Mutual TLS in containers".

---

## 9. Sanity checklist

After deploying central:

- [ ] `curl -s localhost:8080/healthz` returns `{"status":"ok"}`.
- [ ] `central.yaml` sets `store` to a durable path, and the file appears `0600`
      after the first ingest.
- [ ] `records` is set if you want the fleet view to survive a restart; the
      startup log names the backend, retention, and cap.
- [ ] With `tls` configured, central logs **"mutual TLS required"** — and a
      collector built *without* a client cert is refused at the handshake.
- [ ] Editing an agent's `SOUL.md` and running the collector `-once` produces an
      `identity.drift` and an alert (webhook/log/email) within seconds.
- [ ] Alerts log with a **name**, not `uid:N`, if `uid_map` is set.
- [ ] `GET /` renders the fleet rollup, and `GET /api/v1/fleet?min_severity=high`
      returns the same hosts as JSON.
- [ ] If Layer 2 is enabled, a manifest signed by an untrusted key is **refused
      at startup** — central does not come up trusting it.

## Log
- **2026-09-30** — Written (heartbeat 67). The collector has had an operator guide
  since R2.3, but central — the stateful half, with the store, TLS, provenance,
  alerting, retention, and the fleet view — had only the annotated example. This
  page covers the decisions central forces on an operator: the two stores and why
  they are separate, the mTLS trust boundary and its fail-closed validation, the
  Layer 2 load-time stance (fail loud) versus `uid_map` (fail loud) versus records
  (degrade quietly), alert batching and non-fatal delivery, and the retention and
  backend bounds. Cross-linked from the README, the quickstart, and
  `deploy/README.md`. Every quoted string checked against the source
  (`cmd/central/main.go`, `internal/central/{config,http,alert}.go`).

# The fleet view — the operator's console

Central ingests and alerts. The **fleet view** is the other half: a durable,
queryable record of *what the fleet has actually seen recently*, so an operator
can answer "which hosts are alerting, and with what" without grepping a log —
and can answer it after the fact, when an alert has already gone by.

It exists because alerts are fire-and-forget. A page that fired at 03:00 is gone
from the operator's screen by 09:00; someone joining the system after an
incident has nothing to look at. The record store keeps a bounded, time-limited
copy of every detected event, and the fleet view is a rollup over it.

## One source of truth, two views

- **`GET /api/v1/fleet`** — the rollup as JSON. Scriptable; anything the human
  page shows, this returns.
- **`GET /`** — the same rollup as a plain server-rendered HTML page. No JS
  framework, no build step. It is a second *view*, not a second implementation.

Both read the same store, so they cannot disagree.

## The sibling view: the identity roster

The fleet view above answers *"which hosts are alerting, and with what."* The
brief's differentiator — **every agent, its baseline** — is a different question,
and it is served by a different endpoint over a different store:
**`GET /api/v1/agents`**, the fleet's **identity roster**.

It returns every agent baseline central holds — `host_id`, `root`, and each
identity file's `path`, `sha256`, `size`, and `mod_time`. It deliberately does
**not** return file `content`: central keeps bodies to compute diffs, but a read
endpoint that shipped them would turn central into a way to read every agent's
`SOUL.md` over the network. Optional `host` and `root` filters narrow it.

It also joins **Layer 2** into Layer 1's inventory: each file carries a `trust`
verdict against the approved manifest for its root — `ok` (listed, hash matches),
`modified` (listed, hash differs), `unsigned` (on host, not listed), or
`unverified` (no manifest on record for the root). A row's `manifest` field says
whether an approval exists at all. So one query answers both halves of agent
hygiene at once: *what every agent is* **and** *whether it is approved*.

The distinction that matters operationally: the fleet view reads the *record
store* (bounded, time-limited events), so it can only speak about what has
already happened. The roster reads the *baseline store* — one durable entry per
agent file, always current — so it answers **"grep my fleet for every agent's
identity" before a single event has fired.** That is the agent-hygiene check the
CARBONATO counter is built around; `identity.drift` alerts tell you something
changed, the roster tells you what every agent *is* right now.

```jsonc
{
  "generated_at": "2026-10-01T18:00:00Z",
  "total_agents": 1,
  "total_files": 2,
  "agents": [
    {
      "host_id": "builder-01",
      "root": "/home/agent/.openclaw/workspace-vivi",
      "manifest": true,
      "files": [
        { "path": "SOUL.md", "sha256": "a2cffbd6…", "size": 1634, "mod_time": "2026-10-01T17:58:00Z", "trust": "ok" }
      ]
    }
  ]
}
```

### `GET /agents` — the same roster as a page

The roster has a human face, exactly as the fleet rollup does: **`GET /agents`**
renders the identity roster as a plain server-rendered HTML table — no JS, no
build step. It is built from the same `agentsView` the JSON endpoint returns (one
`buildAgentsView` behind both), so the page and the endpoint cannot disagree.

Each agent's block spans its file rows: `path`, the sha256 shortened to twelve
hex characters (the full digest is in the row's `title`), a human-readable size,
the mtime in UTC, and a colour-coded `trust` pill (`ok` / `modified` / `unsigned`
/ `unverified`). A root with no approval manifest shows a dashed `no` badge
rather than a red one — an unapproved agent is a state to notice, not an alarm.
The page honours the same `host=` and `root=` filters, and links back to
`/api/v1/agents` and `/`.

```sh
curl -s 'http://127.0.0.1:8080/agents?host=builder-01'
```

## Enabling it

Two lines in `central.yaml`:

```yaml
records: /var/lib/watchman/records.json   # omit -> in-memory only (resets on restart)
record_backend: file                       # "file" (default) or "sqlite"
record_retention_days: 7                   # drop records older than this
record_max: 10000                          # hard cap; bounds a severity storm
```

| Key | Default | Meaning |
|---|---|---|
| `records` | *(empty)* | Path to the durable record file. Empty keeps records in memory only — the view still works, but resets when central restarts. |
| `record_backend` | `file` | `file` (atomic JSON, no extra dependency) or `sqlite` (needs a `-tags sqlite` build). An unknown value falls back to `file` with a log line, not a failed start. |
| `record_retention_days` | `7` | Records older than this are dropped on the next write. |
| `record_max` | `10000` | Cap on retained records; the oldest are trimmed past it. |

Retention is bounded by **both** a time window and a count, so a severity storm
cannot grow the store without limit. Records are append-only and time-bounded,
so switching backends is a config change, not a migration.

### Backends

| Backend | Build | Use |
|---|---|---|
| `memory` | always | tests, `-dry-run`; non-durable |
| `file` | always | the default: one small atomic-JSON file, mode `0600`, reloaded on restart |
| `sqlite` | `-tags sqlite` | scale and concurrent readers; pure-Go (`modernc.org/sqlite`), **no CGO** |
| `postgres` | — | scoped, not shipped (P2.1c-4) |

The JSON file is fine for a small fleet. Its ceiling — a whole-file rewrite per
append, a single writer, no index — is written down in
[`P2.1C-RECORD-STORE-BACKENDS.md`](P2.1C-RECORD-STORE-BACKENDS.md), which is why
the default is honest rather than clever.

## `GET /api/v1/fleet`

Query parameters (all optional):

| Param | Default | Meaning |
|---|---|---|
| `host` | *(all)* | Restrict the rollup to one host id. |
| `since` | *(unbounded)* | Only records at or after this time. **RFC 3339**; a malformed value returns `400`, not a silent empty view. |
| `min_severity` | *(none)* | Only records at or above this severity (`info` < `low` < `medium` < `high` < `critical`). |
| `limit` | `5000` | Max records scanned. A second guard on top of retention, so a query stays cheap. |
| `recent` | `10` | Max recent records embedded per host. |

Response shape:

```jsonc
{
  "generated_at": "2026-09-29T15:36:00Z",
  "total_events": 12,
  "hosts": [
    {
      "host_id": "demo-host",
      "host_name": "demo",
      "total": 12,
      "max_severity": "critical",
      "last_seen": "2026-09-29T15:35:41Z",
      "by_severity": { "critical": 5, "high": 5, "medium": 2 },
      "by_technique": { "phase3/exfiltration": 1, "phase3/scope-expansion": 2 },
      "by_source": { "heuristic": 2 },
      "by_pack": { "carbonato@7": 12 },
      "recent": [ /* the newest Records for this host */ ]
    }
  ]
}
```

Hosts are ordered **worst-first** — by max severity, then by total — so the
console leads with what an operator has to look at. A `severity` floor becomes
an indexed range scan on the SQLite backend; on the JSON backend it is a filter
over the same `recordMatches` predicate every backend shares.

## Derived vs ground truth

One signal in v1 is a **derivation, not an observation**: the `scope.change`
shim infers "the agent reached for a capability it has never used before" from
first-use telemetry, because no agent-native capability log exists yet (gap
G5/A10). Every such action carries `detail.source=heuristic`, and central
copies that marker onto the record as `action_source`. The fleet view surfaces
it two ways — a `by_source` rollup per host and a dashed **badge** on each
affected recent event — so an operator triaging `carbonato.tool.scope_new`
knows it rests on inference, not a control-plane fact. An unmarked event has an
empty `action_source`: that means the producer made **no claim**, not that it was
verified. A future authoritative feed would set `source=agent` and the same
column would show it, with no change to central.

## Which pack produced this?

Every `behavior.rule_match` carries the pack and version that produced it, and
central rolls that up per host as `by_pack` (`"carbonato@7": 12`). This is the
operator-visible half of the **versioned, signed** pack story: a pin (see
[rules.md](rules.md)) guarantees *which bytes* a host runs and refuses to start
on drift, while `by_pack` shows *what those bytes called themselves* across the
fleet. The two together answer the question that matters during an incident —
"is every host actually running the pack I think it is, and the same one?" A
host still reporting `carbonato@6` next to a fleet on `@7` is pack drift, and it
shows up here without anyone diffing configs by hand. A record with no pack
(an `identity.drift`, or an event from a collector too old to set it)
contributes no bucket — the column shows `—`.

```sh
curl -s 'http://127.0.0.1:8080/api/v1/fleet?min_severity=high&recent=3'
```

## `GET /`

The same rollup as HTML: a table of hosts (id/name, event count, max severity,
last seen, breakdown by severity, by technique, and by signal source, and the
most recent events), colour-coded by severity, with a dashed badge on any event
whose signal was derived. It answers the operator's first question at a glance
and needs nothing but a browser.

The page shows the five most recent events per host; the endpoint's `recent=`
controls its own. That is the only intentional difference.

## Records

A `Record` is a denormalised, self-contained summary — a copy of the fields an
operator triages on, not the full `schema.Event`. It carries `when`, `host_id`,
`host_name`, `agent_root`, `event`, `severity`, `rule_id`, `technique`,
`action_type`, `action_source`, `path`, and a one-line `summary`. Keeping it a
copy means the store is not coupled to every future schema addition, and an old
record stays readable after a schema bump.

## See it live

The runnable demo prints the fleet rollup with no root, no daemon, and no
network — see [`../demo/README.md`](../demo/README.md). It is the fastest way to
confirm your build produces a working console:

```sh
cd demo && ./run.sh      # …then prints GET /api/v1/fleet
```

## Not in v1

A dashboard SPA. The plain page plus the JSON endpoint is enough to be useful;
polish is v2. Auto-remediation is out of scope for the whole tool — it is
alert-only, ever, without a separate opt-in design.

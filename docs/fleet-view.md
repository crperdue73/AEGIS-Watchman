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

## Enabling it

Two lines in `central.yaml`:

```yaml
records: /var/lib/agentids/records.json   # omit -> in-memory only (resets on restart)
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
      "recent": [ /* the newest Records for this host */ ]
    }
  ]
}
```

Hosts are ordered **worst-first** — by max severity, then by total — so the
console leads with what an operator has to look at. A `severity` floor becomes
an indexed range scan on the SQLite backend; on the JSON backend it is a filter
over the same `recordMatches` predicate every backend shares.

```sh
curl -s 'http://127.0.0.1:8080/api/v1/fleet?min_severity=high&recent=3'
```

## `GET /`

The same rollup as HTML: a table of hosts (id/name, event count, max severity,
last seen, breakdown by severity and by technique, and the most recent events),
colour-coded by severity. It answers the operator's first question at a glance
and needs nothing but a browser.

The page shows the five most recent events per host; the endpoint's `recent=`
controls its own. That is the only intentional difference.

## Records

A `Record` is a denormalised, self-contained summary — a copy of the fields an
operator triages on, not the full `schema.Event`. It carries `when`, `host_id`,
`host_name`, `agent_root`, `event`, `severity`, `rule_id`, `technique`,
`action_type`, `path`, and a one-line `summary`. Keeping it a copy means the
store is not coupled to every future schema addition, and an old record stays
readable after a schema bump.

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

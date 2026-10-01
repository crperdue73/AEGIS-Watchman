# ARCHITECTURE — AS BUILT (LAYER 3 TELEMETRY)

This is the data-flow of what actually ships, not the first-cut sketch in
`PHASE0-DISCOVERY.md` §0.3. That sketch was written before a line of code; this
one is written against the code and is kept true to it. Where they disagree,
this wins.

The audience is a contributor who wants to add a telemetry source or a rule
without reading every file. It answers three questions: *where do actions come
from, how do they reach a verdict, and what happens when a piece is missing.*

## The pipeline, in one picture

```
  ┌──────────────────── collector process (one per host) ────────────────────┐
  │                                                                          │
  │   SOURCES (internal/telemetry/*.go)          each implements:            │
  │   ┌──────────────┐                            Actions() ([]Event, error) │
  │   │ auditd       │ FileSource  ─┐                                         │
  │   │  execve →    │              │                                         │
  │   │  process.exec│              │                                         │
  │   ├──────────────┤              │      ┌────────────┐                     │
  │   │ eBPF         │ EBPFSource ──┼─────►│ MultiSource│  polls every child, │
  │   │  file.access │ (build-tag)  │      │            │  concatenates,      │
  │   ├──────────────┤              │      │            │  SKIPS a broken one │
  │   │ Docker       │ DockerSource┘│      └─────┬──────┘  (never fatal)      │
  │   │  container   │ (events)     │            │                           │
  │   │  .start      │              │            │ []Event{Action, Actor}    │
  │   └──────────────┘              │            ▼                           │
  │                                                ┌──────────────────────┐  │
  │                                                │ Engine (engine.go)   │  │
  │                                                │  · records action    │  │
  │                                                │    into the Timeline │  │
  │                                                │  · runs rules.Match  │  │
  │                                                └──────┬───────────────┘  │
  │                                                       │ []schema.Event   │
  │                                                       ▼ (rule matches)   │
  │                                          ┌────────────────────────────┐  │
  │                                          │ Spool (durable)            │  │
  │                                          │  append BEFORE any network │  │
  │                                          └─────────────┬──────────────┘  │
  └────────────────────────────────────────────────────────┼─────────────────┘
                                                           │ drain over mTLS
                                                           ▼
                                            ┌────────────────────────────┐
                                            │ CENTRAL SERVICE            │
                                            │  baseline / drift / fleet  │
                                            │  presentation (uid_map)    │
                                            └────────────────────────────┘
```

Two things this picture is honest about that the old sketch was not:

1. **Sources are peers, not layers.** auditd, eBPF, and Docker all implement the
   same one-method `Source` interface and sit side by side behind `MultiSource`.
   Adding a source is one file and one `Add` call; it is not a new layer.
2. **Every seam degrades.** No source is a hard dependency of the collector.

## The `Source` contract

The entire extension point is one method (`internal/telemetry/engine.go`):

```go
type Source interface {
    // Actions returns the batch of actions read on this poll. Implementations
    // are expected to be non-blocking or short-poll: the collector calls this
    // on its tick.
    Actions() ([]Event, error)
}
```

An `Event` is an `Action` (a `schema.Action`: a `Type` plus a free-form
`Detail` map) and an optional `Actor`. A source's whole job is: read whatever
raw telemetry it owns, translate it into actions, return them. It does not know
about rules, does not know about central, does not make verdicts.

### What each source emits

| Source | File | Input | Action type(s) |
|---|---|---|---|
| auditd | `telemetry.go` | audit log tail (execve records) | `process.exec` |
| auditd | `egress.go` | audit log tail (connect/bind/listen) | `net.egress`, `net.listen` |
| eBPF | `ebpf.go` (build-tag `ebpf`) | ringbuf (open/openat) | `file.access` |
| Docker | `docker.go` | `docker events` stream | `container.start` |

`container.start` is its own action type on purpose. "A new workload appeared
on this host" is a distinct, observable fact — collapsing it into
`scope.change` would overload the meaning of *an agent changing its own scope*
and make a rule unable to say which one it saw. The CARBONATO entry vector is
exactly "a container appeared with host access," so it gets a first-class type.

### Detail fields are the rule language

A rule matches on `Type` plus predicates over `Detail`. The predicates
(`internal/rules/engine.go`) are:

- `exe` / `exe_any` — the executable, by path or basename.
- `argv_any` — any argv element against globs.
- `path_glob_any`, `endpoint_glob_any` — globs over the `path` / `endpoint`
  detail fields.
- `detail_equals` — exact string match on a key.
- `detail_non_empty` — key present and non-empty (an AND over keys).
- `detail_non_empty_any` — at least one key present and non-empty (an OR).
- `detail_glob_any` — any of a key's globs matches that key's value (an OR over
  keys, then globs) — for composite fields like a container's `binds`.

The last two were added for the container rules and are **filter-only**: like
every predicate, they can reject an action, never force a match. (A predicate
that can only *reject* composes correctly with the AND of all other predicates;
one that could short-circuit to `true` would silently bypass them. This is not
theoretical — the first draft of `detail_glob_any` had that bug and the
quiet-on-ordinary-container test caught it.)

So the pattern for a new source is: emit an action type, put the fields a rule
needs to key on into `Detail`, and every existing predicate is immediately
available to match it.

## `MultiSource` — composition without a single point of failure

`MultiSource` (`internal/telemetry/multi.go`) holds a list of children, polls
each in order, and concatenates their events. Its one important property: **a
child that errors is logged and skipped, never fatal.** One blind backend must
not blind the healthy ones. This is the same principle the whole tool is built
on — a verdict tool that goes dark because its *second* source failed is worse
than one that runs with one eye and says so.

`Add(nil)` is a no-op, so build sites can call `Add` with a source that is
disabled (`NewDockerSource` returns `(nil, nil)` when its config block is off)
without a nil check.

## The Engine and the Timeline — how two halves of a chain meet

The `Engine` (`internal/telemetry/engine.go`) is the junction. On each poll it:

1. Reads actions from its `Source`.
2. Records each action into the **Timeline** *before* matching, so a rule can
   see the past.
3. Runs `rules.Engine.Match` for each action and returns the resulting
   `schema.Event`s (rule matches).

The Timeline is how the detector decides things no single action can decide. The
brief's canonical rule — *egress shortly after an identity file changed* — needs
two facts that arrive from different layers at different times. Layer 1 observes
file drift; Layer 2/3 observes the egress. `BehaviorEngine.ObserveDrift`
(`internal/collector/behavior.go`) feeds the former into the same timeline the
latter is matched against, so `prior_event: {class: identity.drift, within: 10m}`
becomes decidable. That is the seam where the file half and the action half
meet, and it is why `container.after_identity_change` can exist.

## Durability — the spool is the single submission path

A rule match is not shipped directly. It is **appended to the spool on disk
first**, and removed only once central accepts it (`internal/collector/`).
Consequences, all deliberate:

- A central outage costs *latency*, not *evidence* — whatever did not ship rides
  the next cycle.
- There is exactly one submission path (append, then drain), so there is no
  separate direct-send branch that could double-deliver an alert.
- The spool is bounded (`spool_max_bytes`, default 16 MiB); oldest entries drop
  past it.

The audit-log read offset has the same treatment: a **cursor** persists the
position so a collector restart resumes instead of replaying the whole log
(which would re-fire every rule the retained lines still match). The cursor path
is derived beside the spool when not set explicitly, so a deployment that
already configured durability gets restart-safe reading for free.

The scope shim's **seen-capability set** is persisted the same way (beside the
spool as `<spool>.scope-seen`): a restart must not re-report the host's
established listeners, endpoints, and executables as newly novel — a restart
that re-emits them is exactly the false burst that tunes a rule off. It is a
bounded cache, not a system of record; an unreadable file is discarded and
rebuilt rather than failing startup.

## Graceful degradation — the budgeted failure modes

Each optional source has exactly one expected failure and one log line for it.
None of them is an error condition.

| Component | Missing | Result |
|---|---|---|
| eBPF | no `-tags ebpf` build, old kernel, no `CAP_BPF` | one "unavailable" line; `file.access` rules stay silent |
| Docker | no daemon / no binary | one "unavailable" line; `container.start` rules stay silent |
| Docker enrichment | daemon unreachable at inspect time | event **still fires** with identity only (image/name), no risk flags |
| auditd | no log path configured | engine loads, runs idle (useful for validating a pack) |
| central | down / unreachable | matches queue in the spool, ship later |

The Docker-enrichment row is the one worth reading twice: a container the
collector cannot inspect is still a container that appeared. It is reported,
just without the risk flags. A detection tool never drops an observed event
because it could not enrich it.

## Layout

```
cmd/collector/        the collector binary (sources + spool + ship loop)
cmd/central/          the central service (ingest, baseline, fleet view)
cmd/manifest/         inventory manifest generator
internal/schema/      the versioned event/action schema (public contract)
internal/telemetry/   SOURCES: auditd, eBPF, Docker + Engine + MultiSource + Timeline
internal/rules/       rule pack loader, match predicates, timeline
internal/collector/   behavior engine (wiring), spool, config, shipping
internal/provenance/  Layer 2 supply-chain hashing
internal/central/     central-side store and presentation
internal/mtls/        mutual-TLS transport
rules/                shipped rule packs (carbonato.yaml)
docs/                 this file, event-schema, deploy guide, R2 eBPF design
```

## Adding a source, end to end

The shape is fixed, so a new source is mechanical:

1. One file in `internal/telemetry/` implementing `Actions()`.
2. A config block in `internal/collector/config.go` with a `Validate` and a
   constructor that returns `(nil, nil)` when disabled.
3. One `src.Add(...)` in `NewBehaviorEngine`, wrapped in the same
   log-and-continue switch the eBPF and Docker sources use.
4. Rules in `rules/*.yaml` matching the action type it emits.
5. Tests: the source in isolation, and a quiet-on-benign case proving it does
   not fire on ordinary input.
6. A deploy-guide section: what it needs, what it detects, what degrades.

Follow those six and the source is indistinguishable, structurally, from the
three that ship. That is the point of the `Source` seam.

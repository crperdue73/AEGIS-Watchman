# Event schema — `agentids.event/v1`

The wire format is the **public contract** of this project. Other people will
build on it, so treat changes as breaking. The machine-readable schema is
[`../agentids.event.schema.json`](../agentids.event.schema.json); Phase 0/1
Go structs live in [`../internal/schema/`](../internal/schema/).

## Envelope

Every event shares an envelope:

| Field | Type | Required | Notes |
|---|---|---|---|
| `schema` | string | ✅ | Always `agentids.event/v1`. Bump the version segment on breaking change. |
| `event` | enum | ✅ | `identity.drift` (L1) · `provenance.untrusted` (L2) · `behavior.rule_match` (L3). |
| `ts` | date-time | ✅ | RFC 3339, UTC. |
| `host` | object | ✅ | `{ id, name? }`. `id` is a stable host identifier. |
| `agent` | object | — | `{ id?, root? }`. Null when the collector cannot attribute. |
| `file` | object | — | Layer 1/2 file events. |
| `action` | object | — | Layer 3 behavior events. |
| `rule` | object | — | Present on rule matches. |
| `actor` | object \| null | — | `{ uid?, user?, pid? }`. Null if unattributable. |
| `severity` | enum | — | `info · low · medium · high · critical`. |

## Layer 1 — `identity.drift`

Fires when a hashed persona/skill/config file changes.

- `file.path`, `file.sha256_before`, `file.sha256_after`, sizes, and a truncated
  unified `file.diff`.
- `actor` populated when auditd attribution is enabled; otherwise `null`
  (drift is still detected — we just can't say *who* yet).
- `severity`: `high` by default for persona/identity files, configurable per
  baseline. An approved-change window suppresses the alert but still records it.

## Layer 2 — `provenance.untrusted`

Fires when an agent file is not accounted for by the approved, signed manifest,
or when its bytes no longer match the approved hash. Layer 1 asks "did it
change?"; Layer 2 asks "was this ever ours?"

- `file.path`, `file.sha256_before` (the **approved** hash, empty if the file is
  not listed), `file.sha256_after` (the **on-host** hash, empty if the file was
  deleted).
- `severity`: `high` for an unlisted file (the CARBONATO "drop a new skill"
  case — nothing has drifted, so Layer 1 alone is blind to it), `medium` for a
  listed-but-modified file, `low` for a listed-but-absent file.
- The approval manifest is an Ed25519-signed inventory of `path -> sha256` for
  one agent root (`agentids.manifest/v1`, see `internal/provenance`). It is
  offline-verifiable, so an operator can approve a workspace on a laptop and
  ship the manifest to the fleet. Because the source is a signed manifest,
  Layer 2 detection is **not evadable by rewriting the hash** the way a naive
  "trust-on-first-use" baseline is.
- Built and checked with `agentids-manifest` (`keygen` / `sign` / `verify`).

## Layer 3 — `behavior.rule_match`

Fires when a rule matches an observed action.

- `action.type` ∈ `process.exec · net.egress · file.access · scope.change`.
- `action.detail` carries rule-specific fields (e.g. endpoint, argv, path, scope).
- `rule` carries `{ id, pack, version }` so consumers can trace which rule fired.

## Versioning policy

- **Breaking** (rename/remove a required field, change a semantic): bump the
  version segment — `agentids.event/v2` — and support v1 for a deprecation window.
- **Additive** (new optional field): no bump; consumers must ignore unknown keys.
- `additionalProperties: false` on the schema is a v1 strictness choice; relax
  if adding optional fields downstream proves painful.

# Security Policy

watchman is a detection tool for the agent layer. It exists because the same
class of product it protects — agent runtimes, skills, MCP servers — is deployed
faster than it is audited. A security tool that hides its own attack surface is
a liability, so this file says plainly how to report a problem in watchman, and
what watchman does and does not defend against.

## Reporting a vulnerability

**Do not open a public issue for a vulnerability.** Public issues are readable
by everyone, including whoever would exploit the bug before a fix ships.

Report privately through **GitHub private vulnerability reporting**:
[Security tab → Report a vulnerability](https://github.com/crperdue73/AEGIS-Watchman/security/advisories/new)
(replace `crperdue73/AEGIS-Watchman` with the canonical repo URL if it has moved). This
opens a private advisory visible only to you and the maintainers, and gives us a
place to coordinate a fix and a disclosure timeline with you. If you cannot use
GitHub advisories, open a minimal public issue that says only *"I have a security
report and need a private channel"* — no details — and a maintainer will reach
out.

Please include, as far as you can:

- the component (collector, central, rule pack, schema, deploy assets) and
  version or commit;
- what you expected vs. what happened;
- the smallest reproduction you have — a config, a fixture event, or a rule;
- the impact you believe it has (what an attacker gains).

If you are unsure whether something is a vulnerability, report it anyway. We
would rather triage a non-issue than miss a real one.

## What counts as in scope

- **The collector and central binaries** (`cmd/`, `internal/`) — memory-safety,
  injection, authn/authz on the ingest and fleet endpoints, TLS handling.
- **The rule engine and seed pack** (`internal/rules/`, `rules/`) — a rule that
  can be made to fire wrongly, fail open, or be evaded by a benign-looking action
  is worth a report.
- **The event schema** (`watchman.event.schema.json`, `internal/schema/`) — a
  schema change that silently breaks a downstream consumer.
- **Transport and trust** (`internal/mtls/`, `internal/provenance/`) — anything
  that lets an unauthorized collector ship events, or a tampered manifest be
  accepted.
- **The deploy assets** (`deploy/`) — insecure defaults, generated cert material
  that is too weak, containers that run with more privilege than they need.

## Explicitly by design (not vulnerabilities)

These are choices, not bugs. Report them if you think the choice itself is
wrong, but they are not undisclosed defects:

- **Alert-only, no auto-remediation.** watchman never acts on what it observes.
  A detection tool that can *change* a host is a different risk class, and an
  explicit, separately-designed opt-in would be required before that changed.
- **No telemetry leaves the host except to the configured central endpoint.**
  This is a privacy property, not a leak.
- **eBPF `file.access` is off unless built with `-tags ebpf` on a capable host.**
  Absence of that signal without the tag is documented, not a gap.
- **Detection depends on auditd being configured to audit `connect`/`bind`/
  `listen`.** Without that, behavioral rules cannot fire — the README says so up
  front under *Getting started*.

## Known blind spots

watchman names what it does **not** cover rather than implying full coverage.
The current list is in the README under
[Known blind spots](README.md#known-blind-spots), each tied to a gap id in
[`PHASE0-COVERAGE.md`](PHASE0-COVERAGE.md). "The tool did not detect X" is only
a bug if X is in scope and the prerequisite telemetry was present; check that
list first when triaging a *missed* detection.

## How watchman protects itself

Operator-facing posture, so a deployer knows what they are trusting:

- **Mutual TLS on the collector→central hop.** Central requires and verifies
  every collector certificate (`RequireAndVerifyClientCert`); an unauthenticated
  client is refused at the handshake. TLS 1.2 minimum.
- **Least data, least privilege.** Collectors read local telemetry and ship
  normalized events; central stores baselines, records, and alerts. No
  privileged action is taken by either binary.
- **Durable state is written atomically, mode `0600`** (baseline and record
  files), so a crash cannot leave a half-written file that a later run trusts.
- **The spool is append-before-send**, so a central outage costs latency, not
  evidence.
- **One dependency, no CGO** on the default build — the smallest supply-chain
  and build surface we can honestly ship. A SQLite record backend exists behind
  an explicit build tag; Postgres is scoped but not shipped.

## Disclosure

We will acknowledge a report privately and work with you on a fix and a
disclosure timeline. This is a small maintainer team working in good faith; we
aim to respond promptly, but we cannot promise a fixed SLA. Please give us a
reasonable window to ship a fix before publishing details. Credit is offered on
request.

## Supported versions

watchman is pre-1.0: security fixes land on `main` and are released forward.
There are no maintained back-branches yet. Pin a commit and watch `main`.

# Rules — writing Layer 3 detections

Layer 3 matches what an agent **does**, not what a file looks like. A rule is a
small, versioned, shareable document — the Snort model: an `id`, a severity, a
`match` block over the action fields, and a description that ties it to a
technique where possible.

The engine is deliberately simple and deterministic: no regex compiled at match
time, no `eval`, no external DSL. A rule is a set of conjunctive predicates; it
fires when **every** predicate matches. That is auditable — you can read a rule
and know exactly what it does — and cheap on the hot path.

> Source of truth: [`internal/rules/rule.go`](../internal/rules/rule.go) (format +
> validation) and [`internal/rules/engine.go`](../internal/rules/engine.go)
> (matching). This page is the author's view of those.

## Where rules live

Rules are grouped into **packs**: one YAML file per pack, all rules under `rules:`.
The collector loads every `*.yaml` / `*.yml` in its configured `rules` directory
(non-recursive, sorted by filename for determinism) and evaluates them together.
An empty directory is not an error; an unparseable pack is.

The shipped seed pack is [`rules/carbonato.yaml`](../rules/carbonato.yaml).

## The rule document

```yaml
- id: carbonato.exec.shell_spawn          # required, namespaced: pack.area.name
  title: Agent process spawns a shell      # one-line human summary
  severity: medium                         # info | low | medium | high | critical
  technique: phase3/execution              # optional ATT&CK-style tag
  description: >                           # optional; explain the *why*
    A shell or interpreter under an agent root is the classic pivot.
  match:                                   # required, must have ≥1 predicate
    type: process.exec
    exe_any: [sh, bash, zsh]
```

| Field | Required | Notes |
|---|---|---|
| `id` | ✅ | Must contain a `.` (namespaced). Unique within a pack — a duplicate is a load error. |
| `title` | ✅ | One line, for humans and alert summaries. |
| `severity` | ✅ | One of the five levels below. Any other value is a load error. |
| `pack` | — | Defaults to the pack's `name`. |
| `version` | — | Defaults to the pack's `version`. |
| `technique` | — | Rides onto the emitted event and central's fleet rollup (group by technique). |
| `description` | — | Prose. Say what it catches and why it matters. |
| `match` | ✅ | The predicate block. An empty one is rejected — it would never fire. |

## The `match` block

Every predicate present must match (logical **AND**). Absent predicates are
ignored. Within a predicate that lists candidates, the rule of thumb is **OR** —
except `detail_equals` and `detail_non_empty`, which are AND over their entries.

| Predicate | Type | Fires when |
|---|---|---|
| `type` | string | `action.type` equals this exactly (`process.exec`, `net.egress`, `net.listen`, `file.access`, `file.write`, `scope.change`, `container.start`). |
| `exe` | string | The executable equals this (full path or basename). |
| `exe_any` | [string] | The executable equals **any** of these (full path or basename). |
| `argv_any` | [glob] | **Any** argument — or the executable itself — matches **any** glob. |
| `path_glob_any` | [glob] | `action.detail.path` matches any glob. |
| `endpoint_glob_any` | [glob] | `action.detail.endpoint` (`host[:port]` or URL) matches any glob. |
| `detail_equals` | {key: value} | **Every** listed `detail[key]` equals its value (string compare). |
| `detail_non_empty` | [key] | **Every** listed `detail[key]` is present and non-empty. |
| `detail_non_empty_any` | [key] | **At least one** listed `detail[key]` is present and non-empty. |
| `detail_glob_any` | {key: [glob]} | For **any** listed key, `detail[key]` matches **any** of that key's globs. |
| `agent_id` | string | `agent.id` equals this (restrict a rule to one agent). |
| `prior_event` | {class, within} | An event of `class` was seen on **this host** within `within` **before** the action — the temporal predicate (see below). |

`detail_non_empty` is an AND over keys; `detail_non_empty_any` is an OR. That
distinction is the shape a rule wants when several independent fields each
signal the same risk — e.g. a container is dangerous if it is *privileged* **OR**
*host-PID* **OR** *host-network*, not only if all three.

`detail_glob_any` exists for fields that carry a **composite** value: a
container's `binds` is a comma-joined list of host mounts, and a rule wants "any
bind that looks like the host root" without the telemetry layer exploding the
list.

## How globs match

Globs use shell-style [`path.Match`](https://pkg.go.dev/path#Match) semantics —
`*` and `?` do **not** cross `/`. Each candidate string is checked four ways, so
a rule can be written at whatever level of specificity is right:

1. **whole** — `path.Match(glob, value)`;
2. **trailing suffixes** — so `*/.ssh/*` matches `/root/.ssh/id_ed25519`;
3. **basename** — so `*id_rsa*` matches `/home/x/.ssh/id_rsa`;
4. **contains** — for globs shaped like `*needle*` with no interior `/`, so
   `*api.telegram.org*` still matches `api.telegram.org/bot123/send`.

The practical upshot: `*.ssh*`, `id_rsa*`, and `*/.ssh/*` all do what you expect
without knowing the exact path.

## Temporal correlation

`prior_event` is the one predicate that needs the per-host **timeline** rather
than a single action. It fires when an event of the given `class` was recorded on
the same host within `within` (a Go duration such as `5m`) **before** the action
under evaluation:

```yaml
  match:
    type: net.egress
    endpoint_glob_any: ["*api.telegram.org*", "*slack.com*"]
    prior_event:
      class: identity.drift        # or a raw action type like process.exec
      within: 5m
```

This is how correlation rules are built — "an outbound call within 5m of an
identity-file change". The collector feeds drift into the timeline *before*
Layer 3 evaluates actions in the same pass, so an event and the action it
correlates with can land together. `class` is required and `within` must be
positive; the pack fails to load otherwise.

## Severity

`info` < `low` < `medium` < `high` < `critical`. Use the lowest level that still
warrants a human looking: an informational signal should not page anyone. The
fleet view's `min_severity` filter and worst-first ordering key on this, so an
inflated severity quietly degrades the console.

## A worked example

A rule that catches a shell writing an SSH key to a non-standard location:

```yaml
- id: carbonato.exec.ssh_key_write
  title: Shell writes an SSH key
  severity: high
  technique: phase3/secret-access
  description: >
    Writing a private key from an agent context is credential staging. Catches
    the heredoc-to-keyfile shape the CARBONATO chain uses.
  match:
    type: process.exec
    exe_any: [sh, bash, zsh]
    argv_any: ["*id_rsa*", "*.ssh*", "*ssh-keygen*"]
```

Every present predicate must match: it must be a shell, **and** one of its
arguments (or its executable) must look like an SSH-key operation.

## The shipped pack

`rules/carbonato.yaml` — pack **carbonato**, version **7**, **20 rules** encoding
the CARBONATO-class "agent as insider" chain. Rule → severity → technique →
the action it keys on:

| Rule | Sev | Technique | Action | Catches |
|---|---|---|---|---|
| `carbonato.tool.scope_new` | high | `phase3/scope-expansion` | `scope.change` | a tool/capability not previously seen |
| `carbonato.secret.read_order_follows_constraint` † | critical | `phase3/secret-access` | `file.access` | credential read after a constraint-ignoring prompt |
| `carbonato.secret.read_after_shell` † | critical | `phase3/correlated-secret-access` | `file.access` | credential read within 2m of a shell spawn |
| `carbonato.secret.read_model_key` † | high | `phase3/secret-access` | `file.access` | a model-endpoint / API-key file read |
| `carbonato.exec.shell_spawn` | medium | `phase3/execution` | `process.exec` | a shell/interpreter under an agent |
| `carbonato.exec.ssh_key_heredoc` | high | `phase3/secret-access` | `process.exec` | shell touches an SSH key / known_hosts |
| `carbonato.exec.obfuscation` | medium | `phase3/defense-evasion` | `process.exec` | an encode/decode pipeline |
| `carbonato.tool.package_install` | medium | `phase3/execution` | `process.exec` | package install / executable download |
| `carbonato.persist.survives_session` | medium | `phase3/persistence` | `process.exec` | process outlives its parent session |
| `carbonato.persist.cron_or_rc` | high | `phase3/persistence` | `process.exec` | write to a persistence location (path named in argv) |
| `carbonato.persist.file_write` | high | `phase3/persistence` | `file.write` | write to a persistence location (path from the kernel, no argv needed) |
| `carbonato.infra.privileged_container` | high | `phase3/container-escape` | `process.exec` | privileged container launched with host mounts |
| `carbonato.net.new_listener` | medium | `phase3/command-and-control` | `process.exec` | agent process opens a listening socket |
| `carbonato.net.listener_bound` | medium | `phase3/command-and-control` | `net.listen` | a process binds a listening socket |
| `carbonato.exfil.egress_after_change` | high | `phase3/exfiltration` | `net.egress` | egress to a chat/C2 endpoint |
| `carbonato.exfil.egress_after_identity_change` | critical | `phase3/correlated-exfil` | `net.egress` | egress (chat **or chain RPC**) within 5m of an identity-file change |
| `carbonato.c2.blockchain_rpc` | medium | `phase3/command-and-control` | `net.egress` | egress to a public blockchain RPC provider (the Graphalgo C2 leg) |
| `carbonato.container.privileged_start` ‡ | critical | `phase3/container-escape` | `container.start` | a privileged, host-access container started |
| `carbonato.container.host_root_mount` ‡ | critical | `phase3/container-escape` | `container.start` | a container with a host-root / host-socket mount |
| `carbonato.container.after_identity_change` ‡ | critical | `phase3/correlated-container-escape` | `container.start` | a container started within 10m of an identity change |

**† / ‡ — rules a stock build cannot fire.** A rule fires only if a source emits
the action class it keys on, and two classes come from sources that are off in a
stock build:

- **† needs the eBPF source (`file.access`) — not built (P2.0).** The eBPF
  adapter is the only emitter of `file.access`; a stock build logs
  `eBPF file.access adapter unavailable, running auditd-only` and produces none.
  These fire the day the eBPF read half ships, and not before.
- **‡ needs the docker adapter (`container.start`) — opt-in, off by default.**
  The docker-events adapter is the only emitter of `container.start`; it is off
  unless `behavior.docker.enabled` is set. These fire once it is.

So on a stock build — auditd plus the default-on `scope.change` shim — **14 of
the 20 rules can fire, and only 1 of the 6 critical rules.** The five inert
criticals are the two `file.access` secret-reads plus the three `container.start`
rules. The collector does not leave that to arithmetic: at startup it prints how
many loaded rules cannot fire and names them (`N of M loaded rule(s) cannot fire
with the attached sources: ...`), so the host, not this table, is the source of
truth. The underlying blind spots are in the README
([*Known blind spots*](../README.md#known-blind-spots): G3's read half, and G4
when the adapter is off).

## Testing a rule

**Every rule carries a test.** Per [`CONTRIBUTING.md`](../CONTRIBUTING.md): a rule
without a fixture is a rumor. The convention, enforced by the pack tests in
`internal/rules/`, is two cases per rule:

- a **fire-test** — the attack action fires the rule; and
- a **quiet-test** — a benign lookalike does **not**, where one exists.

A new rule that never fires, or that fires on the benign case, fails the build.
See `internal/rules/carbonato_pack_test.go` and
`internal/rules/carbonato_benign_test.go`.

## What the parser rejects

`ParsePack` is strict on purpose — a malformed rule that silently never fires is
worse than a startup error. It refuses: a pack with no `name`; a pack with no
rules; a rule with no `id`, or an `id` without a `.`; a missing or unknown
`severity`; an empty `match` block; a `detail_equals` / `detail_glob_any` with an
empty key; a `detail_glob_any` entry with no globs; a `prior_event` with a blank
`class` or a non-positive `within`; and a duplicate `id` within a pack.

It also refuses **any unknown key** at any level: a misspelled predicate —
`argv_anyy` instead of `argv_any` — is a load error naming the bad key, not a
line quietly ignored. This matters because a silently dropped
predicate makes a rule under-match while the operator believes they are covered,
which is the failure mode this whole section exists to prevent. It also keeps a
pack forward-safe: an older binary refuses a pack that uses a predicate it does
not understand, rather than running a weakened version of it.

Every glob (`argv_any`, `path_glob_any`, `endpoint_glob_any`, and the values in
`detail_glob_any`) is compiled against `path.Match` at load, and a **malformed
pattern is a load error**, not a silent never-match — an unclosed class like
`[abc` fails the build instead of loading a dead rule.

`LoadPacks` additionally enforces that rule **ids are unique across the whole
loaded set**, not just within one pack: two packs that both declare
`carbonato.exec.shell_spawn` is a load error, so a stray or hostile pack cannot
ship duplicate alerts under an existing id.

## Verifying a pack without a collector

`agentids-rules` runs the same parser the collector runs, so a rule author can
check a pack the way they check code — no host, no auditd, no daemon:

```sh
agentids-rules verify                       # default path: ./rules
agentids-rules verify rules/carbonato.yaml  # one file
agentids-rules verify rules/ community/     # several packs/dirs as ONE set
agentids-rules list rules/                  # every rule, sorted by id
```

`verify` prints a summary (packs, rule count, severity breakdown, distinct
techniques) and exits non-zero on the first parse or validation error — the same
strict rules above, including unique ids across everything given on the command
line. A path that exists but holds no rules is an error, not a vacuous "ok".
`list` is the quick index of what a set defines. Wire `verify` into CI for
a community rules repo the way `go test` guards code.

## Publishing and pinning a pack set

Rule packs are versioned, shareable artifacts — the Snort model. That is exactly
why a fleet needs to record **which** packs it runs and confirm they are still
the ones it approved. A **pin file** is that record: one line per pack, carrying
its file name, its `name@version`, and the sha256 of the pack file's raw bytes.

```sh
# record the packs you approve (redirect next to them in the repo)
agentids-rules pin rules/ > rules/packs.pins

# check a working set against the pin — in CI, or before a deploy
agentids-rules verify --pins rules/packs.pins rules/
```

The shipped pack has a committed pin: [`rules/packs.pins`](../rules/packs.pins).
`verify --pins` fails, non-zero, on **any** drift, naming each one:

- **changed** — a pack is present but its bytes differ (digest mismatch);
- **missing** — a pinned pack is not in the set; or
- **unpinned** — a pack is present that the pin file does not account for.

The pin describes the same files a collector would load — the same
`*.yaml`/`*.yml`, non-recursive, sorted — so a pin cannot pass while the loaded
set differs. It is **byte-exact on purpose**, the way Layer 2 hashes an agent
file: the pin names the artifact, so a reformat that changes the bytes is a
deliberate re-approval, not a silent pass.

### Signing a pin

A pin on its own is a lockfile: anyone can write or edit one, so it detects
accidental or uncoordinated drift, not a determined forger. To trust a pack set
you did not build yourself — a community pack — sign the pin. Signing reuses the
Ed25519 keys Layer 2 already uses for agent manifests, and is a **detached**
signature over the pin file's exact bytes (kept out of the file, so signing never
changes the thing being signed):

```sh
# once: a signing key (the agentids-manifest keygen format)
agentids-manifest keygen -out ops.key

# sign the pin; writes packs.pins.sig next to it
agentids-rules sign-pins --key ops.key rules/packs.pins

# verify the signature AND the set against the pin
agentids-rules verify --pins rules/packs.pins --pubkey ops.key.pub rules/
```

`verify --pubkey` checks the signature **first** — comparing a set against a pin
whose provenance is unproven would be checking against the wrong document — and
then the drift. A tampered pin, a signature from another key, or a missing
`.sig` all fail non-zero. `--pubkey` accepts either the public key file or the
private key (the public half is derived), since operators often keep one file.
The signature path defaults to `<pins>.sig`; override with `--sig`.

### Enforced on the host

A pin is not only a CI artifact. A collector can be told to require one: set
`behavior.pins` (and, to authenticate the pin, `behavior.pin_pubkey`) in the
collector config, and it **refuses to start Layer 3** when `rules_dir` disagrees
with the pin — the same "look protected, detect nothing" reasoning as an empty
rules dir or a missing audit log. The signature is checked before the drift, so a
tampered pin is rejected as tampered, not merely as mismatched. See
[`collector-config.md`](collector-config.md) for the keys.

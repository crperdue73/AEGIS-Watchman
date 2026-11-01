# FINDING 2026-10-29f — the timeline is a 15-minute memory and nothing compares it to the pack

**Status:** Instrument added (`scripts/timelinemem.py`), mutation-proven twice. **No pack defect today** — 300s of headroom. The finding is that the headroom is unguarded.
**Instrument:** `scripts/timelinemem.py` · **Beat:** 656

## The bound nobody looks at

```go
// internal/rules/timeline.go:43-47
DefaultTimelineWindow = 15 * time.Minute
DefaultTimelineCap    = 256

// internal/collector/behavior.go:160
tl := rules.NewTimeline()      // the defaults, unconditionally, no config
```

Beat 653 fixed the correlation family's *windows* and beat 655 found those windows run on the *poll tick*. Both
fixtures hand the timeline a fresh, empty, amply-sized memory. Neither asked what the memory itself survives.

A correlation rule whose `prior_event.within` exceeds 15m **can never fire** — the observation it needs is dropped
on insert. That is a load-time property of the pack against a compile-time constant, and **no code compares them.**

## Measured today

| rule | within | timeline window | headroom |
|---|---|---|---|
| `carbonato.exfil.egress_after_identity_change` | 300s | 900s | 600s |
| `carbonato.secret.read_after_shell` | 120s | 900s | 780s |
| `carbonato.container.after_identity_change` | **600s** | 900s | **300s** |

The widest rule has five minutes of headroom. Writing `within: 20m` on that rule — a plausible edit, since 10m
already exists and "let's widen it" is the obvious tuning move — produces a rule that **loads clean, ships, and is
silent forever**. I planted exactly that and the instrument named it.

## The cap: a quiet signal dies on a busy host

The 256-observation cap is per host, evicting oldest first. The drift is always the oldest observation — it
happened first — so it is the *first* thing evicted when a host gets busy:

| host activity | retained | drift still seen |
|---|---|---|
| 10 actions | 11 | yes |
| 400 actions | 256 | **no** |

This is the timeline behaving as written, not a bug. But it means the correlation rules are most reliable on
**quiet hosts** — a precise inversion of the threat model, where the compromised host is the busy one. Nothing
warns, and the timeline cannot be resized without a rebuild.

Measured on real alertable behaviour: my eviction scenario uses only `process.exec` noise because that is what
`matches()` actually turns into rule matches. A host doing 256 *alertable* actions inside a correlation window is
already screaming about something else. **The cap is a real footgun only under sustained alertable load; it is not
a silent-failure channel in the way the window is.** Recording the honest size, not the scary one.

## Two findings colliding

`poll=300s` goes silent, and it is **not** eviction — it is beat 653's half-open interval meeting beat 655's clock
error. The action is stamped at the 300s tick; the 5m cutoff is 300−300 = 0; `Seen` is exclusive at the cutoff; a
drift at t=0 lands exactly on it and is excluded. Two individually-correct behaviours, a real correlation dropped.

This is the kind of thing that only appears when you stop testing one component at a time. Each fixture was right.
The system is not.

## Scope: the timeline is per-host, not per-agent

`Timeline` has exactly three fields — `window`, `cap`, `byHost`. Keyed by host id only, no agent, no root. On a
host running several agents, **agent A's identity drift satisfies agent B's `egress_after_identity_change`.** Every
correlation rule in the pack is worded about an agent. This is a genuine false-positive channel, unmeasured in
production, and I am not going to call it a defect without Dad deciding whether per-host is the intended
semantics — the timeline's doc comment says "per-host" deliberately.

## My own probe bug, since it is the third variation of the same mistake

My first agent-scope check searched the source text before `byHost` for `"agent"` and found it — in the doc
comment: *"the agent's identity file changed within N minutes."* It reported a live agent scope that does not
exist. **A probe that reads comments is measuring the author, not the code.** Now it parses the struct body into
fields and prints them: `['window', 'cap', 'byHost']`. Same species as beat 652 (a comment executed as argv) and
beat 655 (expected value derived from the thing under test) — I keep trusting prose as if it were data.

## Mutation proof

| mutation | result |
|---|---|
| `within: 10m` → `within: 20m` in the pack | **FAIL**, names `carbonato.container.after_identity_change` · rc=1 |
| `NewTimeline()` → `NewTimelineSized(30m, 4096)` | **FAIL**, names the hardwired construction · rc=1 |
| control | all checks green · rc=0 |

## Proposal (Dad's call, not a code change I land)

One validation at load: **refuse a pack whose widest `prior_event.within` is ≥ the timeline window.** Cheap, no
wire change, same philosophy as `Validate()` rejecting malformed globs — a rule that can never fire should be a
startup error, not a silent omission. Optional second: warn when `report.interval` ≥ half the retention window.

## Gate

**68 suites / 1474 checks / 0 failures.** Baseline rewritten (`timelinemem.py 11`).

# FINDING — 2026-10-31b: `net.listener_bound`'s only predicate cannot be false

**Status:** measured, **not fixed** — the fix is a rules edit; the *intent* is Dad's call (already open from beat 714).
**Severity:** low on its own; **diagnostic of a class.** This is the clean, provable instance of a defect I have so
far only described in prose.
**Instrument:** `scripts/listenpred.py` (re-derives the tautology from the emitter source and the pack; the Go
references below are what it reads).
**Written up:** beat 718. Complements `FINDING-2026-10-31-listener-bound-promises-a-distinction.md` (beat 714, prose).

## The rule

```yaml
- id: carbonato.net.listener_bound
  title: Process binds a listening socket
  severity: medium
  match:
    type: net.listen
    detail_non_empty:
      - "listen"
```

## The predicate is a tautology — proved from the emitter, not assumed

`net.listen` actions are produced by exactly one function, `AuditRecord.ListenAction()`
(`internal/telemetry/egress.go:125`):

```go
func (r *AuditRecord) ListenAction() (schema.Action, *schema.Actor, bool) {
	addr, ok := decodeSockAddr(r.BindAddr)
	if !ok || addr == "" {
		return schema.Action{}, nil, false        // <- no action at all
	}
	detail := map[string]any{
		"listen":  addr,                          // <- set on EVERY action that exists
		"syscall": r.Syscall,
	}
	...
	return schema.Action{Type: NetListen, Detail: detail}, actorOf(r), true
}
```

There are two paths: **return no action**, or **return an action whose `listen` key is a non-empty address**. There is
no third path.

`detail_non_empty` (`engine.go:148-151`) rejects an action when `detailString(action, "listen") == ""`. Against this
emitter, that condition is **unreachable**. So:

> `carbonato.net.listener_bound` is exactly equivalent to a rule with no predicates at all:
> `type: net.listen` and nothing else.

It is not wrong to *have* that rule — "any process bound a listening socket" is a legitimate medium signal. The defect
is that the match block **looks like it is filtering** and isn't. A reader (and, until tonight, me) sees
`detail_non_empty: ["listen"]` and concludes the rule distinguishes something. It does not.

## Why this matters more than one medium rule

The pack's rules are now built almost entirely from two kinds of predicate: **glob matchers** (which I have hammered
for four beats — over-match, under-match, inexpressible constructs) and **presence tests** like this one. A presence
test on a key the emitter always sets is not a weaker filter — it is **zero filter wearing the syntax of one.**

I have been treating "the rule has a match block, therefore it narrows" as an assumption. This is the first case where
I can prove the match block narrows exactly nothing. **The right response is not to fix this one rule. It is to check
whether any other rule is predicate-free by construction.**

## Checked tonight — the other presence-tests are sound

| rule | predicate | sound? |
|---|---|---|
| `net.listener_bound` | `detail_non_empty: [listen]` | **NO — tautology (above)** |
| `container.privileged_start` | `detail_non_empty_any: [privileged, host_pid, host_network]` | **yes** — `docker.go:139-146` sets each key *only* when the flag is true |
| `container.after_identity_change` | same + `prior_event` | **yes** — same keys, plus a real temporal predicate |
| `container.host_root_mount` | `detail_glob_any: {binds: ["/:/", "*/var/run/docker.sock*"]}` | **yes** — beat 614 already removed the one redundant glob, with measured witnesses |

So the class has exactly one live instance, and I found it by checking all four rather than assuming the pattern
repeated. **Three of four are fine** — recording that matters as much as the defect; "I audited the family and here is
the one bad member" is a different and more useful claim than "this pattern is everywhere."

## What is NOT established

- That the intent is to catch only suspicious listeners. `internal/rules/carbonato_benign_test.go:76-84` says the
  opposite in a **deliberate** fixture: a loopback listener from an operator's dev server is **expected to fire**, with
  the comment *"a listener is a listener … we do NOT special-case loopback in v1; a per-host allowlist is a later
  feature."* That is an honest product decision, taken knowingly, and recorded where a future maintainer will see it.
- That this should be narrowed. Same reasoning as beat 714: narrowing a medium rule can create false negatives, and a
  port allowlist is a fleet judgment.

**So the defect is narrower than beat 714's write-up implied, and cleaner:** the intent is fine and documented. The
match block simply doesn't say what the intent is. Two honest options, both one line:

1. **Drop the predicate** — `type: net.listen` alone. Now the rule's text matches its behaviour, and nobody reads a
   filter into it that isn't there.
2. **Make the predicate do work** — `detail_glob_any: {listen: ["0.0.0.0:*", "[::]:*", "*:4444"]}` etc. Beat 714
   measured this: wildcards separate correctly, but it does **not** separate 443 from 4444.

Option 1 is the honest minimum. Option 2 changes behaviour and needs an allowlist decision.

## Method note

I checked `own:` and `session:` keys first because my block parser reported them as match predicates on two rules.
They are **prose inside `description:`** — my regex `^\s{6}([a-z_]+):` matched indented text lines, not YAML keys at
map depth. **Fourth parsing error in four beats** (715 `grep -B8`, 716 double `- "*/.ssh/*"` anchor, 717 table-row
regex, 718 this). The pattern is consistent: I keep using a line-shaped tool on a structure-shaped question. The
correction is the same every time — **parse the block, then look at the field, not the other way round.**

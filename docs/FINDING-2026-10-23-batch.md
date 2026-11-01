# FINDING — 2026-10-23 — one clock per batch: the timeline's resolution is the poll interval

**Status:** measured, no pack change. `rules/` and `internal/` untouched.
**Instrument:** `scripts/batchprobe.py` · **Test:** `scripts/batchprobe_test.py` (39 checks, green)
**Beat:** 536, written 23:55 ET Friday

## The fact

`Engine.Matches` (`internal/telemetry/engine.go:83-100`) reads the clock **once per batch**:

```go
asOf := e.now()                                   // one read, before the loop
for _, ev := range events {
    e.timeline.Record(e.host.ID, asOf, ev.Action.Type, asOf)   // poll time, not event time
    matched := e.engine.Match(e.host, e.agent, ev.Action, ev.Actor, asOf)
}
```

A batch is everything accumulated since the last poll. Default `Report.Interval = 30s`
(`internal/collector/config.go:143`). `ev.Action` carries no timestamp to prefer, and the audit line's
timestamp is not preserved onto the struct — so the poll time is the only time the timeline ever sees.

This is not a coding error. It is a resolved design tradeoff with **three separate consequences**, and the
probe measures each rather than blending them into a complaint.

## A. Skew — one interval, per rule

An action at the head of a batch is stamped at the tail. Up to 30s:

| rule | window | skew | % of window |
|---|---|---|---|
| `secret.read_after_shell` | 2m | 0.5m | **25%** |
| `exfil.egress_after_identity_change` | 5m | 0.5m | 10% |
| `container.after_identity_change` | 10m | 0.5m | 5% |

**Measured, not alarming in itself:** one interval is always finer than every window, so skew alone never makes
a rule unwinnable. The 2m rule simply resolves its correlation to a quarter of its width.

## B. Batch-blind — within a batch the gap is always zero

Two actions in the same batch are recorded **at the identical instant**. A shell exec at t=0 and a secret read
at t=29s are indistinguishable from a read 1ms later. The rule still fires — zero is under 2m — so the error
direction here is **permissive**: within-batch temporal predicates degrade to "these happened in the same poll."

Worse for the semantics: an action that happened **before** its supposed cause, landing in the same batch, is
still judged correlated. Ordering is not merely coarse, it is **absent** within a batch.

## C. Boundary — a real correlation can be missed

This is the one with teeth, and it is the one I got wrong first.

A shell at t=1s and a read at t=119s are 118s apart — comfortably inside the 2m window. If they land in
different batches, the shell is stamped at its poll (30s) and the read at its poll (150s if picked up two ticks
later). **Recorded gap: 120s = exactly the window** — and `Seen` uses `o.At.After(cutoff)`, **strict**, so exactly
at the boundary is *excluded*. A true 1m58s correlation is lost.

Even the ordinary case — one poll of skew — shrinks the 2m rule's usable resolution to 60s.

So the error is **permissive within a batch and restrictive across a boundary**. Averaging them hides both.

## My first run was red, and the red was mine again

I asserted the worst-case gap **exceeds** the window and wrote `> 2.0`. It returns exactly `2.00`, so the
assertion failed on `2.00 > 2.0`. The honest statement is **reaches** the window, and the *reason it matters* is
specifically that `Seen`'s comparison is strict. Both facts are now separate assertions. Adjusting the model
until it agreed would have thrown away the sharpest part of the finding — that the failure sits exactly on the
boundary where a strict comparison excludes it.

## The invariant worth adding

`Report.Interval` is guarded against zero but **not against a large value** (`batchprobe_test` asserts that no
upper guard exists, and will fail if one is added, since that would change this arithmetic). An interval of, say,
5 minutes would make `secret.read_after_shell` **structurally unfireable** — its window would be under one
interval, so no two events could ever be recorded far enough apart *and* close enough, and it would fail silently.

**Proposed invariant, same family as 534a's window rule:**

```
Report.Interval < min(prior_event.within)      # per rule, or the rule cannot resolve its own window
```

Cheap to check at startup. The pack owner has three temporal rules and one number; this is a five-line guard.

## Reproduce

```bash
python3 scripts/batchprobe.py        # rc 0; prints skew per rule, batch-blindness, boundary case
python3 scripts/batchprobe_test.py   # 39 checks; asserts the one-clock premise against engine.go
```

## For the pack owner — updated ordered list

1. **R2.2 eBPF sensor** — three `secret.*` rules inert; `file.access` has no constructor (534b).
2. **Cap starvation** (535) — a noisy class evicts the drift a critical correlation needs.
3. **`binds` joined-string ordering** (533) — only the last mount is reachable.
4. **Clock resolution** (this finding) — 25% of the 2m rule's window is unresolvable; a correlation straddling
   a boundary lands exactly on `Seen`'s excluded edge.
5. **`cfg != nil` gate** (534b) — four container rules only see their fields when config was supplied.
6. **Glob spelling** — the four proved fixes.
7. **Two startup invariants** — `timeline_window >= max prior_event.within` (534a) and
   `Report.Interval < min prior_event.within` (here). Together they make the temporal layer's assumptions
   checkable instead of documentary.

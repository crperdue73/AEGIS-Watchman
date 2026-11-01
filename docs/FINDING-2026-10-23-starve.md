# FINDING — 2026-10-23 — the cap binds before the window, and it is per-host

**Status:** measured, no pack change. `rules/` and `internal/` untouched.
**Instrument:** `scripts/starveprobe.py` · **Test:** `scripts/starveprobe_test.py` (40 checks, green)
**Beat:** 535, written 22:45 ET Friday

## The question

`driftprobe` (534a) established the temporal rules are wired correctly end to end. It named two bounds it could
not measure without a live collector: the **15m window** and the **256-observation cap**, both per host. This
beat measures them. `Timeline.Record` is nine lines of arithmetic — it does not need a Go toolchain, it needs a
faithful model and a test that pins the model to the source.

## The model, pinned to the Go

```
cutoff := asOf - window
keep   := [o for o in list if o.At > cutoff]   # filter EXISTING list
keep.append(new)                               # then append, unconditionally
if len(keep) > cap: keep = keep[len(keep)-cap:]  # evict from the FRONT
```

`starveprobe_test.py` asserts this order **by index into the Go source**, so if `Record` is ever reordered the
model fails loudly rather than quietly simulating a program that no longer exists. That is the whole risk with a
simulation, and it is now closed.

## What it measures

A drift observation at `t0`. Every later observation on that host — **any class** — is one eviction step.

| load | drift evicted in |
|---|---|
| idle (0/min) | never |
| busy agent (5 exec/min) | 51 min |
| noisy agent (20 exec/min) | 12.8 min |
| **runaway loop (300 exec/min)** | **51 seconds** |

**The cap is per host, not per class.** 256 observations of *anything* — all `process.exec`, all noise — push
`identity.drift` out of the ring. `container.after_identity_change` then cannot fire, with 10 minutes still
unelapsed, inside a 15-minute window, and no error anywhere. That is the same silent shape as every other
finding this week, arriving by a different route.

## The two bounds, ranked

For a normal host the **window** binds first: 15m is the outer limit, and a 10m rule uses two thirds of it. For a
loud host the **cap** binds first, and it can bind in under a minute. So:

- **Window is a design question** — the 10m rule's fit inside 15m is worth a startup assertion
  (`window >= max prior_event.within`), already noted in 534a.
- **Cap is a defect-shaped question.** A single noisy class can starve a critical correlation. The fix is not
  tuning 256 upward — that just moves the threshold. It is **making the ring per-class, or reserving slots**, so
  a flood of one event type cannot evict another. Alternatively: record only the classes some rule actually
  correlates against, and let `process.exec` volume be irrelevant to `identity.drift` retention.

This is a pack/engine decision, not mine, and it is now stated with numbers instead of adjectives.

## A third gate the Go revealed, and my model got wrong first

`Record`'s comment says expired entries are a prefix because the slice is append-ordered. **That assumption
breaks on any out-of-order `ev.TS`** — feed one observation older than its predecessor and expired entries are no
longer a prefix, so the filter stops dropping them. Consequence is **memory retention, not a wrong alert**:
`Seen` applies its own `asOf` bound independently, so a stranded observation is never *seen*. I checked that
specifically rather than assuming it.

My first model run was RED on this and **the red was my bug, not the Go's**: I asserted that recording an
already-expired observation leaves the ring empty. It leaves exactly **one** entry — the one just appended — since
the filter runs on the *existing* list before the append. Reading `Record` line by line settled it. Both facts are
now pinned as assertions, including the "second record at a later `asOf` drops it" half.

## Also corrected in this probe

One assertion was vacuous — `t2.hosts["h5"] == [...] or True` asserts nothing, ever. Removed rather than left
looking like coverage. A test that cannot fail is worse than no test, because it counts.

## Reproduce

```bash
python3 scripts/starveprobe.py        # rc 0, prints the load table
python3 scripts/starveprobe_test.py   # 40 checks; asserts Record's operation ORDER against the Go source
```

## For the pack owner — the ordered list, updated

1. **R2.2 eBPF sensor** — three `secret.*` rules inert; `file.access` has no constructor (534b).
2. **Cap starvation (this finding)** — a noisy class evicts the drift a critical correlation needs. Per-class
   ring or reserved slots.
3. **`binds` joined-string ordering** (533) — only the last mount is reachable by whole-string globs.
4. **`cfg != nil` gate** (534b) — the four container rules only see their fields when the daemon supplied config.
5. **Glob spelling** — the four proved fixes.
6. **Window invariant** — `window >= max prior_event.within`, as a startup check.

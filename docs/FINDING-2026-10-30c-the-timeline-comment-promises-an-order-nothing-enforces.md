# FINDING 2026-10-30c — the timeline comment promises an ordering nothing enforces

**Status:** Instrument added (`scripts/pruneprefix.py`). Simulated from source; no Go toolchain.
**Instrument:** `scripts/pruneprefix.py` · **Beat:** 676 · **Family:** 656, 657, 658, 676

## Credit first

`Seen` is strict in the way that matters: it re-checks `o.At.After(cutoff)` **and** `!o.At.After(asOf)` on every
observation, so a stale entry cannot make a positive-window rule fire, and a *future* entry cannot either. That
independent re-check is the reason this finding stays small. Whoever wrote it did not rely on the timeline being clean —
they verified at the point of use.

## The assumption

`Timeline.Record` (rules/timeline.go:70-86), right before the eviction loop:

```go
// Drop expired (the slice is append-ordered, so expired entries are a
// prefix).
```

That parenthesis is a **claim**, and `Record` never checks it. It appends whatever `at` it is handed; no sort, no
monotonic guard. If an observation arrives with an `at` older than what is already in the slice, the slice stops being
ordered and the expired region stops being a prefix.

## Measured (window 900s, asOf 1000)

```
Record(h, 950, "live",  asOf=1000)  ->  [(950,"live")]
Record(h,  50, "stale", asOf=1000)  ->  [(950,"live"), (50,"stale")]
```

The 50s entry is already expired (cutoff 100) and it sits **after** a live entry. The prefix assumption is false, and
`Record` retains the expired entry.

## What it does NOT do — and I am not going to inflate it

I expected stale evidence to reach a rule. **It does not.** Measured:

```
Seen(h, "stale", within=900, asOf=1000) -> no match   (cutoff 100 > 50)
Seen(h, "stale", within=900, asOf=1060) -> no match   (cutoff 160 > 50)
```

The only `Seen` path that would match is `within <= 0` — "ever seen" (timeline.go:88, sets `cutoff = time.Time{}`) — and
that is **unreachable from a pack**: `rule.go:296` rejects `pe.Within <= 0` at load (beat 658). So for every rule a pack
can actually write, the stale entry is invisible. That guard is load-bearing, not incidental: mutation 2 below shows the
finding's severity rests on it.

## The real consequences (both small, both real)

1. **Memory.** The stale entry is never evicted by the prefix loop. `Prune` would collect it, but `Prune` only runs on
   the poll path and only for hosts still present.
2. **Cap eviction goes the wrong way.** `keep = keep[len(keep)-t.cap:]` trims from the **front by position**. With a
   backdated entry in the slice, the front holds *live* observations, so capacity pressure discards live evidence and
   keeps the stale one. Measured with `cap=3`: after a backdated arrival, `live1` is gone and `stale` survives.

## Reachability — the honest map

| call site | `at` | can order break? |
|---|---|---|
| `telemetry/engine.go:89` | `Record(host, asOf, type, asOf)` — **`at == asOf`** | No. Order is preserved by construction. |
| `behavior.go:377` | `ObserveEvent(ev, now)` — `at` derives from the **event** | **Possibly** — not traced to every producer. |

So on the engine path this cannot occur at all. On the `ObserveEvent` path it depends on whether an event's own
timestamp can run behind a previously recorded one — clock skew, a replay, or a backfilled record. **I did not trace
those producers, and I am not going to claim the leak occurs in practice.** That trace is the open question this beat
hands forward.

## Claim / not claimed

**Claimed (simulated from the exact source logic):** with a backdated `at` the slice loses sorted order; the expired
entry is not in the prefix; `Record` retains it; cap eviction trims by position and can therefore discard live entries
while keeping stale ones.

**Not claimed:** that any pack rule sees stale evidence — measured false for every positive `within`, and `within <= 0` is
rejected at load (658). **Not claimed:** that this leaks in practice on the engine path — there `at == asOf` by
construction. **Not claimed:** that this is Go behaviour — it is a transcription; no toolchain (642).

## Fix (Dad's call) — the comment is the actual defect

1. **Correct the comment** and make the cap trim filter-aware, OR
2. **keep the slice sorted on insert** so the invariant becomes true, OR
3. if the invariant is genuinely relied on, **assert it**: reject a non-monotonic `at` at the one call site that can
   produce it.

The eviction *filter* is already correct — it walks the whole slice. Only the cap trim and the comment assume order.
A comment that tells the next reader an invariant holds that nobody enforces is worse than no comment, because it stops
the next reader from checking.

## Mutation proof — including one of my own checks that could not fail

| mutation | result |
|---|---|
| false comment removed | **FAIL** — "Record's comment claims expired entries are a prefix" · rc=1 |
| `pe.Within <= 0` disabled (`false && ...`) | **FAIL** — "the loader REJECTS a non-positive within" · rc=1 |
| control | 15 rows green · rc=0 |

**My first version of the second check asserted the literal text `"pe.Within <= 0"` and stayed GREEN against that
mutant** — a check that could not fail, in an instrument written to catch checks that cannot fail. Fixed to parse the
guard and require it has no `&&` short-circuit, i.e. it tests the property rather than the shape of the expression
(beat 661's rule, third time this week). A second self-catch in the same check: a `(class, at)` tuple in the wrong order
crashed on the *control* path — the demonstration line had its own bug, found by running it.

## Gate

**89 suites / 1708 checks / 0 failures.** Baseline rewritten (`pruneprefix.py 15`).

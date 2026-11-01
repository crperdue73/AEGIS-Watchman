# FINDING — 2026-11-01e: the timeline cap evicts by arrival order, so a busy host forgets an old-but-fresh change

**Status:** measured, **not fixed** — the fix is small but it is in the correlation path that decides whether the
flagship rule fires. **Dad's call.**
**Severity:** medium-high — **false negative on the egress-after-change correlation**, on a busy host, silently.
**Instrument:** `scripts/timelinecap.py`.
**Written up:** beat 724. This is the "hard category" beat 723's census named and could not see.

## The setup

`Timeline.Record` (`internal/rules/timeline.go:67-88`) does two separate things to the per-host list:

```go
cutoff := asOf.Add(-t.window)
list := t.byHost[host]
// Drop expired (the slice is append-ordered, so expired entries are a prefix).
keep := list[:0]
for _, o := range list {
    if o.At.After(cutoff) { keep = append(keep, o) }
}
keep = append(keep, Observation{At: at, Class: class, Host: host})
if len(keep) > t.cap {
    keep = keep[len(keep)-t.cap:]        // trim from the FRONT
}
```

**The eviction is correct.** It is a full scan comparing each `o.At` against `cutoff` — the comment's "expired entries
are a prefix" claim is *not* relied on by the code, and `keep := list[:0]` is safe because `keep` never outruns `list`.
I checked this specifically because the comment invites you to assume a prefix-drop; the code does not do it. **Good.**

**The cap trim is not correct.** `keep[len(keep)-t.cap:]` drops from the **front of the slice**, which is
**arrival order**, not time order. That is only the same thing when every append carries a non-decreasing `at`.

## Why arrival order is not time order

There are exactly two feeders, and they disagree:

| feeder | what it stores as `at` |
|---|---|
| `telemetry/engine.go:89` | `Record(e.host.ID, asOf, …)` — **the poll clock**, one per batch |
| `rules/timeline.go:158` (`ObserveEvent`) | `at := ev.TS`, falling back to `asOf` only when `TS` is zero |

`ObserveEvent`'s comment says why it prefers `ev.TS` — a file/identity change should land at *its* time, not at the
collector's next tick. **That is right, and it is exactly what makes the list non-monotonic.** Feed a delayed file
event, a replayed backlog (beat 721: the zero-config collector replays from byte 0 on restart), or a slightly
out-of-order watcher notification, and an **older** timestamp is appended **after** newer ones.

Then the front-trim drops a *fresh* entry and keeps a *stale* one.

## Measured (faithful transcription of the two operations)

`CAP = 4` for exposition; the real cap is `DefaultTimelineCap`.

```
list: (900 identity.drift)                    <- the file change the rule needs
append 1010,1020,1030,1040 process.exec       <- four routine actions
=> (1010 exec)(1020 exec)(1030 exec)(1040 exec)
   identity.drift at 900 SURVIVED? False
```

**The drift event at 900 was 100 seconds old and the retention window is 900 seconds.** The eviction filter kept it on
every tick — verified: `cutoff` was 110/120/130/140, and `900 > cutoff` each time. **It was removed by nothing except
the cap trim, and only because it sat at position 0.**

**Consequence:** `Seen(host, "identity.drift", 5m, asOf)` returns **false**. The rule
`carbonato.net.egress_after_identity_change` — the CARBONATO ordering the whole product is built around — **does not
fire**, and the host looked busy, which is exactly when you would want it to.

**It is silent.** Nothing logs an eviction. `Len()` just returns a smaller number.

## What is NOT established

- **How often this fires in a real fleet.** It needs an out-of-order or delayed `ev.TS` *and* a host at the cap. **A
  quiet agent never hits it**; a busy one hits it exactly when its timeline is full of routine actions. I cannot size
  the rate without a deployment — I am claiming the mechanism, not the frequency.
- **That the replayed backlog is the common trigger.** Beat 721 established replay happens on restart for zero-config
  users; whether that dominates out-of-order watcher events is unmeasured.
- **That sorting on every append is the right fix.** Sorting the list each `Record` makes the trim correct but costs
  `O(n log n)` per event on the hot path. The cheap alternative — insert in time order (`sort.Search` + insert) — keeps
  the trim valid at `O(log n)` lookup + `O(n)` shift. **Both are reasonable; picking one is a performance decision, not
  a correctness one.** I am not making it here.

## Recommendation

1. **The trim's correctness currently rests on an invariant nothing enforces or documents.** Either enforce it
   (keep the list time-ordered on insert) or make the trim time-aware (drop the oldest by `At`, not by position). The
   second is a few lines and needs no ordering invariant at all. **That is my suggestion, because it removes the
   assumption instead of adding a rule to honour it.**
2. **Log or count cap evictions.** If a retention structure drops an entry inside its window, that is a fact the
   operator should be able to see — the same reasoning as beat 720's "correlation windows use poll time."
3. **Do not change it on a heartbeat.** The trim sits in the path that decides whether the flagship rule fires.

## Method note

**I went looking for the wrong bug and the comment nearly hid the right one.** The comment says "expired entries are a
prefix," so my first hypothesis was that the *eviction* used a prefix-drop and would break on out-of-order input. I
transcribed it, tested it, and **it held** — the loop is a full scan. I could have stopped there and written "audited,
sound." Instead I checked the *other* two lines in the same block, and the **cap trim right below it** has exactly the
assumption the comment describes.

> **The comment was describing the code's assumption accurately — one line away from where the code actually used it.**

That is a new variant of this week's shape. The previous five were *comments promising what the code doesn't do*. This
one is a **correct comment about a different line than the one that needs it**, which is harder to catch: a reader who
verifies the comment finds it true and moves on. **Beat 723's census would have scored this comment as accurate**, and it
is — it's just pointed at the wrong statement.

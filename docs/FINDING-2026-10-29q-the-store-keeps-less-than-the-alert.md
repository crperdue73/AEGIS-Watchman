# FINDING 2026-10-29q — the record store keeps strictly less than the alert it was built from

**Status:** Instrument added (`scripts/recordloss.py`). Third leg: beats 659–663 followed the *alert*, 664–665 the *pack*.
**Instrument:** `scripts/recordloss.py` · **Beat:** 666q (`p` was already taken by the beat-665 correction)

## What the store keeps

`Record` (eventstore.go:37-72) has 14 fields. Parsed from the struct body, not from prose:

```
When, HostID, HostName, AgentRoot, Event, Severity, RuleID, RulePack, RuleVersion,
Technique, ActionType, ActionSource, Path, Summary
```

That is a thoughtful field set — it answers *which host, which agent root, which class, which severity, which rule and
which pack version, which technique, which path*. The pack-version fields in particular are a good instinct.

## What it does not keep

**1. The event's own timestamp.** `When` is documented as *"when the record was retained (server time at ingest)"* and
is stamped from `s.nowTime()` in `retain` (service.go:86). **`ev.TS` — when the event actually happened on the host —
is stored nowhere.** Checked the struct, the struct body, and `RecordFromEvent`'s body: zero reads of `ev.TS`.

**2. The argv.** `ActionType` keeps `"process.exec"`. The command line that ran appears in **no** Record field, and
`summarizeEvent` renders `"<rule_id> (<action_type>) on <host>"`. Zero reads of `Argv`.

## Why the two together are the finding

Alone, (1) is a metadata gap and (2) is a detail gap. Together, the retained record cannot answer the two questions
anyone asks when an alarm fires:

- **When did this happen?** → only "when we received it"
- **What ran?** → only "process.exec"

Beat 662 measured that the **alert payload carries both** — the full event with `ts` and `argv` goes to the webhook. So
the store, the component whose entire job is durability, keeps **strictly less than the transient message**. Once the
alarm is delivered and the terminal scrolls, the evidence for `cat .ssh/id_rsa` is a record saying a rule matched, on
some host, at roughly ingest time.

## The third thing, which makes it worse

```go
// service.go:87-91
if err := s.records.Append(context.Background(), rec); err != nil && s.logger != nil {
    s.logger.Printf("central: record store append failed: %v", err)
}
```

**The error is logged and discarded.** `retain` returns nothing; `IngestActions` calls it as a bare statement (verified:
the only `s.retain(...)` line in the function is a plain call, no assignment) and returns success on every path (beat
660). So the collector is told **accepted** for events that were never stored. A failed store is invisible to the sender
and indistinguishable to the operator from a quiet host — the same shape as beats 663 and 664, in the one component
built to be trustworthy.

## Corollary: no intra-batch order (a reading, not a measurement)

`when` is computed once *outside* the loop, so every event in a batch shares one timestamp, and batches are per-Poll —
so retention time has tick granularity. Beat 655 found the correlation window runs on that same poll clock. The store
therefore cannot reconstruct intra-batch order either. **Flagged as a reading**; I did not measure a consumer that
depends on it.

## Claim / not claimed

**Claimed:** `Record` has no field carrying `ev.TS` or argv (struct body + parser both read); `When` is an ingest stamp
by its own doc comment; `retain` logs and drops append errors; `IngestActions` calls it as a statement and still returns
success.

**Not claimed:** that this was deliberate; that a specific investigation was hurt by it; or that adding argv is free —
**it is not free.** argv is unbounded input, and a store that keeps every argv inherits a size problem of its own. That
is why the fix below says *bounded digest*, not *keep the argv*. No store was written to, no record queried.

## Fixes (Dad's call)

1. **`EventTS` (`ev.TS`) on `Record`.** Cheap, fixed-size, and it is the field that makes "when" answerable. This is the
   one I would do first.
2. **A bounded argv digest** — e.g. the argv joined and truncated to N bytes plus a length — so the store can say *what
   ran* without becoming a log sink.
3. **Return the retain error** so a failed store is not reported to the collector as `accepted`. Same reasoning as beat
   660's error classes: a success code must mean the bytes landed.

## Mutation proof — and a mutant that failed to apply

| mutation | result |
|---|---|
| add `EventTS: ev.TS` to `Record` | first attempt **did not change the bytes** — my anchor `Summary: summarizeEvent(ev)` was a struct-literal field that does not exist (the real line is `rec.Summary = ...`, eventstore.go:376). `assert count == 1` caught it: **count was 0** |
| (retry) `rec.Event = ev.Event + "|" + ev.TS.Format(...)` | **FAIL**, "RecordFromEvent never reads ev.TS" · rc=1 · mutant printed before running |
| `retain` gains `error` return + `return err` (the proposed fix) | **FAIL** ×2 ("error logged not propagated", "no return value") · rc=1 |
| control | 13 rows green · rc=0 |

The failed first mutant is worth recording: had I written it without the count assertion, the gate would have gone green
and I would have reported "mutation-proven" for a mutation that never happened. **An unverified mutant and a passing
test look identical** — which is exactly the failure this whole project exists to catch, and the third time this rule has
earned its place this week.

## Gate

**79 suites / 1587 checks / 0 failures.** Baseline rewritten (`recordloss.py 13`).

## Postscript — my own index guard was too weak too

Writing this doc tripped `docsindex.py`'s beat-uniqueness check, and the trip was instructive twice over:

1. **Beat 666 was already claimed** by `FINDING-2026-10-29p-class-vocabulary-cannot-be-built-from-schema.md` — a correction
   of my beat-665 fix, which is **right and which I accept**: exporting the vocabulary from `schema` alone would have
   rejected the shipped pack (`class: process.exec` lives in `telemetry`, not `schema`). My recommendation from beat 665
   was wrong as stated; the corrected one stands in that doc. This doc is `666q`.
2. **The guard itself was too weak to say so precisely.** `re.search(r"Beat:\*\*\s*(\d{3}[a-z]?)")` *matched* the
   suffix, then `sorted(claims)[0]` **discarded it**, so `666` and `666q` collapsed into one "collision" and the check
   could not distinguish a real duplicate from a disambiguated pair. Fixed to sort by `(int(c[:3]), c[3:])` so the
   suffix survives. Mutation-proven both ways: setting this doc back to `Beat:** 666` → **FAIL "no two findings claim
   the same beat number"**; correct `666q` → green.

That is the week's recurring shape once more — the instrument was reading something *adjacent* to the fact. Here it read
the right regex and then threw the answer away in the next line. Worth noting that it caught a genuine problem anyway
(beat 666 was really taken), which is how a weak guard usually behaves: it fires, but for nearly the right reason.

# FINDING 2026-10-29i — the spool has two failure modes and no mode that both keeps everything and makes progress

**Status:** Instrument added (`scripts/spoolwedge.py`). **Hypothesis retracted mid-beat** — see §1. No code change landed.
**Instrument:** `scripts/spoolwedge.py` · **Beat:** 659

## 1. What I thought, and what I measured

I went in expecting to report: *"`Drain` breaks on the first error and cannot tell a 503 from a 400, so one
permanently-rejected batch wedges the queue **forever**, while `trim()` discards real events to make room for the one
batch that can never ship."*

**The second half is false, and it is false because I never ran it.**

`trim()` drops from the **head** (`recs = recs[1:]`, spool.go:251) and the undeliverable batch is **at** the head. So
once the spool hits its cap, the poison batch is thrown away and the queue **self-heals**:

```
cap=6, 20 cycles: 15 deliverable events discarded; poison batch evicted after cycle 6
```

Bounded data loss, then recovery. That is a materially different — and less alarming — finding than the one I had
drafted. **I found it by running my own simulation, not by reading the code, and the simulation contradicted my
sentence in the same command that would have printed it.** I had already written the confident sentence into the
script as a `print`. That is how a wrong finding gets into a doc: not by lying, by writing the conclusion before the
measurement.

## 2. The thing that is actually true

The two failure modes **trade off**, and neither is a queue:

| spool config | poison batch at head | outcome |
|---|---|---|
| `maxBytes` set (capped) | 503 for a while, then 400 forever | head evicted at cap → **silent data loss**, then recovery |
| `maxBytes <= 0` (uncapped, the zero-value default) | same | `trim()` no-ops (spool.go:223-225) → **permanent stop**, log grows without bound |
| transient outage (any config) | central unreachable | **correct**: keep everything, drain fully on recovery — verified, 3 cycles down then 3/3 shipped |

So: a temporary outage is handled *exactly right*, which is the durability guarantee working. A **permanent**
rejection is handled by whichever of "lose data" or "stop forever" the operator's config happens to select. Nothing
chooses deliberately, and nothing says which you have.

## 3. Why the code cannot choose

```go
// client.go:141-143
if resp.StatusCode < 200 || resp.StatusCode >= 300 {
    return fmt.Errorf("central returned %s", resp.Status)
}
// spool.go:116-120
if err := submit(ctx, ...); err != nil {
    log.Printf("collector: spool drain paused at %d/%d: %v", idx, len(recs), err)
    break
}
```

Every failure is one `error` with no class. `Drain` has no attempt counter, no age check, no quarantine file, no
status accessor — those are checked as *mechanisms* in the instrument, not as words in the source. **Neither `400`
nor `429` is distinguishable from `503`.**

## 4. Claim / not claimed

**Claimed:** capped spool = silent bounded loss on a poison batch; uncapped spool = permanent stop; transient outages
are handled correctly; no mechanism anywhere distinguishes permanent from transient.

**Not claimed — and this is the load-bearing gap:** **I have not observed central return a 400 to a collector match
batch.** I read the handler's validation and inferred it is possible; I did not send one. Everything here is a
reading of code paths plus a faithful transcription, and the "permanent rejection is reachable" premise is an
assumption, marked as one. If central is written to never reject a batch, the whole finding is latent.

## 5. Fix direction (Dad's call)

Classify the response: `4xx` except `408`/`429` is permanent → move the batch to a bounded quarantine file and
**continue draining**. Then the queue always makes progress, data loss is explicit and inspectable, and one bad line
cannot stop a host's telemetry. Second item: expose `Spool.Len()` and oldest-batch age in the status output so a stuck
queue is *visible* rather than inferable from a repeated log line.

## 6. The instrument mistake, once more

My first version asserted the trim **direction** as `kept[0] != "BAD"` — which doesn't test direction at all, so the
mutant (drop from the tail instead of the head) passed. Fixed by asserting against the Go source: `recs = recs[1:]`
present and `recs[:len(recs)-1]` absent. Mutant now rc=1.

And the tab count: **third time this week** my mutation sed guessed the indentation wrong (three tabs, actual two).
Each time `assert count == 1` refused to write the file, so no mutant silently ran against unchanged source. The
assert has now caught three bad mutants and zero bad checks; it is the most valuable four words in this repo.

## 7. Mutation proof

| mutation | result |
|---|---|
| `recs[1:]` → `recs[:len(recs)-1]` (drop from tail) | **FAIL**, "self-heal analysis is void" · rc=1 |
| `central returned %s` → adds a `permanent=` field | **FAIL**, "client returns a plain error, no status class" · rc=1 |
| control | 6 rows + 5 mechanism facts + trim analysis, green · rc=0 |

## Gate

**72 suites / 1509 checks / 0 failures.** Baseline rewritten (`spoolwedge.py 13`).

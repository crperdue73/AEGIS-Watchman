# FINDING 2026-11-04b — Option 1 alone is not a fix; it converts silent loss into a stall

**Status:** MEASURED · OPEN. No fix applied to the released tree. Dad's call, narrowed to two named options.
**Instrument:** no instrument script — a hand measurement in a scratch copy; the script that performed it is not published (it carries host-specific paths). Findings are reproducible from the method described below.
untouched): `go test ./...` with option 1 patched into `handleActions`; `Spool.Drain` poison-head probes;
`TestRefusalDoesNotSpin` (R3) as the counterweight. No script was added to the tree, because the change
under test is deliberately not in the tree.

Follow-up to **2026-11-02g** (spool `202 {"accepted":0}` is RECEIPT, not ACCEPTANCE). That finding
has been carried as a deliberate failing test, `TestHTTPActionsTotalDropIsNotAccepted`, waiting on
Dad's decision between two options. This beat measured both, and the answer is not what either
option described.

## Why this mattered this beat

The deliberate red had become an ordinary obstacle: `preflight.sh` stops at it during the default
test step, so `go test -tags sqlite ./...`, `-race`, and everything after are **unreachable in the
real gate**. A red carried as a marker had started blocking the guards that would catch other
things. That is why this moved from "documented, waiting" to "measured now".

## Measurement 1 — option 1 applied, does anything break?

Built option 1 on a scratch copy (`/tmp/dropfix`, the released tree is untouched): in
`handleActions`, after the ingest returns `n`, answer **400** with `{"accepted":0,"error":...}`
when `n == 0`, keep 202 otherwise.

**Result: `go test ./...` green in every package, including the deliberate red.** The fix is one
condition. Blast radius on the central side: zero.

## Measurement 2 — does the consumer survive it?

`internal/collector/spool.go` `Drain` loops over spooled batches and on error does:

```go
log.Printf("collector: spool drain paused at %d/%d: %v", idx, len(recs), err)
break
```

It **breaks and preserves the remainder**. It does not skip the bad batch. Measured on the scratch
copy with a submitter that rejects the first batch:

```
MEASURED: calls=1 shipped=0 err=<nil> remaining=3
STALLED: the rejected batch blocked every batch behind it
```

Three batches spooled (1 poison + 2 good). One call, **zero events shipped, all three still on
disk.** With option 1 in place, a batch central permanently refuses blocks the spool forever and
everything behind it never ships. **Option 1 alone trades silent loss for a permanent stall** — and
a stalled spool filling the disk is its own outage.

## Measurement 3 — my attempted companion fix, and the guard that stopped it

I added a quarantine path: classify the error as permanent (400/413/422) vs transient (5xx,
transport), `continue` past permanent ones, keep them on disk, keep draining. Measured:

```
MEASURED: calls=3 shipped=4 err=<nil> remaining=1
good batches DID ship past the rejected one
```

That looked like a fix. It is not, and `TestRefusalDoesNotSpin` (R3, refusal_test.go:111) caught it:

```
R3: refusing submitter called 2 times in one drain, want exactly 1
    (a tight retry loop would be a hot spin against a 400)
```

**The guard is right and my patch was wrong.** My `continue` is only safe if a rejection applies to
ONE batch. Nothing in the error string proves that. A central refusing everything — wrong auth
header, misrouted endpoint, deploy in progress, body-too-large on every batch — rejects batch 1, my
code quarantines it, retries batch 2, quarantines it, and spins the whole spool through N HTTP
requests per drain, every drain cycle, forever. R3 exists precisely to forbid that.

"Classify the error by string prefix" is also a bad foundation: `isPermanentReject` is reading HTTP
status out of an error message with `strings.Contains`. That is the same species as the
`substringNeedle` defects in the rules engine — a discriminator built on prose instead of structure.
A typed error carrying the status code would be the correct mechanism, and that is a change to the
`ActionSubmitter` contract.

## What is actually true, stated plainly

1. **The central-side fix is safe and cheap.** 400 on `n == 0` breaks nothing, closes the finding,
   and unblocks the gate. Zero risk on its own.
2. **The central-side fix alone makes recovery worse**, because the collector treats every non-2xx
   as "central is down, try later" and preserves the head of the spool.
3. **A proper fix needs the collector half too**, and it must distinguish per-batch rejection from
   central-wide refusal. That requires a typed error, not string matching. It also needs a policy
   decision: quarantine forever, quarantine with a cap, or drop with a loud count.
4. **The existing R3 guard is a genuine constraint on any option-1 fix**, and it was already in the
   tree before this beat. Anyone implementing option 1 without reading R3 will ship the spin.

## Recommendation to Dad

Do option 1 **and** the typed-error quarantine as one change, not option 1 alone. Concretely:

- `Client.SubmitActions` returns a typed `RefusalError{Status int, Body string}` for 4xx, and a
  plain error for 5xx/transport.
- `Drain` quarantines only `RefusalError`, and **stops the run** after the first quarantine so R3's
  one-call property holds while a total-refusal central is distinguished by a bisect on the next
  cycle (ship batch 1 only; if it refuses, stop).
- Quarantined batches go to a sibling file with a cap, never a silent `rm`.

If he prefers the smaller change now: **revert to leaving the test red is worse than it sounds** —
the red blocks `-race` and the tagged SQLite suite. The smallest honest unblock is to apply the
central-side 400 and accept that a total-refusal central stalls the spool, *documented*, until the
collector half lands. That is a real regression in recovery for a real gain in not losing data, and
it should be his call, not mine.

## Scratch state

`/tmp/dropfix` holds both experiments. **The released tree at `projects/carbonato-detection-tool/`
is unmodified by this finding** — no code changes to `http.go` or `spool.go` were applied there.
That was deliberate: option 1 is a semantic change to a public API, and 2026-11-02g has been
logged as Dad's call since beat 746.

## CORRECTION appended 2026-11-04 (beat 804) — "unreachable" was too strong

The section above implies the stall is a general property of `Drain`. A later measurement narrowed
it, and the narrowing matters enough to state separately rather than leave buried.

**Empty-batch stall: genuinely unreachable from this producer.** `Poll` (behavior.go) only appends
when `len(events) > 0`; `Spool.Append` returns early on an empty slice (measured: `append(0 events)
-> spool length 0`); `Client.SubmitActions` no-ops on an empty batch. Three independent guards. So
the shape that Callie's R4 describes cannot be produced here.

**All-unnamed batch: REACHABLE, and this is the real trigger.** `Service.IngestActions`
(service.go:215) returns `(0, nil)` for a NON-EMPTY batch when *every* event lacks `Event` — the
`droppedUnnamed` path. Measured with the real `Spool`:

```
MEASURED all-unnamed-head: calls=1 shippedEvents=0 drainN=0 err=<nil> remaining=2
REACHABLE STALL: one all-unnamed batch blocks the good batch behind it, permanently
```

One all-unnamed batch at the head of the spool blocks every good batch behind it, **forever**, under
option 1.

**Why this is the dangerous trigger rather than an obscure one:** an entire batch arriving unnamed is
not "the user sent garbage". It is what a collector/rule-pack version mismatch looks like, or an
engine regression in how `Event` is populated. That is precisely the deployment where you most want
the rest of the backlog to keep shipping — and precisely what option 1 alone freezes. The finding
should be read as *narrower but more serious* than the first write-up implied.

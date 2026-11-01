# FINDING 2026-10-29t — the wire cannot say "I missed something", and the fleet view omits the hosts that stopped

**Status:** Instrument added (`scripts/gapsilence.py`). Wire/console finding; schema changes are Dad's call.
**Instrument:** `scripts/gapsilence.py` · **Beat:** 669 · **Follows:** beat 668 (a split audit record is lost)

## The question that follows from beat 668

Beat 668 measured that `FileSource` silently drops an auditd record split across a poll boundary. The useful follow-up
isn't "fix that" — a reader bug is one bug. The useful question is: **would anyone ever find out?** That answer lives in
the wire, so it outlives any particular reader.

**It would not, by construction.**

## The envelope has no way to express a loss

`schema.Event` (schema.go:29-42), fields parsed from the struct body:

```
Schema, Event, TS, Host, Agent, File, Action, Rule, Actor, Severity
```

The collector→central message is the same on both ends (`collector/client.go:98-102`, `central/http.go:32-36`):

```
Host, Agent, Events
```

No sequence number. No batch id. No per-collector counter. No dropped/skipped count. No heartbeat. **A batch of zero
events and a batch that was lost are the same absence on this wire.** Central cannot notice a hole because it is never
told what number to expect.

Searched all of `internal/` for a mechanism (comments stripped — see the near-miss below): no sequence, batch-id,
watchdog, or heartbeat exists anywhere.

## The field that looks like a watchdog isn't one

`fleetHost.LastSeen` (fleet.go:17) is the tempting read. Three reasons it isn't liveness:

1. **It is an ingest stamp.** It advances from `rec.When`, and `retain` computes `when` **once, before the loop**, from
   `s.nowTime()` (service.go:86) — beat 666 measured this. It says when central *heard about* something, not when it
   happened.
2. **It only advances on a rule match.** Records are created by matches. An agent polled fine, shipping fine, that
   matches no rule produces **no records** — so it has no `LastSeen` at all, and is indistinguishable from a dead agent.
3. **It is only assembled from records.** The fleet view iterates records and keys `byHost` by record host
   (fleet.go:96-136). **A host with no records does not appear in the fleet view at all.** Stopping is what removes you
   from the list, so the console can only show the hosts that are talking.

That is beats 663 / 664 / 666 / 667 at fleet scale: failure and calm render as the same screen. Here the calm screen is
an *empty row* rather than a green light, which is arguably worse — a missing row looks like a host that was never
enrolled.

## Claim / not claimed

**Claimed:** the envelope and both `ActionsRequest` definitions carry no sequence, batch id, gap count, or heartbeat
(enumerated from struct bodies); no such mechanism exists in `internal/`; `LastSeen` is an ingest stamp advanced only on
matched-rule records; a zero-record host is absent from the fleet view rather than flagged.

**Not claimed:** that central *should* carry sequence numbers in v1 — though `schema.go:5` says additive optional fields
are explicitly fine, so the cost is low and this is a Dad call, not a unilateral change. **Not claimed: that batches are
currently being lost on the wire.** Beat 659 measured that the spool makes a *stopped central* a bounded, self-healing
loss. This finding is about **silent loss inside the collector** (beat 668's split records), which the spool never sees
because those records never became events. And I did not run a collector or a central; this is read from source.

## Fix (Dad's call — three independent pieces, any one helps)

1. **`seq` (monotonic, per collector) + `dropped` count on `ActionsRequest`.** Central can then see a hole *and* see the
   count it was told to skip. This is the one that makes beat 668's loss visible instead of theoretical.
2. **A periodic empty "poll heartbeat."** Then a healthy-but-quiet agent has a liveness timestamp that is not a rule
   match, and "no matches" stops meaning "no contact."
3. **A fleet row for a known host with no recent contact**, rendering silence as **SILENT** rather than omitting the row.

## A near-miss worth recording

First run went **RED** on two rows — both my own fault, both the same species: I grepped the word `missed` (and `gap`)
across the source **including comments**, and caught *"never a missed action"* and *"an operator who missed a page."*
Green prose, no mechanism. Fixed by **stripping comment lines before searching**, because a mechanism is an identifier.
That is the **eighth** prose-as-data bite in this project (652, 655, 658, 661, 662, 664, and now 669 twice). It keeps
happening because prose about a thing reads exactly like the thing — and the fix is always the same: search code, not
sentences.

## Mutation proof

| mutation | result |
|---|---|
| `Seq uint64` added to `collector.ActionsRequest` (**proposed fix 1**) | **FAIL**, "Host/Agent/Events only" · rc=1 |
| `Silent bool` added to `fleetHost` (**proposed fix 3**) | **FAIL**, "no expected/last_poll/silent field" · rc=1 |
| control | 12 rows green · rc=0 |

## Gate

**82 suites / 1618 checks / 0 failures.** Baseline rewritten (`gapsilence.py 11`).

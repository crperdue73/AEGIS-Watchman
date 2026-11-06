# FINDING — 2026-11-01u: the fleet view answers "which hosts are alerting" with a sample, and never says it's one

**Status:** source reads. **NOT observed. NOT APPLIED.** **Instrument:** `scripts/fleetcoveragecensus.py`.

**One line:** `GET /api/v1/fleet` lists hosts **derived from retained records**, not from the baseline store — so a host
that is enrolled, reporting, and simply *quiet* does not appear in the fleet view at all. Nothing in the response marks
the absence. **A zero and a silence are the same shape on the operator's console.**

## What I expected to find, and didn't

I opened this beat looking for the place where beat 733's 5000-record sample meets beat 739's 10-second deadline — a
page that could time out scanning its own store. **That is not what's here.** Every backend's `Query` filters *while*
scanning and breaks at `len(out) >= q.Limit` (`eventstore.go:164-173`, `:267-276`; SQLite does it in SQL at
`eventstore_sqlite.go:204-206`), so the limit bounds the work, not just the answer. There is no full-table scan behind
the cap. **This is the sound part, and it's a good piece of design: the cap is an early exit, not a post-filter.** I am
recording that as a measurement because a finding that only names breakage is half a finding.

## What is here

`buildFleetView(recs, recentN)` (`fleet.go:103-160`) builds its host list from `byHost := map[string]*fleetHost{}`,
populated **only** by iterating `recs`. `recs` is the query result. There is no second input. So:

- The host roster this view can ever show is the set of hosts **with a record inside the scan window**.
- `Store.ListBaselines` exists (`store.go:41`) — the full enrolled roster, documented as *"the fleet's identity roster is
  queryable (GET /api/v1/agents) instead of only readable by opening the store file on disk."* The fleet view does not
  call it. `/agents` does.
- A host enrolled ten minutes ago, verified, alerting nothing, is **absent from `/api/v1/fleet` and identical to a host
  that was never enrolled, or one that died.**
- `TotalEvents: len(recs)` (`:106`) is the count of records *scanned*, presented as a fleet statistic. Beat 733's
  complaint was that the view is a 5000-record sample; this beat's is that **the sample defines the population.** Truncation
  isn't only "some events are missing" — it can mean "a whole host is missing, and the view has no vocabulary for it."

## The one place absence IS expressed, and it's not the fleet view

`fleetHost.LastSeen` is `time.Time` with no `omitempty` (`:15`), so it always serialises — and a host can only *be* in
this list if it had a record, so `LastSeen` is never zero in practice. I checked rather than assumed. **Dead code with a
zero value that can never occur** — worth knowing, but not a finding on its own.

Compare `/agents` (`agentspage.go`), which renders from baselines: the roster shows every enrolled agent, and
`fmtTime` renders an em dash for a zero ModTime with the comment *"a zero ModTime is honest — 'we do not know' — not a
1970 timestamp."* **Someone in this tree has already reasoned about how to express "we don't know" on a page.** The fleet
view is the page that doesn't.

## Two smaller things, both real, neither dressing

1. **`/` and `/api/v1/fleet` can disagree about the same fleet** (`fleetpage.go:60` `buildFleetView(recs, 5)` vs
   `fleet.go:103` `buildFleetView(recs, 10)`): the page embeds 5 recent records per host, the JSON 10. Same function,
   two answers. Documented as "recent=<n> default 10" on the endpoint only. Deliberate for page width, probably — but the
   page shows `Total`, and a reader comparing the two will find the `Recent` arrays differ with no field to explain it.
2. **`handleFleet` passes `r.Context()` into `Query`** (`fleet.go:79`) while both the memory and file backends **ignore
   it** (`_ context.Context`) and the SQLite one **does not** (`QueryContext`). Finish reading beat 739 backwards: the
   ingest path's exemption is *deliberate* and *documented*. The read path has the opposite property — **the deadline is
   honoured by exactly one of three backends**, and nothing says which. A fleet query against SQLite can be cancelled
   mid-scan; against memory it cannot. Not a bug (the locks make the other two fast enough to not care), but **"cancelled"
   is backend-dependent behaviour with no documentation of that fact.**

## Options — listed, not picked

1. **Union the roster.** Take `ListBaselines`, start every enrolled host at zero records, then overlay the rollup. A
   quiet host then appears with `Total: 0` and a null `LastSeen`. Closes the zero/silence ambiguity properly. Costs one
   more store call per page load and a decision about `LastSeen` rendering.
2. **Surface the exclusion count.** Add `Scanned`/`StoreTotal` (beat 733's option 1) — still doesn't name absent hosts.
3. **Make `LastSeen` a pointer** so "no record" is expressible, then union (option 1's prerequisite).
4. **Document the limit.** One sentence on the endpoint: "hosts are derived from retained records; a quiet host does not
   appear." Cheapest honest minimum; leaves the operator's ambiguity intact.

Options 1–3 change the response contract ⇒ **Dad's call** (beat 724).

## What I am NOT claiming

- **Not** that any deployment is missing a host — Phase 2 is not deployed.
- **Not** that the 5000 cap makes the query slow. I checked the opposite direction and found the opposite of what I
  expected: the cap is an early exit.
- **Not** that ignoring `ctx` in two backends is a defect — it is a consequence of them holding everything under a lock
  and finishing immediately. The observation is that cancellation semantics are **undocumented and backend-specific**.
- **Not** a latency number, and no claim about how long `/` takes to render.

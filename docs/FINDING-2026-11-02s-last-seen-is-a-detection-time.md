# FINDING 2026-11-02s — "Last seen" is a detection time, not a contact time

**Status:** MEASURED · **LIVE at HEAD** (label only; the data path is correct) · **NOT APPLIED**
**Instrument:** `internal/central/lastseen_test.go` (X1–X5), `-race` clean.
**Origin:** Fern's 7497 — *a guard comparing two wall-clock stamps from the same clock, which cannot fail.*

## The short version

The fleet table has a column headed **"Last seen."** In an agent-monitoring console that reads as **liveness** — when
did this host last check in. Measured, it means something entirely different: **when did this host last produce a
detection.** A host reporting clean for a week shows a week-old "Last seen," and a host that has been off the network for
a week shows exactly the same thing.

The value is not wrong. The **label** invites a reading the value cannot support, and the failure mode is the one that
matters in a security console: an operator looking for dead agents will conclude a healthy host is dead, and a
decommissioned host looks identical to a clean one.

## Measured

| Test | Question | Measured |
|---|---|---|
| **X1** | is `LastSeen` a max, or the first record? | **Max** — correct even with oldest-first input. The `newest-first` premise is not needed for the arithmetic. |
| **X2** | is the newest-first premise backed? | **Yes** — `eventstore.go:102` states it as a store contract. The comment at `fleet.go:127` is redundant but honest. |
| **X3** | do events from one ingest share a timestamp? | **Yes** — 2 records, **1** distinct timestamp. `retain()` calls `nowTime()` **once per ingest**, so `When` is ingest time, never event time. |
| **X4** | can a clean ingest move `LastSeen`? | **No** — a clean ingest produces **zero** records, so nothing to stamp. `LastSeen` only advances on a **detection**. |
| **X5** | is the field documented honestly? | **Yes** — `eventstore.go:38`: *"When is when the record was retained (server time at ingest)."* |
| — | what does the page call it? | **`fleetpage.go:166`: `<th>...<th>Last seen</th>`** |

## Why this one is sharper than the last four findings

Findings `k`/`m`/`o`/`q`/`r` are all about a component tolerating, skipping, or mis-describing something. They're latent,
they need a hand-written input, and they fail loud. **This one is on the primary operator surface, at HEAD, today, with
no unusual input** — every fleet page has it. Its damage isn't a missed detection; it's an **inverted** reading: the
column most likely to be read as "is this agent alive" is the one column that answers a different question.

That's also why I'm not calling it a bug in the data path. X1–X5 say the pipeline is *correct* and the naming is the
problem, which means the fix is a decision about what the console should say — Dad's call, not mine.

## Options — none applied

1. **Rename the column to "Last detection"** and add a separate **"Last contact"** sourced from ingest attempts
   (including clean ones). This is the honest fix: two different questions, two columns, and the liveness one needs data
   that is currently discarded (X4: a clean ingest retains nothing, so its arrival time is not recorded anywhere).
2. **Rename only**, to "Last detection." One word of HTML, and it stops the misreading immediately. Cost: no liveness
   column at all — but there isn't one today either; it just looks like there is.
3. **Keep the label, change the value** to the newest ingest attempt regardless of events. Cheapest to compute, but it
   destroys the detection-time information and requires retaining records for clean runs.
4. **Document it.** Weakest — a tooltip under a column heading in a security console will not be read by the person
   scanning for dead hosts.

**Recommendation: option 2 now, option 1 when there is somewhere to store contact time.** Renaming costs a word and
removes a false reading; option 1 is the real feature and it needs a data-model change (X4), which is not something to
slip in under a finding.

## Mutation note — X4 is a measurement, not a guard

X4 asserts a clean ingest stores no records. **I could not make it go red.** Two mutants tried: deleting the call-site
`if len(events) > 0` (retain() still refuses on its own guard at `service.go:83`), and deleting both guards (the
for-loop over an empty slice still appends nothing).

So X4 measures a property the code **forces structurally** — you cannot append one record per event when there are zero
events. The guards are tidy-up, not the mechanism. I've written that into the test file, because *"the guard protects
this"* would have been a claim and I would have been wrong.

The finding above does not depend on X4 being mutatable: records were examined directly after a clean ingest and were
empty. What I am **not** claiming is that deleting a guard would change the outcome.

## Process note — two of my own claims were wrong first

1. I wrote the X2 log saying *"no store contract guarantees the ordering"* **before reading the interface**. The
   contract is right there at `eventstore.go:102`. I corrected the log and left the correction in the file rather than
   quietly editing it, because the wrong version was the more interesting sentence.
2. I also guessed `schema.Agent.Name`, `FileState.Mode`, `NewMemoryRecordStore()`, and `.List()` — **four invented API
   details in one fixture**, each caught by the compiler. This is the exact mistake I logged at beat 761 ("read the
   schema first, not last") and I did it again four beats later. The compiler caught all four, which is the argument for
   writing the fixture against real types instead of planning it in prose.

**Neither of those changed the finding.** X3/X4/the label were all measured after the corrections. That's six invented
API details across two beats, all caught by the compiler — which is itself the argument for writing fixtures against
real types first and explaining them second.

# FINDING 2026-11-02j — the agent id arrives, is used for attribution, and is dropped out of the baseline key

**Status:** MEASURED (real `Service`, real `MemoryStore`, real `Ingest`) · **NOT deployed · NOT APPLIED.**
**Instrument:** `internal/central/baselinekey_test.go` (P1–P5), `-race` clean.
**Builds on:** `2026-11-01v` (manifest keyed by root — beat 753 made it observed) and `2026-11-02i` (roster carries no
agent id — beat 754). This beat asks the question between them: **does central ever have the id at all?**

**One line:** yes — `Ingest(ctx, host, agent, inv)` takes `schema.Agent` and passes it to Drift, `ProvenanceEvents` and
`retain`. The identity is *received and used for attribution*. It is dropped at exactly two places: the **baseline
key** `AgentKey{HostID, Root}` and the **roster entry** built from `b.Key` + `b.Files`.

## Measured

| Test | Question | Measured |
|---|---|---|
| **P1** | does a new identity on an existing `(host, root)` get its own baseline? | **No.** Second agent ingests with `FirstSeen: false`; one baseline survives; the second agent's files are what's in it. `helper`'s baseline is gone and nothing said so. |
| **P2** | does the roster carry the id ingest received? | **No.** `buildAgentsView` builds from `b.Key` + `b.Files`; `agent.ID` never reaches the entry (which finding i measured as absent from the wire). |
| **P3** | does the id exist at the ingest edge? | **Yes.** `schema.Agent` carries `ID`; this is a **drop, not an absence** — which is what makes the fix small. |
| **P4** | control: are baselines keyed by root *alone*? | **No.** Two roots → two baselines. The collapse is specifically the missing agent id, not a coarser key. |
| **P5** | control: is identity ignored everywhere? | **No.** Drift events carry `agent.ID == "helper"`. Identity is used for events and unconsulted for baselines. |

**Mutation-proven, both directions.**
- Appending `agent.ID` to the root component of `AgentKey` → **P1 red by name**, with its own retirement message
  (*"if the key now includes the agent id, this finding is fixed — retire it, don't keep the pin green"*).
- Collapsing the key to the host alone → **P4 red by name** (*"the store is not keyed by root alone"*), proving P4 is a
  live control and not decoration.
- `service.go` md5 `687a7cf64d971799bac5cffb6de43c60` and `store.go` md5 `caabc48a31b154dbf922ae5c7929aad2` restored and
  verified identical after each mutant.

## Why this belongs with v and i, and where it differs

- **v** is the *manifest registry*: two agents on one root share an approval, last load wins.
- **i** is the *wire*: the roster names no identity, so a consumer can't audit the substitution.
- **j** is the *baseline store*: two agents on one `(host, root)` share one baseline history — and the second agent's
  ingest diffs against the first agent's files, with `FirstSeen` false.

Same shape three times at three layers, and the same missing key each time. **The fact that matters for the fix is P3:
central already holds the id.** Option 1 for every one of v/i/j is "put `agent.ID` in the key", and none of them
require new information at the ingest edge — only that the field already in hand is carried one layer further.

## Not claimed

- **Not** that any deployment has colliding identities — Phase 2 isn't deployed. **Latent.**
- **Not** that P1's collapse is silent *harm today*: with one agent per root the behavior is identical to correct.
  The finding is about what happens when there are two, which is exactly what the hygiene line exists to find.
- **Not** that the fix is one line. `AgentKey` is persisted (`String()`/parse round-trip in `store.go:50-64`) and
  `ListBaselines`, the roster, and the Postgres/SQLite stores all key on it ⇒ a schema migration, not a rename.
- **Not** that P5 is a mitigation. Attribution on the event path is a different field from baseline identity; a
  correctly-attributed event about the wrong agent's file is still about the wrong agent.

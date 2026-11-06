# FINDING 2026-11-02i — the roster says `ok` about an identity it cannot name on the wire

**Status:** MEASURED (real types, real JSON, real `rosterTrust`) · **NOT observed in a deployment · NOT APPLIED.**
**Instrument:** `internal/central/rosteridentity_test.go` (N1–N5), run under `-race`.
**Builds on:** finding `2026-11-01v` (root-keyed manifests) — beat 753 made that one *observed*. This one asks the
next question down the wire.

**One line:** finding v's option 1 was "key manifests by `(HostID, Root)`". Before proposing that key, I checked what
the roster payload already contains. It contains **HostID** and **Root** and **no agent id at all** — so a consumer
of `/api/v1/agents` is told `"trust": "ok"` about an identity it has no field to distinguish. The strongest evidence
that this matters is that **the roster cannot be used to audit the substitution the hygiene line exists to catch.**

## What is measured

| Test | Question | Measured |
|---|---|---|
| **N1** | does the roster entry carry an agent id? | **No.** Marshalled `agentRosterEntry` contains `host_id`, `root`, `files[].trust` and **not** `agent_id`. Positive control on the same JSON proves those fields are really present, so the absence is a real absence and not an empty struct. |
| **N2** | do two agents on one root render different verdicts? | **No.** Both resolve through the *one* manifest the registry returns for `/srv/x`; every approved path reads `"ok"` for **both** identities. |
| **N3** | can an agent inherit another's approval? | **Yes.** `helper`'s own approved file reads `"unsigned"` against the surviving `helper-copy` manifest, while `helper-copy`'s file reads `"ok"`. The losing manifest is internally consistent — it is simply **never consulted**. |
| **N4** | is this `rosterTrust` misbehaving? | **No.** Distinct roots get their own verdicts. The key is what collapses identities. |
| **N5** | is `"ok"` a content check? | **Yes.** Matching hash → `ok`; mismatched hash → `modified`. **This is the strongest thing in the tree and it is sound.** |

**Mutation-proven, both directions.** Adding `AgentID` to `agentRosterEntry` turns **N1** red by name, printing the
wire payload. Making `rosterTrust` presence-only (`case false && want != hash`) turns **N5** red by name. `http.go`
md5 `3ab3419836633afc9023c5a87f5722cf` restored and matches after both mutants.

## Why this is the useful half of finding v

v says the *registry* cannot tell two identities apart. This says the *payload* cannot either — and that is the
difference between "a bug in central" and "the hygiene feature cannot do its job through its own API". A consumer
holding this JSON has exactly two fields to tell `helper` from `helper-copy`: `host_id`, which is not what the
hygiene line asks about, and `root`, which is identical by construction in the case that matters.

## Not claimed

- **Not** that any deployment has colliding roots — Phase 2 isn't deployed. **Latent.**
- **Not** that `rosterTrust` is wrong. N5 measures that it is right, and it is the reason this finding is narrow:
  the content check works, the identity envelope around it does not.
- **Not** that adding `agent_id` fixes it. A field on the wire is necessary, not sufficient — central still has to
  *consult* it (v's finding: `manifest.AgentID` is carried, signed, echoed into verdicts, and consulted by no
  decision). N1 exists to make that first step visible, and to retire itself the moment someone takes it.
- **Not** a new copy of v. v is the keying; this is the wire. Same family, different door, and I am not inflating it
  by re-counting v's defect.

## Recommendation (options, still Dad's call)

1. **Add `agent_id` to `agentRosterEntry`** — cheapest first step, no format change, and N1 retires by design.
2. **Then consult it** (v's options 2/3): fail load on a root collision, or compare `m.AgentID` to the reporting
   agent. Without this, option 1 is a field nobody reads — the exact defect v documents.
3. **Label honestly in the meantime:** the roster column says `trust`; it is a *root*-scoped verdict. Option 4 from
   v (rename) composes with this cleanly.

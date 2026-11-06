# FINDING — 2026-11-01v: the manifest is keyed by root, so an agent identity is exactly as unique as its directory

**Status:** source reads + **now OBSERVED in `internal/central/manifestcollision_test.go` (beat 753)**.
**NOT APPLIED.** **Instruments:** `scripts/manifestkeycensus.py`, `internal/central/manifestcollision_test.go`.

**One line:** every lookup that decides whether an agent's identity is approved is keyed on `Root` alone. `manifest.AgentID`
is carried, signed, and never compared to the agent that is actually reporting. **Two agents with the same root are one
agent to everything that matters** — and the roster's trust column is the thing that would be asked.

## Why this one matters more than the last five

Beat 740's complaint was that the fleet view can't show a quiet host. This is the opposite shape: `/api/v1/agents` shows
everything, and the payload tells the operator **"`ok`"** — the word that reads best in the tree — based on a key that
cannot tell two identities apart.

The brief's differentiator is the agent-hygiene line already quoted in `http.go:100-104`: *"grep your fleet for someone
else's agent identity."* **This is the mechanism that decides that question**, and it is keyed by a directory name.
`schema.Agent` has exactly two fields, `ID` and `Root` (`schema.go:50-53`) — so `ID` is available at every call site and
is still not the key.

## The three places, all the same key

1. **`ForRoot(root)`** (`manifest.go:105-112`): `r.byRoot[normalizeRoot(root)]` — one manifest per root, one root per
   manifest. `Set(root, m)` overwrites. Two manifests for one root: **last load wins, silently.** No collision check in
   `NewManifestRegistry`, none in `Reload`.
2. **`ProvenanceEvents`** (`manifest.go:175-194`, the Layer 2 detection path) looks up `s.manifests.ForRoot(inv.Root)`
   where `inv` is the inventory the agent just reported. **The agent supplied that root.** It is a body field —
   beat 726/727/729's family, one level up: not "the host id is a wire field" but "the key that decides approval is a
   wire field."
3. **`buildAgentsView`** (`http.go:135`): `manifest, ok := s.manifests.ForRoot(b.Key.Root)` and
   `Trust: rosterTrust(manifest, f.Path, f.SHA256)` (`http.go:140`).

## What `rosterTrust` proves, and what it doesn't

`http.go:85-98`: nil manifest ⇒ `"unverified"`; path not listed ⇒ `"unsigned"`; hash mismatch ⇒ `"modified"`; else
`"ok"`.

**This IS a real per-file content check** — and it is the strongest thing in the tree, so say so plainly: the hash
compared is the one in the *stored baseline*, and the baseline is what central holds for `AgentKey{HostID, Root}`. For
`modified`, root-keying is harmless (the comparison is against the right agent's own baseline in every realistic case).

**The gap is `"ok"` on `unsigned`→`ok` transitions.** A file's trust label is a statement about *the manifest*, and the
manifest belongs to *the root*. So:

- Agent A (`id: helper`, root `/srv/x`) is approved. Its roster reads `ok`.
- Agent B (`id: helper-copy`, root `/srv/x`) — a second agent claiming the same directory, the exact thing the hygiene
  line exists to catch — inherits A's manifest. **Its roster reads `ok` too.** The path and the hash match because the
  files are the same files. Nothing in `agentRosterEntry` distinguishes the two but `HostID`.
- If B has its *own* manifest, `Set` overwrote A's at load time. A's roster now reports against B's approvals — `modified`
  and `unsigned` verdicts attributed to the wrong identity, **silently**. `NewManifestRegistry`'s contract is *"a bad
  approval list is a configuration fault the operator must fix, not something to silently skip"* (`manifest.go:34-37`).
  A root collision is exactly that, and it is not checked.

## What I checked before writing this, and it saved the finding from being wrong

- **Does `provenance.Verify` use `AgentID`?** No (`provenance.go:214-227`): it walks `hostFiles` against `m.Files`,
  nothing else. So the identity stamped on provenance events comes from the *reporting* agent (`agent` param), not from
  the manifest. The manifest's `AgentID` is written (`BuildManifest`, `:243-255`), signed, and echoed into
  `Verdict.AgentID` (`:60-66`) — and every *decision* ignores it. I read this before claiming "AgentID is unused";
  it is **carried everywhere and consulted nowhere**, which is a claim I can defend.
- **Fine to keep:** `normalizeRoot` (`:163-169`) trims only trailing slashes and maps `""`→`"/"`. It does not clean
  `.`/`..` or relative paths — beat 729 already filed that for the baseline key. **Not re-filed here**; the finding is
  about *uniqueness*, not *normalisation*, and I'm not going to inflate it by borrowing 729's defect.
- **Fine to keep:** `ForRoot` is nil-safe on a nil registry (`:106`), and `ProvenanceEvents` calls
  `s.manifests.ForRoot` on a possibly-nil `s.manifests` — Go allows this because a nil-receiver method checks `r == nil`
  first. Verified, correct, and worth recording, because reading it cold looks like a nil panic.

## Options — listed, not picked

1. **Key manifests by `(HostID, Root)`** and compare the manifest's `AgentID` to the reporting `agent.ID` at
   `ProvenanceEvents`. Rejects the identity substitution instead of approving it. Changes the manifest format ⇒
   migration.
2. **Fail load on a root collision** (`NewManifestRegistry` and `Reload`) — consistent with the existing "configuration
   fault" contract, no format change, catches the silent-overwrite half only.
3. **Compare `m.AgentID` to `agent.ID` when set**, falling back to root when the field is empty. Small, no format
   change, closes the substitution half.
4. **Rename the field and the lookups to say what they are** — `ForRoot`→`ForApprovedRoot`, roster column labelled
   "root-approved". Cheapest honest minimum: still root-keyed, just no longer implying identity.

Options 1–3 change the approval contract ⇒ **Dad's call** (beat 724).

## Now measured, not read (beat 753, M1–M6)

This finding was built on source reads. The strongest claim it makes — *last load wins, silently* — had never been
executed. It has now, through the real registry with real manifest files on disk:

- **M2 (the defect, pinned):** two unsigned manifests, `helper` and `helper-copy`, both rooted at `/srv/x`. Load
  returns **no error**; `byRoot` holds **exactly 1** entry; the survivor is `helper-copy` and `helper`'s approved path
  is **gone**. No error, no log line, no warning — the absent approval is indistinguishable from an agent that was
  never approved.
- **M4 (not an artifact of glob order):** with the files named so the later-sorted one is written first *and* second,
  the surviving identity still tracks last-load. So M2 pins **last-write-wins**, not `filepath.Glob` ordering.
- **M5:** `Reload` delegates to `NewManifestRegistry`, so a collision check added at startup would survive a hot
  reload rather than being bypassed by it.
- **M6 (control):** `/srv/n`, `/srv/n/`, `/srv/n//` all resolve to one entry — a trailing slash is **not** a collision,
  so the finding is not inflated by normalisation.
- **Mutation-proven:** adding a duplicate-root check to the constructor turns **M2 and M3 red by name**, and M3's
  failure message states the retirement condition. A comment-only edit (control) stays green; `manifest.go` md5
  unchanged after restore.

**What this does not change:** the finding is still **latent** — Phase 2 is not deployed — and the fix is still
Dad's call.

## What I am NOT claiming

- **Not** that any deployment has colliding roots — Phase 2 isn't deployed.
- **Not** that the hash comparison is wrong. It is right, and it's the part that makes this finding narrow: the
  content check is sound; the **identity envelope around it** is not.
- **Not** that `AgentID` is dead code — it is signed and propagated into verdicts. It is **unconsulted by every
  decision**, which is the precise claim.
- **Not** a second copy of beat 729. Same family, different key, different consequence. 729 was "the key mangles a
  relative path"; this is "the key cannot hold two identities."

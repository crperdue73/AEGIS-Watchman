# FINDING 2026-11-02w — "trust: ok" is scoped to a path, not to a host or an agent

**Status:** MEASURED · **LIVE at HEAD** · **NOT APPLIED** · **may be intended — see the brake**
**Instrument:** `internal/central/manifestscope_test.go` (C1–C6), `-race` clean.
**Continues:** the fleet/roster presentation cluster (s, t, u, v) — this one is about what `trust` *means*.

## The short version

The roster joins an approved manifest onto each baseline to produce a per-file `trust` verdict. The join is:

```go
manifest, ok := s.manifests.ForRoot(b.Key.Root)   // http.go:135 — ROOT only
entry.Manifest = ok
...
Trust: rosterTrust(manifest, f.Path, f.SHA256)    // http.go:85 — reads m.Files ONLY
```

Three measured consequences:

1. **`ForRoot` is keyed by root alone** (`manifest.go:111`) — every host sharing `/home/agent` gets the same manifest.
2. **`rosterTrust` never reads `m.Root`** — a manifest declaring `/home/totally-different` still yields `trust=ok` on a
   baseline at `/home/agent`.
3. **`rosterTrust` never reads `m.AgentID`** — swapping `agent-A`→`agent-Z` leaves trust unchanged.

So `trust: ok` answers **"is this content approved for this path?"** It does **not** answer "is this agent approved?" or
"is this approved on *this* host?" — which is what an operator reads it as.

## Measured

| Test | Question | Measured |
|---|---|---|
| **C1** | control — does the join work at all? | **Yes.** Matching file → `ok`; modified → not `ok`. The join is real, not vacuous. |
| **C2/C3** | two hosts, same path, manifest for one — does the other inherit? | **Yes** — `hostB` reports `trust=ok` from a manifest installed for `hostA`'s root. |
| **C4** | does the roster say *which* manifest produced the verdict? | **No** — no `manifest_path`, `manifest_host`, or `approved_for` anywhere in the response. |
| **C5** | is the manifest's `Root` field consulted? | **No** — manifest declares `Root=/home/totally-different`, baseline is `/home/agent`, verdict is still `ok`. |
| **C6** | is `agent_id` compared to the baseline? | **No** — `agent-A`→`agent-Z` changes nothing. |

**Mutation (the important one):** a **three-line** change making the join scope-aware —

```go
if ok && manifest.Root != "" && manifest.Root != b.Key.Root {
    manifest, ok = nil, false
}
```

→ C5 fails with *"If the join now compares m.Root, that is finding 2026-11-02w being addressed -- update this pin and
retire it."* Control green after restore. `http.go` md5 `3ab3419836633afc9023c5a87f5722cf` unchanged.

**The fix is small. The decision is not** — see below.

## My C5 brake was wrong, and correcting it made the finding sharper

I wrote C5 first as *"the manifest format has no host field, so this needs a format change."* **Wrong** — `provenance.Manifest`
has `Root string` at `provenance.go:36`, and `AgentID` at `:35`. I asserted the absence of a field without reading the
struct, in a test whose whole job was to be the brake on my own conclusion.

The correction moved the finding from "design limitation" to **"the scope fields exist and this code path doesn't consult
them."** That is a different and more actionable claim: not *we'd have to redesign*, but *the data is already there and
three lines would use it.*

## The brake that survives — is this even a defect?

`trust` might be **intentionally content-scoped**: "this exact file content was approved for this path, on some host,
under some agent." Under that reading the behavior is correct and the *word* is the problem.

But look at what the roster is for. `http.go:103` quotes Riley's line: *"grep your fleet for someone else's agent
identity."* The page exists to answer **is every agent's identity approved** — `http.go:133` says exactly that in the
comment above the join. If the question is *identity*, then approving an identity is approving **which files make up that
agent**, and two different agents can legitimately share `/home/agent` with different `SOUL.md` content — which C1's
hash comparison catches. So path+hash does carry real signal.

Where it genuinely leaks:

- **Same agent, 40 hosts.** An approval for host A silently covers all 40. If an attacker plants the approved content on
  a host that was never enrolled, it reads `ok`. *(Whether "enrolled" is a concept here is the design question.)*
- **A manifest naming another root or agent is accepted verbatim.** C5/C6 — the declared scope is decorative on this path.
- **No provenance.** C4 — the operator cannot tell *which* approval they're looking at.

## Options — Dad's call, none applied

1. **Enforce `m.Root` at the join** (the 3-line mutant). Rejects a manifest whose root doesn't match the baseline. Makes the
   existing field meaningful, costs nothing, retires C5. **Recommended as the minimum.**
2. **Key the registry by `(host, root)`.** The real per-host fix; requires deciding what "approved on this host" means and
   how a manifest gets enrolled for a host. Bigger, and Dad's call on intent.
3. **Rename the verdict.** `"content_ok"` instead of `"ok"`, or show `trust_scope: "path"`. Honest immediately, no
   behavior change — but it's a public JSON field, so it's a contract decision.
4. **Show provenance.** Add `manifest_root` / `manifest_agent_id` to the roster entry so the operator sees what was applied.
   Pairs with C4 and with the `Count` idea from t/u.

Option 1 alone doesn't fix the 40-host case; options 1+4 together make the current behavior *legible*, which may be the
right resting point if fleet-wide identity approval is the actual design.

## Answering C5's own question the honest way

I don't know whether per-host scoping is intended, and the comment at `http.go:132-134` talks about *the root* ("the
approved manifest for this root"), which suggests root-scoping is at least the mental model. So the finding stands as
**"the code does not enforce the scope its own comment and its own data fields describe"** — and the intent call is Dad's.

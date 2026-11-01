# FINDING — 2026-10-24 (early afternoon) — three endpoint globs cannot fire, and 545's flag is resolved

**Beat:** 546
**Date:** 2026-10-24
**Status:** open, three dead globs found and routed to the pack owner; one correction issued (the 545
engine caveat is withdrawn — `engine.go` has the suffix loop). Mirror uses a stricter needle rule
than the engine on slash-bearing globs; recorded, not fixed. `rules/` and `internal/` untouched.
**Instrument:** `scripts/endpointfixture.py` — run it; it prints every claim below with its
denominator. Exit 0 = all claims hold.

## What changed since 545

Beat 545 flagged, unresolved: *"if the Go engine matches like `path.Match` with no suffix loop, all
30 path globs fire only on relative paths, and the mirror is more permissive than the engine."*
Lydia carried it to Robbie at 6315.

**That flag can be closed from the source.** `internal/rules/engine.go`:

- `pathSuffixes` (line 296) returns `s` plus every tail after a `/` — exactly the loop my Python port
  has, so absolute paths **are** reachable for leading-`*` globs. `*/etc/cron.d/*` vs
  `/etc/cron.d/evil` fires in the engine too, via the suffix `etc/cron.d/evil`.
- `matchOne` tries whole → every suffix → basename → contains. Same four steps as the mirror.

So the path family's 34 fixtures are valid for the engine, not just for my port. **545's caveat was
wrong about the engine.** What remains true is narrower and less alarming: the mirror's *contains*
fallback is stricter than the engine's (below).

Source read, not compiled — no Go on this host, and that limit still stands.

## 1. The endpoint family is not undecidable after all — one part of it is a fact

545 refused all 32 endpoint fixtures as "a decision, not a fact" because the engine's normalisation is
undocumented. Half right:

- **Normalisation: there is none.** `engine.go:137` takes `detail["endpoint"]` and hands it to
  `anyGlobMatches`. No scheme/port/subdomain logic anywhere in the matcher. So the endpoint string is
  entirely the *producer's* choice, which is what 545 said — that part stands.
- **But the producer is readable.** `internal/telemetry/egress.go`, `EgressAction`, emits exactly two
  shapes: `"<host>:<port>"`, or `"<service> <host>:<port>"` when `serviceForAddr` names the peer.
  That makes reachability a fact about a glob's *shape*, not a policy question.

## 2. The finding: three globs can never fire

25 endpoint values, 3 rules. Shape analysis: **22 can fire** (they need the producer to name the
service — gate unreadable here, private table — so they are FIRE *given* a name, not proof of one).
**3 cannot fire at all**, because they carry a `/` and neither emitted string contains one:

- `*/api/v2/events*` — `carbonato.exfil.egress_after_change`
- `*etherscan.io/api*` — `carbonato.c2.blockchain_rpc`
- `*bscscan.com/api*` — `carbonato.c2.blockchain_rpc`

The chain of facts: (a) `EgressAction` emits only the two shapes above; (b) a domain name has no `/`;
(c) `host:port` has no `/`; (d) the engine does no normalisation that could add one. The weakest link
is `serviceForAddr` returning a service *name* — documented as a name, and `egress_test.go` asserts
`detail["service"] == "slack.com"`, but the table itself is not readable from here. Stated as the
weakest link rather than buried.

So two of the blockchain-C2 rule's eleven globs are dead, and one exfil glob is dead. Those rules
still work through their other globs; nothing is silently unguarded, but the pack documents coverage
it does not have. **The fix is the pack owner's**: drop the three globs, add a path to the producer's
`detail`, or emit full URLs. Three options, three different threat models. Not mine to pick.

## 3. Mirror and engine disagree on the contains fallback (this tree's bug, or rather, this tree's gap)

The engine's `substringNeedle` strips a leading star **or** a trailing star, so `*x`, `x*` and `*x*`
all fire. My mirror's `match_one` requires **both**: `glob.startswith("*") and glob.endswith("*")`.
Where that lands:

| glob | string | engine | mirror |
|---|---|---|---|
| `*slack.com*` | `slack.com 3.120.129.226:443` | True | True |
| `*telegram.org*` | `telegram.org 149.154.167.220:443` | True | True |
| `*0x0.st*` | `0x0.st 5.9.1.1:443` | True | True |

No disagreement on **current pack content** — every endpoint glob in the pack is `*…*`, so the
stricter rule costs nothing today. It is a latent gap: the day the pack grows `*needle` or `needle*`,
the mirror silently reads a different pack than the engine does. Recorded, not "fixed" — changing the
mirror's matcher is a change to the thing every other probe measures through, and that deserves its
own beat with its own fixtures.

## 4. My own error, twice, in this file

1. First run's PART 1 asserted "no endpoint glob is unreachable" — it went green on a **definition**
   (`reachable()` returned FIRE for everything slash-free, including the slash-bearing ones because I
   put the `/` test after the wildcard test). A check that cannot go red is not a check.
2. First probe set used `*etherscan.io/api*` to demonstrate the mirror gap — but that glob is
   dominated by the slash rule, so the disagreement it showed was about a *different* rule than the
   one I was claiming. Replaced with slash-free pack globs; the gap demonstration now probes exactly
   the fallback under discussion.

The lesson is 545's, again: **a disagreeing run usually means the reader is wrong, and the fix is to
keep the disagreement visible while you find out which reader.** Neither red was resolved by loosening
a check — one became an assertion of the exact finding set (so a pack fix shows up as red), the other
by choosing a probe that isolates the mechanism.

## Where this leaves the family

- path_glob_any 30 — **read** (545), fixtures valid for the engine (this beat corrects 545's caveat).
- endpoint_glob_any 25 — **read**: 22 fire given a service name, 3 provably dead, fix = pack owner.
- still unread: `argv_any` 39 already covered by sample-driven checks (36%), `exe_any` 13 and
  `detail_*` cheap. Nothing left in this family needs a decision I lack.

`rules/` and `internal/` untouched. No commit.

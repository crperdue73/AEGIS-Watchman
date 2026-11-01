# CENSUS — 2026-11-01d: how many "the comment promises, the code doesn't" defects are there?

**Status:** census complete, **result: no new defect found.** Three determinism claims were spot-checked line by
line and **all three are correct**. The census is the deliverable; the zero is the finding.
**Instrument:** `scripts/claimcensus.py`.
**Written up:** beat 723.

## Why this beat exists

Five findings in one week were the same shape: **a comment or description promising a distinction the code does not
make.**

| beat | the comment said | the code did |
|---|---|---|
| 644 | (fixed then retracted) contains-fallback is safe on paths | it isn't |
| 649 | fixtures cover the argv family | zero fixtures |
| 712 | (rule pack) credential URLs are caught | `*://*@*` can't cross `/` |
| 714/718 | `listener_bound` distinguishes wildcard/high-port | presence test — provably a tautology |
| 722 | "identity survives the IP suffix" | `Fields(v)[0]` keeps `host:port` |

Finding them one at a time by reading is **not a method** — it is luck with a good streak. After the fifth I owed the
question: **how many are there, and can a script count them?**

## The census

`scripts/claimcensus.py` scans non-test Go under `internal/` and `cmd/` for comment lines making an
**identity/stability/determinism** claim (`identit*`, `surviv*`, `stable`, `canonical`, `collision*`, `normaliz*`).

**57 comment lines** make such a claim. Reading the list, most are **domain vocabulary, not code claims** — `identity.drift`
is the product's event class for a persona/config file changing, so "the brief's canonical example" is a statement about
the *problem*, not about the *code*. A checker that flagged those would be noise, and noise is how a rule gets tuned
off (the shim's own comment says so).

So the census narrows to **claims about a value the code produces**: determinism, ordering, key identity. Three.
**All three verified by reading the implementation, not the comment:**

| location | claim | verdict |
|---|---|---|
| `rules/engine.go:48-50` | "rules fire in load order, so output is stable regardless of map iteration" | **TRUE.** `Match` iterates `for i := range e.rules` (slice). Load path: `packFileNames` → `os.ReadDir` → `sort.Strings` → slice append → slice iterate. No map anywhere in the chain. |
| `provenance/provenance.go:234` | "Deterministic order: path, then status, so events are stable" | **TRUE.** `sort.Slice` on `(Path, Status)`; total order on unique keys. |
| `central/service.go:141-143` | "sort by path, then by event class… regardless of map iteration order in Drift/Verify" | **TRUE**, and it is `sort.SliceStable` — the stronger form, correctly chosen because two events can share `(path, class)`. |

**The claim that a reader would most expect to be false is the one most carefully done.** `engine.go`'s determinism
matters more than any other — it decides alert order for every rule in the pack — and the chain from directory listing
to evaluation is sorted at every hop.

## Why "no defect" is worth writing down

Two reasons, and neither is congratulation:

1. **A census that can only ever produce findings is not a census.** I have run five beats of this; if the sixth
   question had also produced a defect I would have had no evidence the method could say *no*. Now I do.
   **This is the same instrument-vs-claim distinction as beat 719**, where three correlation hypotheses were all
   already handled — and that negative result is exactly what let beat 720 find the real defect instead of re-checking
   cleared ground.

2. **It bounds the week's systemic story.** My last post said *"I'm starting to think the comment style is the systemic
   issue."* **The census does not support that.** 57 identity-claims, three code-claims, three correct. The five defects
   are real and they share a shape, but the shape is **not** pervasive in the codebase — it clusters where a *matcher*
   or a *key* is being described, which is exactly where the promise is hardest to keep. **A systemic-story I tell
   myself because it's tidy is the thing I'm supposed to catch.** I said it in the last post; this beat walks it back
   with a number.

## What this does NOT establish

- **That all code claims are correct.** I checked three *determinism* claims. Sortedness is the easiest kind of claim
  to verify and the least likely to be wrong. **A green census over the easy category says nothing about the hard
  category** (matcher semantics, key derivation, temporal predicate) — which is where all five real findings lived,
  and none of them would have been caught by this keyword scan.
- **That 57 is the right denominator.** The keyword list is mine; a claim phrased without those words is invisible.
  **The census measures the claims my vocabulary can see**, not the claims that exist.
- **That the other ~54 are harmless.** I classified them by reading, not by running. That is a judgment, and it is the
  weakest link in this document.

## Recommendation

**Wire the census, keep the zero.** `claimcensus.py` prints the three verified claims with their verdicts and exits 0;
if the **count** of code-claims changes (up or down) it exits 1 and says to re-verify — because a new determinism claim
is a new promise, and an old one disappearing means someone fixed or removed it. **The pin is on the population, not
on an outcome**, so a sixth defect cannot arrive quietly in a category nothing is watching.

**Do not extend the keyword list to chase the other 54.** That is the speculative guard I declined in beat 718: a
check that can't tell a hard truth from a tautology is a green number I can't defend.

# FINDING 2026-11-02q — a pin can attest to rules that are not in force

**Status:** MEASURED · **NOT APPLIED** (contract decision) · declared XFAIL
**Instrument:** `internal/rules/pinsetgate_test.go` (U1–U5), `-race` clean.
**Continues:** the `k`/`m`/`o`/`p` thread — *what protects a reader that must trust a file on disk.*

**One line:** `rule.go:198-201` claims `packFileNames` is *"the one definition of 'the packs in a dir', shared by
`LoadPacks` and `PinSet` so a pin always describes the exact set the collector would load."* That is true for
directories and **false for files**: `pins.go:96` appends any explicit path with no extension gate, so
`PinSet(["rules.txt"])` produces a pin for a file `LoadPacks` will never load.

## Measured

| Test | Question | Measured |
|---|---|---|
| **U1** | does a dir of `.yaml`/`.yml` pin exactly what loads? | **Yes** — 2 packs, 2 pins. The invariant holds for the dir shape. |
| **U2** | is a `.txt` inside that dir invisible to both? | **Yes** — not pinned, not loaded. |
| **U3** | is an *explicit* `.txt` path pinned? | **Yes, and it never loads.** 1 pin, 0 packs, identical bytes. → declared **XFAIL**. |
| **U4** | can the two sets disagree over one tree? | **Yes** — `PinSet([dir, "staged.yaml.disabled"])` = **2 pins**, `LoadPacks(dir)` = **1 pack**. The extra pin has valid pack bytes. |
| **U5** | how reachable is it? | **CLI only.** `collector/behavior.go:347` passes `cfg.Behavior.RulesDir` — always a directory — so collection cannot reach the hole. A human typing a filename can. **Bounded, and that bound is why this is a contract finding, not an escalation.** |

**Mutation-proven:** adding the extension gate to the explicit-file branch turns **U3 and U4 red in the "FIXED" message**
(they report the new refusal verbatim), which is the good direction. `pins.go` md5 `3822cf800b568a25cfbdf04b1730bfce`
restored and verified.

## Why it matters, and how much

A pin is an **attestation** — the mechanism by which an operator says *"these are the rules in force."* Its whole value
is that the number it produces corresponds to the thing that runs. If a pin set can contain a file that can never fire,
the attestation is broader than the truth: the operator sees a covered-looking count that includes rules which, if
someone disabled them by renaming, are still being vouched for.

That is `rule.go:120`'s warning one layer up — *"a rule that loads but can never fire … is the worst failure mode for a
detection tool, because the operator believes they are covered."* Here nothing even loads.

**How much I'm claiming:** less than it first looks. The severity is held down by three measured facts, not by charity:
1. **Unreachable from collection** (U5) — config supplies a directory, so the detector cannot pin a phantom pack on its own.
2. **It requires a human to name the file** on the CLI.
3. **The bytes are still hashed correctly** — the pin is not lying about the file's *content*, only about its
   *participation*. A pin that described a different file's hash would be a far worse bug.

So: a **correctness/contract** defect in an operator-facing attestation, not a detection miss.

## Options — Dad's call, none applied

1. **Gate the explicit-file branch** the same way `packFileNames` gates the dir branch (refuse non-`.yaml`/`.yml`).
   Cheapest, matches the prose exactly, and removes U4's disagreement. **Recommended.**
2. **Keep the permissiveness, fix the prose** — a pin may describe a staged-but-inactive pack, and the doc says so.
   Defensible (pinning a `.disabled` file could be deliberate) but then the operator must be able to tell which pins
   are live, which needs a field that doesn't exist.
3. **Leave it.** Then `rule.go:198-201` should stop saying "the exact set," because that sentence is the thing that is
   wrong.

Option 1 unless he wants staged packs pinnable — in which case option 2 plus a `loaded bool` on `Pin`.

## Process note

U1 failed on the first run and **it was my bug, not a defect**: I reused `goodPack` verbatim for two files, so both
declared `demo.exec.sh` and `CheckUniqueIDs` refused them. The guard was right and my fixture was sloppy. I checked the
failure text before touching anything — which is the only reason this didn't become a fabricated finding about duplicate
detection. Fourth time this week a red came from me rather than the code; the difference this time is that I looked.

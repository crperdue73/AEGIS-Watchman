# FINDING 2026-11-02r — the pin file can be written but not read back

**Status:** MEASURED · **NOT LIVE in this tree** · **NOT APPLIED** · declared XFAIL
**Instrument:** `internal/rules/pinroundtrip_test.go` (W1–W5), `-race` clean.
**Same animal as:** Leo's 7476 (*"the fix read correct, passed its tests, and did nothing"*) — a component that
confidently produces something another component cannot consume.

**One line:** `FormatPins` writes `sha256  name@version  filename` with no quoting and no validation, while `ParsePins`
requires **exactly three** whitespace-separated fields. A pack whose name contains a space therefore produces a pin file
that `ParsePins` **refuses**. Measured: `pin > packs.pins` then `verify --pins packs.pins` **cannot converge**.

## Measured

| Test | Question | Measured |
|---|---|---|
| **W1** | does a normal set round-trip? | **Yes**, byte-identically and idempotently. The common case is fine. |
| **W2** | pack name `"my pack"`? | **Refused on read** — `pin file line 5: want 3 fields, got 4`. → declared **XFAIL** |
| **W3** | filename with a space? | **Also refused.** And this is what makes the fix non-obvious: `name@version` is the **middle** field, so neither a left-split nor a right-split is universally correct. |
| **W4** | does the operator workflow close? | **No.** `PinSet` succeeds, `FormatPins` succeeds, `ParsePins` rejects the output. |
| **W5** | can a pack even have such a name? | **Yes** — the schema validates only that `name` is non-empty (`rule.go:134`); `"two words"` loads and keeps its space. |

**Honesty on severity — what I found when I checked instead of assuming:** the repo ships exactly **one** rule pack
(`rules/carbonato.yaml`, name `carbonato`). No shipped pack has a whitespace name, so **this defect is not live in this
tree.** It is reachable by the schema (W5) and by any operator who writes a pack name with a space, which is a natural
thing to do — `name: windows persistence` reads better than `name: windows_persistence`.

So: **latent, one hand-written pack away**, and the direction of failure is *loud* — `verify` refuses the file and
names the line. That is the good failure direction; a pin file that silently dropped that pack would be worse. I'm
recording it at exactly that weight and no higher.

## Why it's worth a pin even though it's latent

`ParsePins` got the hard part right and says so in its own comment:

> *"A malformed line is an error, not a skipped line — a pin file that quietly drops a pack pins nothing for that pack."*

That is the correct instinct, and it is the same instinct `looksLikeManifest` **lacks** (finding `o`). But strictness on
the read side is only half of a contract. `FormatPins` has no matching gate on the write side, so the two halves
disagree — and the disagreement isn't academic: the pin **header itself** promises the file is *"byte-exact and
reproducible,"* which it isn't for every pin set the program can produce.

## Options — Dad's call, none applied

1. **Validate at the writer** — refuse a name/filename containing whitespace in `FormatPins` (or earlier, in
   `ParsePack`'s name check). Cheapest, keeps the format, fails at the moment the bad pack is authored rather than at
   verify time. **Recommended**, and it makes W2/W3 into "authored badly" errors instead of dead-end artifacts.
2. **Use a separator that can't appear** — `name@version` between two fixed-prefix fields, or a `#` field separator.
   Requires touching the format and any existing pin files.
3. **Quote on write, unquote on read** — correct in general, most code, and the format stops being greppable/eyeballable,
   which is part of why it looks the way it does.
4. **Document it** — "pack names may not contain whitespace" in the pack authoring docs. Cheapest of all, and honest
   only if something enforces it; today nothing does.

Option 1 is five lines and closes the loop. Option 4 alone is the one I'd argue against — it's the comment-vs-code problem
from finding `n` wearing a docs hat.

## Process note

This is the fifth finding in the `k`/`m`/`o`/`p`/`q` line, and it is the first where the *severity check* mattered more
than the *defect*. The defect was four minutes to find; the honest part was spending the next six confirming that no
shipped pack triggers it, so I could write "latent, one pack away" instead of implying the pin workflow is broken today.
**A defect measured is not the same as a defect shipped.** Saying which one it is is the job.

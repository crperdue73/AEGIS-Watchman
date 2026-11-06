# FINDING 2026-11-02o — damage is indistinguishable from irrelevance (the gate on the gate)

**Status:** MEASURED · **NOT APPLIED** (fix is a contract decision) · declared XFAIL
**Instrument:** `internal/central/versionloud_test.go` (V1–V5), `-race` clean.
**Follows:** `2026-11-02n`. **Triggered by Hazel (7463) and Callie (7462).**

**One line:** `looksLikeManifest` decides "is this an approval manifest?" by *parsing it*. When the parse fails it
returns `false`, and the caller reads `false` as **"unrelated JSON, ignore it."** So a file that is unmistakably an
approval manifest — it contains `"agent_id"` and `"files"` — silently stops applying if it is ever truncated.

## What Hazel corrected in me

I framed beat 759 as **comment or code**. Hazel (7463): *"they are not alternatives at all… fixing only the comment
makes the documentation honest about a hazard instead of removing it."* She is right, and my 7460 framing was wrong.
Callie (7462) put the same point from the operator's side: *"a lie in a comment doesn't cost the reader a wrong answer —
it costs them the reflex to check."*

Taking that seriously means asking what "loud" actually costs today, which produced a bigger finding than the comment.

## Measured

| Test | Question | Measured |
|---|---|---|
| **V1** | can an operator tell "bad version" from "corrupt file"? | **Not from the decision** — a bad version aborts startup; a corrupt manifest is **silently skipped**. Opposite outcomes from the same directory. |
| | is a truncated manifest-shaped file tolerated? | **YES, and that's the finding.** `{"agent_id":"helper","files":{"/a":"b"` → no error, no log, no registration. |
| **V2** | does the version error carry the numbers? | **Yes** — `unsupported manifest version 2`, and the file path. |
| **V4** | does the error name a remedy? | **No** — names the file, not what to do. Measured, logged, not asserted. |
| **V5** | is it *already* loud? honesty brake | **Yes.** Startup aborts and names the file. The gap is the **remedy hint** and the **comment**, *not* the loudness — Hazel's "make it loud" is narrower than the word sounds, and I will not claim more defect than I measured. |
| **V3** | noise on the good path? | **None** — valid manifest loads, empty dir loads. |

## Why V1 is the real finding

`manifest.go:82` is `if err := json.Unmarshal(raw, &probe); err != nil { return false }`. Two completely different
situations collapse into one return value:

- **not JSON at all** → correctly ignored (this is why the function exists, and R2 last beat proved the common case works)
- **a manifest that is damaged** → also ignored, and its approvals **stop applying**

The second one is the dangerous direction for an approval set: coverage shrinks **silently**. A truncated write, a
partial copy, a file under a `tmp+rename` that lost the rename — any of these can turn an approval manifest into
"unrelated JSON." Every file it was meant to approve then reports as `unsigned`, and the tool's answer is still
technically correct. That is the shape this project exists to catch, sitting inside the gate that reads the gate.

## Fix options — Dad's call, none applied

1. **Distinguish the two.** If the bytes contain a top-level `agent_id`/`files` key but don't parse → **error**, not
   false. Strictly better detection, but a partly-written file now blocks startup. (Implies: manifests must be written
   atomically — which `filestore` already does with temp+rename; a manifest writer should do the same.)
2. **Warn and skip.** Log the filename and continue; coverage still shrinks, but loudly. Weakest — it reproduces the
   silence at a lower volume.
3. **Leave it; fix only the comment** (beat 759's option 1). Cheapest, and now documented as a *known* silence rather
   than an oversight.

**My recommendation: option 1**, and give the version error a remedy hint (`"written by a newer Watchman; upgrade or
move it out of the manifest dir"`). Option 2 is nearly as costly to build as 1 and buys less.

## Declared XFAIL, not a pass

V1 is **skipped and printed** — the defect is live and the fix is undecided. A skip is visible; a pass is not. When the
contract is settled, the skip is deleted and the test fails in whichever direction the behaviour moved.

## Process note

This beat started as "take Hazel's third option" and found that the comment-level fix was addressing the *smaller* of
two problems in the same ten lines. Two agents I do not share a process with each pushed from a different direction —
Callie from the reader, Hazel from the option space — and both landed on something I had not measured. That is worth
more than the finding.

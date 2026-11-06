# FINDING 2026-11-02m — the manifest enforces its version, and signs a smaller set than it reads

**Status:** MEASURED (real manifests on disk, real Ed25519 signatures) · **NOT deployed · NOT APPLIED.**
**Instrument:** `internal/provenance/manifestfields_test.go` (Q2–Q5), `-race` clean.
**Contrast with:** `2026-11-02k` — the baseline store **writes** a version and never reads it. The manifest
**refuses** an unknown one. Same repo, two opposite behaviours, and this beat measured both sides.

**One line:** `LoadManifest` is the strict one — unknown version is a hard error, not a silent load. But the bytes
that get **signed** (`canonicalBody`) are a closed set of five fields, while the JSON that gets **read** is whatever
`encoding/json` accepts. Anything else in the file is loaded into no field, refused by nothing, and covered by nothing.

## Measured

| Test | Question | Measured |
|---|---|---|
| **Q2** | does the manifest enforce its version? | **Yes.** `version: 10` fails with `unsupported manifest version 10`. This is the behaviour finding k says the store lacks — and it is verified by execution, not by reading. |
| **Q3** | are unknown JSON fields refused? | **No.** `requester`, `approval_note` — accepted, ignored, known fields intact. `LoadManifest` does not call `DisallowUnknownFields`. |
| **Q4** | does a signed manifest verify? | **Yes.** Clean signed manifest → zero verdicts. |
| **Q5** | is the signed set exactly `{version, agent_id, root, created_at, files}`? | **Yes, pinned as a closed set.** Any sixth key in `canonicalBody` fails the test by name. |

**Mutation-proven, both directions.**
- **Adding `DisallowUnknownFields`** turns **Q3 red by name** — *"the manifest now refuses unknown fields, and old
  manifests carrying extras will fail to load"*. That is a real trade, and the test states it instead of hiding it.
- **Adding a sixth field to `canonicalBody`** turns **Q5 red by name**, naming the field — so the signed set cannot
  grow without a doc being re-read.
- First attempt at mutant A **did not compile** (`strings` not imported). A build failure is not a verdict; redone with
  `bytes.NewReader` so the mutant actually ran. `provenance.go` md5 `2765627bfee97119466c3c734bada126` restored and
  verified.

## The honest shape of this

This is **weaker** than k, and I am not going to dress it up. k is a claim that is false and a comment that promised
otherwise. This is a design choice with a documented reason: `canonicalBody` excludes `Signature` **on purpose** (a
signature cannot cover itself) and sorts keys **on purpose** (so a reformatted manifest still verifies). Unknown fields
being unsigned is the cost of a tolerant reader, and tolerant readers are exactly what you want when manifests are
hand-written and hand-approved.

What is worth knowing is the **asymmetry**, because it decides what a signature means here:

- **Covered, therefore tamper-evident:** `version`, `agent_id`, `root`, `created_at`, `files`.
- **Not covered:** `Signature` itself (necessarily), and every unknown key (by construction).
- **So:** a signature attests to the five fields and to nothing else. It does not attest that the file is unmodified —
  only that *the parts that matter* are unmodified. That is a fine guarantee; it is just a different one from "the file
  is intact", and a reader who assumes the second is trusting more than the code provides.

## Not claimed

- **Not** a vulnerability. There is no field on `Manifest` outside the canonical set, so today an unknown key cannot
  change behaviour — it is dropped on load, not interpreted. The finding is about the *reader being tolerant*, which
  matters the moment someone adds a field and forgets `canonicalBody`.
- **Not** that k's bug appears here. Measured the opposite way round: the manifest refuses unknown versions loudly.
- **Not** that `DisallowUnknownFields` is the answer. It would refuse manifests already on disk that carry extra keys —
  a compatibility break for a tolerance that has not hurt anyone. Dad's call, and my recommendation is: leave the
  reader tolerant, and make `canonicalBody` the guarded thing (Q5 already does).
- **Not** observed in a deployment — Phase 2 isn't deployed. Latent, like v/i/j/k.

**Status:** CLOSED at beat 771 (2026-11-02). Comment corrected to match the code; R1b promoted from declared XFAIL to a live regression guard.

# FINDING 2026-11-02n — the comment promises a startup safety the code forbids

**Status:** MEASURED · **NOT APPLIED** (two possible fixes, different contracts ⇒ Dad's call) · declared XFAIL
**Instrument:** `internal/central/manifestskip_test.go` (R1, R1b, R2–R5), `-race` clean.

**The comment, at `internal/central/manifest.go:49-51`:**

> *"Only files that parse as an watchman manifest are treated as manifests; a JSON file whose content **fails the
> version check is skipped rather than aborting startup**, so an unrelated .json does not take down the service."*

**What the code does:** `looksLikeManifest` checks only that `agent_id` is non-empty and `files` is non-nil. It never
looks at `version`. A manifest-shaped file then goes to `LoadManifest`, which **refuses** an unsupported version, and
`NewManifestRegistry` returns that error **up**. Measured:

```
central: load manifest .../future.json: provenance: unsupported manifest version 2
```

The service refuses to start. **The comment says the opposite, in the place an operator reads when deciding whether
it is safe to drop a file into that directory.**

## Measured

| Test | Question | Measured |
|---|---|---|
| **R1** | is a bad-version manifest skipped? | **No** — `NewManifestRegistry` returns an error and names the file. Pinned so a fix retires it loudly. |
| **R1b** | does the comment still claim skipping? | **Yes** → declared **XFAIL** (skipped, printed, not passed). |
| **R2** | is genuinely unrelated JSON skipped? | **Yes** — `trust-keys.json`, `notes.json` ignored. This is why the defect survived review: *the common shape works.* |
| **R3** | do the gate and the loader agree on "is a manifest"? | **No.** `looksLikeManifest` says yes on any `{agent_id, files}`; `LoadManifest` refuses the version. Both true at once. |
| **R4** | does a valid manifest still load? | **Yes**, and it registers by root. |
| **R5** | what is the blast radius? | **The whole directory.** One future-format file aborts startup and **every valid manifest in the dir is discarded** — a/b-good are read first and thrown away by the returned error. |

**Mutation-proven both directions.**
- **Implementing the comment** (`continue` on load error) → **R1 and R5 go red**, each saying *"FIXED; retire this pin"*.
  That is the good direction, and the test says so.
- **Fixing only the comment** → **R1b goes red** and names the claim.

## Why this is the sharpest one this week

Finding k was a version that is never read. This is a version that **is** read, strictly — and a comment that tells the
operator the strictness isn't there. The failure mode is not "the detector misses an attack". It is: **an operator drops
a manifest from a newer box into the shared directory, the service refuses to start, and the code they are reading
told them that couldn't happen.** For a tool whose whole premise is that a check must not lie about what it does, a
safety claim that is false is worse than a missing one.

The two fixes imply different operational contracts, which is why I am not picking one:

1. **Fix the comment** (5 minutes, no behaviour change): mid-upgrade, in that directory, an unreadable manifest is a
   stop-the-world event, and the operator must know it before deploying.
2. **Fix the code** (skip + log): matches the comment, but silently narrows coverage during an upgrade — a wrong-version
   manifest would be *ignored*, so files it was meant to approve would report as `unsigned`. **That is the more
   dangerous silence**, and it is precisely the class of failure this project was built to catch.

**My recommendation:** fix the comment. Fail-loud is the right behaviour for an approval set; the lie is the bug. But it
is his call, and it is not applied.

## What I did wrong on the way here, kept on the record

**R1b passed twice while testing nothing.** The first version searched for the phrase `"skipped rather than aborting
startup"` — but the sentence wraps across two comment lines, so that byte sequence exists **nowhere in the file**. The
check was unsatisfiable: green forever, proving nothing. I only found it by mutating the comment and watching it stay
green. Fixed by flattening `//` lines before searching, and I added a **positive control** that the flattener can see
this file's prose — because a silent flattener bug would make it vacuously green again, which is exactly how it failed
the first time. Then it went red on the real claim, which is the first evidence that it works at all.

That is the third time this week a check has been green for a reason other than the thing it was checking. **A green
that has never been made red is a claim, not a measurement.**

## Resolution (beat 771, 2026-11-03 ~00:05 UTC)

**Closed by fixing the comment, not the code.** The distinction that decided it: *a comment that describes its own code
inaccurately is a defect, not a policy decision.* Nothing about the operational contract changed — the abort behaviour is
untouched. Only the sentence that lied about it is gone.

`manifest.go:47-56` now states the real contract, including the part that matters: **the shape test is what skips
unrelated JSON; once a file looks like a manifest, any load or verify failure is fatal, and an unsupported version
ABORTS STARTUP.**

R1b was a `t.Skipf("XFAIL …")` for three beats. It is now `t.Fatalf` with a message that says *the false claim came
back* — because a retired pin is a different thing from a pending pin, and it should fail like a regression guard, not
skip like a decision.

Proven both directions: replanting the false sentence → **FAIL** (was SKIP); real tree → **PASS**.
`manifest.go` md5 `4ff5d589bad72940cb37e626289fdd83`, `-race` clean.

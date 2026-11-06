# FINDING 2026-11-02k — the store advertises a migration hook and never reads it

**Status:** MEASURED (real `FileStore`, real store files on disk) · **NOT deployed · NOT APPLIED.**
**Instrument:** `internal/central/storeversion_test.go` (Q1–Q5), `-race` clean.
**Origin:** finding `2026-11-02j` concluded that adding the agent id to the baseline key is *a migration, not a
rename*. So this beat went to look at the migration hook. There isn't one.

**One line:** `fileStoreDoc` writes `"version": 1` on every persist with the comment *"Kept versioned so a future
migration can detect the format."* **Nothing detects anything.** `NewFileStore` unmarshals the document and takes
`doc.Items` without ever comparing `doc.Version` to `fileStoreVersion`. A version field nobody reads is not a
migration hook — it is a comment wearing a JSON tag.

## Measured

| Test | Question | Measured |
|---|---|---|
| **Q1** | is the field live on disk? | **Yes.** `PutBaseline` → file contains `version: 1`. So Q2/Q3 are about a real field, not a vestigial struct member. |
| **Q2** | does a store from a *future* version load? | **Yes, silently.** `version: 42` loads, its items become baselines, and its keys parse exactly like current ones. |
| **Q3** | is the field unpoliced or unread? | **Unread.** `version: 0`, `-1`, and absent ALL load with their items intact. |
| **Q4** | control: does the current format still work? | **Yes.** `version: 1` loads; a missing file is a fresh store. |
| **Q5** | control: is the store validated at all? | **Syntax yes, format no.** Malformed JSON *is* rejected. |

**Mutation-proven, both directions.**
- **Implementing the hook** (reject `doc.Version != fileStoreVersion`) turns **Q2 red by name**, with the message
  stating its own retirement condition — *"If it now errors, the hook was implemented — retire Q2/Q3 and say the
  migration path is real."* This is the mutation a real fix should produce, and it produces it.
- **Removing JSON validation** turns **Q5 red by name** — *"the store has no validation at all, which is a bigger
  claim than this finding makes."* Q5 is a live control, not decoration.
- `filestore.go` md5 `79a9295c6bfea25d8bd3764a4952effa` restored and verified after each mutant.

## One honest wrinkle Q3 recorded rather than smoothed

`version: "not-a-number"` IS rejected — but by the **JSON decoder's type**, not by a version check (`json: cannot
unmarshal string into Go struct field fileStoreDoc.version of type int`). The test logs that distinction instead of
counting it as evidence for the finding. **A type error and a policy check both exit non-zero, and they are not the
same evidence** — that is the same trap as beat 645's import-error-scored-as-red, one layer down. `nil` and `0` are
the load-silently cases that actually prove the point.

## Why it matters, concretely

`2026-11-02j`'s fix needs a key change. `AgentKey` is persisted through `String()`/`parseAgentKey`, and those two
functions are **lossy in exactly the way a migration would need to detect**: if the key grows a third component, the
existing `parseAgentKey` will still split on the first `:/` and produce a plausible-looking `AgentKey{HostID, Root}` —
no error, no version bump, no warning. The store would read old baselines with a new parser and file them under a key
that happens to parse. That is the failure the advertised comment exists to prevent, and the comment is the only part
that exists.

**Not claiming** this happens today — `fileStoreVersion` is 1 and the key has not changed. It is latent by
construction, exactly like v/i/j.

## Not claimed

- **Not** that anyone has been bitten — one format version, one key shape, no deployment.
- **Not** that the hook is hard to add. It is a two-line check with a clear failure mode, and Q2's mutant shows it
  works. It is Dad's call only because it changes what central refuses to start with.
- **Not** that the version comment is a lie. It says *"so a future migration CAN detect"* — the intent was recorded
  correctly. What was never built is the detection, and the finding is that the intent reads like a guarantee.
- **Not** a restatement of j. j is the key dropping the agent id; this is the store being unable to notice its own
  format changing — the thing that makes j's fix dangerous.

## Options (Dad's call)

1. **Enforce on load:** reject `doc.Version != fileStoreVersion` with a clear error. Two lines, fails loud, refuses to
   start on an unknown format instead of guessing. Quarantines the migration risk.
2. **Read-and-branch:** compare the version and route through a migration function. Needed eventually, pointless
   until there is a v2 to migrate *from*.
3. **Delete the field and the comment.** Cheapest honest option if nobody intends to enforce it: a version nobody
   reads is worse than no version, because it reads as a guarantee.

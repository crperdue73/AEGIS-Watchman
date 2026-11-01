# FINDING — 2026-11-01k: a file the collector can't read is reported as a file that was deleted

**Status:** source reads throughout. **NOT CLAIMED as observed** — no Go toolchain here (beat 642), so no run.
**NOT APPLIED** — every option changes the wire schema or the alert semantics, so it is Dad's call.
**Instrument:** `scripts/seencensus.py`.
**One line:** the walk skips what it can't read (correctly, on purpose), and the drift layer then reports the skip as
a deletion — with high severity if the file was an identity file.

## The good decision, stated first

`collector.go:170` — *"Unreadable files are skipped rather than failing the whole walk (a locked file should not
blind the collector to every other file in the agent)."*

That is the right call and I am not arguing with it. **The finding is about its unsigned output, not the call.**

## Three absences, one representation

`WalkRoot` has three skip paths and they all produce the same thing — a file that is not in `inv.Files`:

| where | cause | what happens |
|---|---|---|
| `WalkDir` callback, `d.IsDir()` | unreadable directory | `filepath.SkipDir` — whole subtree, silent |
| `WalkDir` callback, entry | unreadable entry | `return nil` — one entry, silent |
| after `matchGlob` | `HashFile` failed | `return nil // unreadable file: skip, keep walking` |

`Inventory` is `{Root string, Files []FileState}`. **There is no field where "could not read N files" could live**
even if the walk wanted to record it. The absence is not merely unlogged — it is *unrepresentable*.

## What the drift layer says about it

`drift.go:16` iterates the baseline first. A baseline path missing from `curr` hits a branch whose comment is
literally `// Removed file.` It emits `EventIdentityDrift` carrying `SHA256Before`, no `SHA256After`, and
`ev.Severity = severityFor(path)`.

So `chmod`, an ACL, a dropped mount, a lock — **reported as a deletion.** Not "unknown", not "unverified":
*removed*.

And `severityFor` grades `soul.md`, `identity.md` and `persona.md` **high**, so the false removal arrives on precisely the files this
product exists to protect, wearing the severity that makes people act. (Measured from the switch: exactly those three
suffixes. My first draft of this doc said "identity/persona/skill" — **'skill' is not in the list**, and I corrected
the sentence rather than let the instrument encode my guess.)

## Why this is a step past 724–729

Those five were **silences** — true things nobody could see. This one **turns a silence into a positive false
statement.** (Said plainly: absence becomes an assertion.) "I did not see the file" becomes "the file was here and is gone." A miss is a non-event. This is a false
event with an alarm bell attached.

Nothing downstream can tell the difference either: the event is well-formed, the severity is right, and central has no
field to ask *"was this merely unseen?"*

## Scope, honestly

Requires a file readable at baseline to become unreadable later. Not exotic — secret rotation touching permissions, a
backup agent grabbing an exclusive lock, a bind mount appearing over a directory — but **not the default case.**

**And not measured.** This is what the code will say, read from source. A single test with a chmod'd file would
settle it; there is no toolchain on this host to run one. I am recording it as a code-level claim and labelling it as
one.

## Options — NOT applied, not picked

1. **Carry the gap in `Inventory`** — e.g. `Skipped []SkippedPath` with a reason per entry. Then `Drift` can tell
   *gone* from *not seen*, and the alert can say which. Structurally honest; changes the wire schema on both ends.
   My preference on the merits.
2. **Log it once per walk** (count + first few paths). Cheapest, fixes nothing, and the log is the last place anyone
   looks during an incident.
3. **`Drift` emits an `unverified` event instead of a removal** — needs (1) first; a consequence, not an alternative.
4. **Suppress removals that might be permissions.** **Argue against** — it silences real deletions to remove false
   alarms. In a detector that trades a loud wrong answer for a quiet missing one, and the quiet one is worse.

## What I am NOT claiming

- **Not observed.** No run, no toolchain. Source reads only.
- **Not that skipping is wrong.** It is right, and the comment shows it was chosen deliberately.
- **Not that every removal alert is false.** Most removals are removals. The claim is that one specific class of
  non-removal is indistinguishable from them.
- **Not a severity change recommendation.** I do not know whether the right fix is a new event type or a field; that
  is a product decision and guessing it on a heartbeat is how a tool acquires a policy nobody chose.

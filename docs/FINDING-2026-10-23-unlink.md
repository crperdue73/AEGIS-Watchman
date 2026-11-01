# FINDING 2026-10-23 — the rule cannot tell a delete from a write, and `persist` is where that costs the most

**Disposition:** Open, not patched. `persist.file_write` has no nametype predicate, so a delete answers like a write. Blocked on the toolchain for a behavioural fix; the probe stays.
Follow-on to `FINDING-2026-10-23-rename.md`, from the same read of `internal/telemetry/filewatch.go`,
asking the opposite direction: not what a rename hands the rule, but what a **delete** does.

## Result

`carbonato.persist.file_write` carries exactly one field predicate — `path_glob_any` — and **no predicate
on `detail["nametype"]`**. All ten globs are reachable, and they answer *identically* for a create, a
normal write, and a delete. `scripts/unlinkprobe.py`:

```
10 globs, 30 record shapes, 10 paths reached, 30 of 30 indistinguishable
unlink /etc/cron.d/<file>  -> fires, reported as "agent writes to a persistence location"
```

`unlink` and `unlinkat` are in `strictWriteSyscalls`, and `writeTarget()`'s first branch returns any PATH
record tagged `DELETE` — so the delete arrives as a `file.write` action, carrying `nametype: DELETE` in
its detail, and the rule has nothing to read it with.

## Why this is worth a finding and not a shrug

For a persistence rule specifically, a write and a delete are **opposite findings**:

- `install /tmp/x /etc/cron.d/evil` → persistence. This is the rule's stated job.
- `rm /etc/cron.d/evil` → anti-forensics / cleanup. Worth alerting on — as its own technique, not as
  `phase3/persistence`, and not under a title that says the agent *wrote* something.

An operator triaging a `persist.file_write` alert today cannot tell which of the two they are looking at
without going to the raw audit record. The detection is not wrong; the *label* is, and a label that
collapses "someone planted a cron job" with "someone deleted the cron directory" is the kind of alert
fatigue that gets packs switched off.

## Why the fix is not mine to make, and is bigger than it looks

The obvious patch — add `detail_equals: {nametype: NORMAL}` — is actively dangerous given today's rename
finding: **a rename carries no destination nametype at all**, so gating on nametype would silence renames
completely, on top of them already being reported against the source path. The two defects have one
correct shape:

1. `WriteAction` learns to name the destination on rename-family syscalls (`internal/`), and
2. the rule (or the schema) gets a way to say *which* nametypes it means, with a documented default.

Doing either alone makes the other worse. That is one decision, made once, by the pack owner — which is
why this is written down as a finding with both ends on one page instead of a patch.

## A mistake in my own probe, kept in the record

The first run reported **4 of 10 globs dead** (`*/etc/cron.d/*`, `*/etc/cron.daily/*`, `*/etc/cron.hourly/*`,
`*/etc/systemd/system/*`). That was my sample generator: it built the test path by stripping the glob's
stars, turning `*/etc/cron.d/*` into `/etc/cron.d` — the directory, which the glob correctly does not match
because a directory is not the file the rule claims to watch. The pack was fine; my fixture was testing the
wrong noun. Fixed by naming a realistic file under each directory, and `unlinkprobe_test.py` (9/9) pins the
bad transformation so it cannot return. Fourth time today that the instrument, not the code, was the defect.

## Denominators and limits

- 10 globs × 3 record shapes = 30 checks; 10 paths reached; 30/30 indistinguishable *by the rule* (the
  record itself does carry `nametype` — nobody reads it).
- `neargo.match_one` again: transcription, not the engine. No Go on this box.
- "auditd tags an unlink as DELETE" is read off the adapter's own comment and `nametypeFor`'s handling, not
  from a live audit log. Same open verification as the rename ordering.
- The remaining nine `path_glob_any`/`endpoint_glob_any` rules were not surveyed for nametype-blindness;
  only `persist.file_write` was, because it is the one where a delete means something opposite to a write.

Vivi 🌺 — 2026-10-23, ~2:55 PM ET

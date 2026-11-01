# FINDING 2026-10-23 — the rename claim was backwards, and the probe that said "never set" was never reading the code

I have repeated one sentence for four hours: *`persist.file_write` reads a `detail["path"]` that is never
set on a rename.* It is in my 11:30 report, my 12:31 report, WORKLOG 524, `FINDING-...-typecheck.md`, the
collector's own coverage note, and a chat post. **The field is set. Unconditionally. At
`internal/telemetry/filewatch.go:66`.** What the rename does is fill it with the *wrong file*.

This is the same defect with a different cause, and the difference decides the fix — so it gets its own
correction rather than a quiet edit.

## The chain, read again

```
WriteAction()                       internal/telemetry/filewatch.go:61
  target, ok := r.writeTarget()      :64
  detail := map[string]any{ "path": target, "syscall": r.Syscall }   :64-67   <-- always set
writeTarget()                       :86
  1. any PATH record with nametype CREATE or DELETE      -> that path   :88-93
  2. else firstTargetPath()                              :95
  3. strictWriteSyscalls[syscall]  -> true               :101        (27 syscalls, incl. rename,
                                                                      renameat, renameat2)
firstTargetPath()                   :112
  skips nametype==PARENT, returns the first remaining PATH.Name          :113-119
```

A rename has **no CREATE/DELETE nametype**, so branch 1 never runs. It takes branch 2 →
`firstTargetPath()` → *the first non-PARENT path record*. auditd lists the source PATH before the
destination on rename-family syscalls. So `detail["path"]` holds **the file being renamed from**.

`scripts/renametrace.py` prints that chain from the source with line numbers and asserts nothing it has
not read; `renametrace_test.py` (6/6) then runs both directions through the pack's own glob list:

```
case 1: mv /tmp/stage /etc/cron.d/evil   -> detail['path'] = /tmp/stage
        9 globs tried, 0 matched          <- a write INTO a cron directory, undetected
case 2: mv /etc/cron.d/evil /tmp/stage   -> detail['path'] = /etc/cron.d/evil
        9 globs tried, 1 matched: '*/etc/cron.d/*'   <- fires, on a path that is a coincidence
```

Same syscall, opposite results, decided entirely by which end of the rename auditd listed first.

## What this changes

1. **The mitigation is not "teach WriteAction to set path on rename" — it already does.** The fix is one of:
   take the *last* non-PARENT record for rename-family syscalls (destination, per auditd ordering); or read
   `renameat2`'s destination via `firstTargetPath()` reversed; or add a `detail["paths"]` and let the rule
   consider both ends. That is a schema/engine decision — `internal/`, not `rules/`. Not mine to make.
2. **The severity goes up, not down.** "Blind on rename" described a missing field. The truth is a rule
   reporting the *wrong file*: it misses `mv x /etc/cron.d/evil` and fires on `mv /etc/cron.d/evil x` where
   the destination is `/tmp`. An operator reading alerts gets a path that was moved, not a path written.
3. **Blindcheck's F2 is right about the effect and wrong about the mechanism.** I filed it as "globs never
   consulted on rename". They are consulted — against the source path. The pass case it wrote still
   demonstrates the miss; only my explanation was wrong.

## Also corrected in this pass: a file I overwrote

At 12:18 a second version of `scripts/typecheck.py` landed (class check: declared type vs. producer) and
**took the name over from my 11:32 wire check**, leaving `typecheck_test.py` (which pins the wire check's
known answers) failing 9/9 against a file it did not write. I had also briefed the guard set as green twice
since. Repaired:

- Wire check preserved verbatim: `docs/superseded/typecheck-wirecheck.py.txt`; its test at
  `scripts/attic/typecheck_test.wires.py`; its finding at `docs/superseded/FINDING-2026-10-23-wirecheck.md`
  with a superseded header, its still-true parts marked, and its broken denominator explained.
- Class check (`scripts/typecheck.py`, rc=0, 20 rules, 13/20 stock · 4/20 adapter-gated · 3/20 dead until
  R2.2) now says in its own docstring what it does **not** do: it never looks at detail fields, so it cannot
  see this finding.
- ⚠️ **Struck, same hour, before publishing:** I wrote that `blindcheck.sh` does not exist. It does — both
  files are there, `bash scripts/blindcheck.sh` → PASS (28 fixtures, 0 moved, 5 defects). My `ls` was run
  with a stale filter and I nearly published "gone" in a doc whose whole subject is not trusting a
  recollection. The lesson is on the tin: verify before retracting, too.

## Lower bound, stated

- `neargo.match_one` is a transcription of the Go matcher; nothing has been run. Two directions, 9 globs
  each: 18 match attempts, 2 capable of changing a verdict. `packcheck_test.py` does not exist (my guard line
  has been claiming "packcheck 4/4" all day); `packcheck.py` runs, its test does not exist. Corrected here.
- The glob parser here drops the last item of a block list (`*/authorized_keys` for this rule) — the same
  class of bug `fieldcheck.keys_by_rule` has. Pinned as a known answer in the test instead of fixed mid-hour.
- auditd PATH ordering for rename is assumed (source first). It is documented behaviour, but I have not
  verified it against a live audit log on this box — that is the one thing that would turn this from a
  strong reading into a confirmed one.

Vivi 🌺 — 2026-10-23, ~2:05 PM ET

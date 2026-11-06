# FINDING — 2026-11-02a: the collector reports an unwalkable root as an empty agent

**Status:** source reads **+ executed measurement** (beat 743). **NOT observed in a deployment. NOT APPLIED.**
**Instrument:** `scripts/walkerrorcensus.py`.

**One line:** an unwalkable root is reported as an empty agent. `WalkRoot`'s callback answers a directory error with
`filepath.SkipDir`, so a root that cannot be read at all returns a valid `Inventory` with `Files: []` — and central
diffs that against the baseline as **every file removed**.

## The chain

`collector.go:175-221`:

```go
err := filepath.WalkDir(root, func(path string, d os.DirEntry, err error) error {
    if err != nil {
        // Skip unreadable subtrees; do not abort the inventory.
        if d != nil && d.IsDir() {
            return filepath.SkipDir
        }
        return nil            // <-- the walk's own failure, swallowed
    }
    ...
})
if err != nil {
    return nil, err           // unreachable in practice
}
```

Every error path inside the callback returns `nil`. So `WalkDir` returns `nil`, `WalkRoot` returns
`(*Inventory, nil)`, and the `if err != nil` guard at `:219` is **dead code in the error direction** — it can only fire
if `filepath.Rel` or a future edit changes that, which the current callback never does.

The callback's first argument `err` is the one that matters: when `filepath.WalkDir` cannot read a directory it
**has already descended into**, it calls the callback with `d != nil`, `d.IsDir() == true`, and the permission error.
`d != nil && d.IsDir()` is true, so we `return filepath.SkipDir` — silently skipping the unreadable directory and
returning no error. Result: **`Inventory{Root: root, Files: []}`**.

Then central (`service.go:121-130`) does what it is told: `GetBaseline` finds the previous inventory, `Drift` compares
full baseline against empty current, and every known file is emitted as `EventIdentityDrift` with
`SHA256Before` set and no after-hash — **an AGENT ROOT INVENTORY WIPED event, once per file, at `high` severity if the
paths end in `soul.md`/`identity.md`/`persona.md`** (`drift.go:95-107`). And then `PutBaseline` (`service.go:150`)
**commits the empty inventory**, so the removal is no longer a transient report: the baseline is now empty, and the next
successful walk reports every file as **newly added**. Two false alarms from one permission bit, and the second one
says "unknown file appeared in a persona directory."

## MEASURED (beat 743) — the premise I flagged as unmeasured, now measured

My first draft of this finding carried a caveat: *"not that `filepath.WalkDir` calls the callback with `d == nil` —
that is documented Go behaviour but I have no toolchain-measured transcript."* **That caveat was wrong, and the
toolchain is on this host, so I measured it.**

`filepath.WalkDir` on an unreadable directory, three cases (`go build`, Go 1.22.12):

| case | first callback |
|---|---|
| readable root | `d != nil, isDir=true, err=<nil>` |
| **unreadable root (as `nobody`)** | **`d != nil, isDir=true, err="open …: permission denied"`** |
| nonexistent root | `d == nil, err="lstat …: no such file or directory"` |

So the `d == nil` branch only fires for a path that does not exist at all. **The real case takes the `SkipDir` branch** —
which is the outcome my draft called "worse, not better," and it is the one that happens.

And end to end, through the real `WalkRoot`, as an unprivileged user, on a root that genuinely contains `a/soul.md` and
`identity.md`:

```
RESULT err=<nil> files=0
```

**`files=0` for a directory with two files in it, one of them an identity file.** Not a read of the source — an executed
test against the shipped function. Central then diffs that against the baseline and reports both files as removed at
`high` severity, then commits the empty baseline at `service.go:150`.

`CollectAll` (`run.go:22-35`) *does* guard the "matched nothing" case — `len(matches) == 0` returns
`agent root %q matched no directories`. That is a **glob** check, done before the walk. It protects against a root that
does not exist. It cannot protect against a root that **exists and cannot be read**, because that distinction only exists
inside the walk — which is exactly where the errors are discarded.

So the shape is: the guard is real at the boundary where it is cheap to write, and is absent at the one place the
information actually lives. Same refrain as 740's cap, 739's retention.

**The realistic trigger is not exotic:** the collector runs as one user; the agent root is created by another (a
container, a service account, a freshly-provisioned workspace). `chmod 700` on `~/.openclaw/workspace-x` and the
collector's walk gets `EACCES` on the root itself. Or a filesystem that is not mounted yet, or a root that is a
mountpoint whose volume is unmounting. All of these look identical from inside the callback and all mean **"I could not
look"**, never **"there is nothing there."**

## Sound parts, checked

- **Skipping unreadable *subtrees* is the right call** and is documented as such (`:177-179`). Aborting an entire
  inventory because one directory is `chmod 000` would let an attacker suppress the whole agent's drift with one
  `chmod`. The design is right; the *unreported* part is what's missing.
- **Unreadable *files* are deliberately skipped too** (`:202-204`) — same reasoning, same problem, and **already filed
  as beat 730** (`FINDING-2026-11-01k-unreadable-is-reported-as-removed.md`). That is the per-file door.
- **`filepath.Rel` failures fall back to the absolute path** (`:191-194`) rather than dropping the file — a real file is
  never lost to a path-format problem. Good.
- **ModTime failure is tolerated** (`:206-210`) rather than failing the file — a metadata problem does not cost you a
  hash. Good.

## De-duplication — this is 730's third door, not a new bug

Beat 730 found: an unreadable **file** is reported as removed. This is the same false statement entering through a
different door: an unreadable **directory** is reported as an empty inventory, and the same
`unreadable → absent → removed` conversion happens. What is genuinely new here is only:

1. **The scale.** 730 was per-file; this is per-agent-root, and the blast radius is the entire inventory in one report.
2. **The amplification.** 730's false removals leave the baseline intact, so they repeat identically each cycle. Here
   `PutBaseline` at `service.go:150` is unconditional, so the empty inventory is **committed** — the next good walk
   reports every file as **added**. One permission change produces two opposite false alarms, and the second is the
   one that reads like an intrusion.
3. **The dead branch.** The `if err != nil` guard at `:219` was presumably written believing the callback could
   propagate. It cannot. A guard that cannot fire is not a guard — the same species I named at beat 737 for
   `errors.Is` and have been counting ever since.

## Options — listed, not picked

1. **Carry the skip.** `Inventory.Skipped []SkippedPath` (or `Unverified []string` + `Complete bool`), filled by the
   callback. Central then refuses to diff an inventory whose root was never read. **This is also beat 730's preferred
   fix** — one field closes both doors, which is the strongest argument for it.
2. **Fail the walk when the ROOT itself fails.** Distinguish `path == root && err != nil` inside the callback → return
   the error. Narrow, two lines, leaves the per-file door (730) open.
3. **Refuse to store an empty baseline over a non-empty one.** A cheap central-side backstop that survives collector
   bugs generally, at the cost of never being able to record a genuinely-emptied agent.
4. **Document it** next to `WalkRoot`: "an inventory with zero files may mean the root was unreadable."

Options 1 and 3 change the wire format / the store's contract ⇒ **Dad's call** (beat 724).

## What I am NOT claiming

- **Measured, not inferred:** the `files=0` above is an executed `WalkRoot` call (beat 743). The shipped-source claim
  in this doc is now backed by a transcript, not by reading.
- **Not** measured under a real container/mount. I reproduced with `chmod 000` + an unprivileged uid, which is the same
  `EACCES` the callback sees; I have not reproduced an unmount race or an NFS stale handle.
- **Not** that an empty agent root is impossible. It is possible and legitimate; that is why option 1 (report
  **unverified**, not empty) beats option 2.
- **Not** a new finding at the per-file level. That is beat 730 and it stays 730's.

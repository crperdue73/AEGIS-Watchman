# FINDING 2026-11-02l — I predicted the baseline store leaks its slice; it does not

**Status:** MEASURED · **NEGATIVE RESULT — nothing to fix.**
**Instrument:** `internal/central/cloneisolation_test.go` (N.B. Go test files are accepted by the `internal/...\.go`
pattern in `scripts/docsindex.py`.)
(S1–S5), `-race` clean.
**Continues:** v → j → k (identity, key, format). This beat went one layer lower and found the layer **sound**.

**One line:** `cloneInventory` uses `make` + `copy`, which gives the store a **fresh backing array**. I wrote a test
asserting the two inventories shared storage and it **failed on first run** — the prediction was wrong, the code was
right. The test is kept, pointing the direction the measurement went instead of the direction I guessed.

## Measured

| Test | Question | Measured |
|---|---|---|
| **S1** | does mutating a `GetBaseline` result reach the store? | **No.** Mutating `SHA256`/`Size` on the returned inventory leaves the stored baseline intact. |
| **S2** | do the source and clone share a backing array? | **No.** Element addresses differ; a write through the source is invisible in the clone; the clone does **not** inherit spare capacity, so a caller's `append` allocates rather than writing into shared slack. |
| **S3** | does the ingest path write into the caller's inventory? | **No.** Deliberately unsorted input came back unsorted — `Ingest`/`Drift` are read-only over `inv.Files`. |
| **S4** | does `append` in a caller reach the store? | **No.** |
| **S5** | is shallow-copying sound *by construction*, or by luck? | **Sound today, guarded forward.** `FileState` (5 flat fields) and `Inventory` (`Root` + the copied slice) contain no reference types; a reflection guard fails the day one is added. |

**Mutation-proven, both directions.**
- **A genuinely leaky clone** (`Files: inv.Files` instead of make+copy) turns **S1 and S2 red by name** — S1 prints the
  tampered hash/size that reached the store.
- **Adding `Tags map[string]string` to `FileState`** turns **S5 red by name**, naming the field and the fix. That is the
  guard earning its keep: the clone would silently start sharing a map between the store and every caller.
- `store.go` md5 `caabc48a31b154dbf922ae5c7929aad2` and `collector.go` md5 `7fab12e729a589465353447a537e8bf1`
  restored and verified after each mutant.

## What I got wrong, kept on the record

I had the Go semantics backwards: I believed `copy` from a same-length slice left two headers over one array. It does
not — `make` always allocates. I noticed because S2 went red, and then I confirmed it outside the repo with a
three-case scratch program before touching the test, rather than editing the assertion until it went green. **The
temptation in that moment is to turn a failing prediction into a passing test by weakening it; that is how a fixture
gets bent to fit a theory** (beat 649, same species).

## Why this is worth a finding at all

Two reasons, neither of them "I fixed something":

1. **It closes the stack honestly.** v, i, j, k are four live defects in the identity path. This is the layer directly
   underneath them, and it is correct. Knowing where the soundness *stops* is as load-bearing as knowing where the
   defects start — it tells Dad the key change in j doesn't also need a storage-copying fix.
2. **S5 is the only forward-looking thing here**, and it is a guard rather than a finding: the moment `FileState`
   gains a map, pointer, or slice, `cloneInventory` silently becomes shallow in a way that matters. That field is
   exactly what a richer file inventory would grow, so the guard has a real chance of firing.

**Not claiming** a bug, a risk in production, or a change of any kind. Nothing applied; nothing to apply.

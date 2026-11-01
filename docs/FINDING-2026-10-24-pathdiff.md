# FINDING — 2026-10-24 — two copies of the same path resolver, and they disagree on one input

**Status:** real, small, not a bypass. `rules/` and `internal/` untouched.
**Instrument:** `scripts/pathdiff.py` · **Test:** `scripts/pathdiff_test.py` (45 checks, green)
**Beat:** 540, written 03:55 ET Saturday

## Where it came from

Severity (539) was defended because `ParsePack` validates. That raised the next question: does **every** way a
pack can enter the system go through `ParsePack`? It does —

- `collector` → `behavior.go:94` → `rules.LoadPacks(dir)` → `LoadPack` → `ParsePack` ✅
- `cmd/rules` → `loadSet(paths)` → `LoadPacks` (dir) or `LoadPack` (file) → `ParsePack` ✅
- `pins.go` → `PinFile` / `PinSet` → `ParsePack` ✅

One choke point, validation at it, `KnownFields(true)` refusing unknown keys. So the loader is genuinely
defended, and that is the end of that thread.

But the pins path is a **second implementation of the same traversal**, and `pins.go` says so:

> The file selection matches LoadPacks exactly — the same `*.yaml`/`*.yml`, non-recursive, sorted — so a pin
> always describes the set the collector would actually load from those paths.

Two implementations of one rule is this week's recurring bug shape. So I measured the claim instead of trusting
the comment.

## What actually agrees

**File selection: genuinely shared.** `packFileNames(dir)` is defined once in `rule.go` and both callers use it —
`LoadPacks` via `packFileNames(dir)`, `PinSet` via `packFileNames(p)`. The `*.yaml`/`*.yml` suffix filter exists in
exactly one place (verified: two `HasSuffix` calls, both inside `packFileNames`). `PinSet`'s comment is **true as
written**.

## What disagrees

The **path list walk** is copy-pasted rather than shared:

| | `loadSet` (collector-side verify) | `PinSet` (pinfile writer) |
|---|---|---|
| `os.Stat` each path, branch on `IsDir()` | yes | yes |
| dir → `packFileNames` | yes | yes |
| file → parse it | yes | yes |
| **reject a set that contributes nothing** | **`total == 0` → error** | **no such check** |

`loadSet`'s guard comes with its own reasoning in the comment — *"A path with no rules is almost always a mistake
(a wrong dir, a deleted pack), and 'verified: 0 rules' is a green light for nothing."* That reasoning applies
exactly as much to `pin`, and `pin` does not apply it.

**Consequence:** `watchman-rules pin /typo'd/dir` writes a lockfile containing a header and **zero pins**, exit 0.
The next morning an operator reads that as "I recorded an approved set." There is nothing in it.

## Why I am calling it small

I checked the direction before writing it up, because "pin writes an empty file" sounds worse than it is:

- `verify` compares pins to loaded packs by count and by hash. **An empty pin set can never approve real packs** —
  `verify(0, n)` fails for every `n > 0`. Modeled and asserted across `n = 1..9`.
- `verify(0, 0)` is a "PASS on nothing" — annoying, not dangerous.
- So the cost is **a misleading artefact, not a security bypass.** It tempts an operator into false confidence; it
  does not let anything through.

It is the same *shape* as the bugs that did matter — one logical rule, two implementations, a guard on one side
only — but the blast radius is one wrong file, so it belongs at the bottom of the list, not near the top.

## Fix, if the pack owner wants it

Three lines in `PinSet`, mirroring the guard already in `loadSet`:

```go
if len(pins) == 0 {
    return nil, fmt.Errorf("no packs found in %s", strings.Join(paths, ", "))
}
```

The deeper fix is to share the traversal — one `resolvePackPaths(paths []string) ([]string, error)` used by both —
so the two can never drift again. That is the `packFileNames` lesson applied one level up, and this file already
argues for it: *"It is the one definition of 'the packs in a dir', shared by LoadPacks and PinSet so a pin always
describes the exact set the collector would load."* That sentence is about filenames. It wants to be true of
paths too.

## The test has a retirement condition

If `PinSet` ever gains the guard, the disagreement disappears. Rather than leaving a stale finding to rot in the
list, `pathdiff_test.py` **detects the guard's presence** and prints a notice to re-verify and retire this file.
A finding that cannot announce its own obsolescence is a finding that becomes folklore.

## Reader bug, mine, caught by the probe

First run went red on `the doc claim is about FILE SELECTION` — because I searched `pins.go` for the comment as
one string, and the source wraps it across lines 76–77. **The claim was there; my reader wasn't.** Fixed by
normalising the comment carrier (strip `//`, join lines) before matching the content — the same lesson as the YAML
list-marker bug: *normalise the carrier before matching the content.*

## Reproduce

```bash
python3 scripts/pathdiff.py        # rc 0; disagreement confirmed, smallness modelled
python3 scripts/pathdiff_test.py   # 45 checks incl. retirement condition
```

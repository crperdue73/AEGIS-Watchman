# FINDING 2026-10-24 — noloadprobe (541): the pin path validates more than I assumed; the residue is cross-pack ids

**Status:** open (small, ranked below 540). **Severity:** low. **Bypass:** none.

## What I expected, and what measurement said

I opened this hour following 540 with a hypothesis that looked obvious: `watchman-rules pin`
walks paths and hashes bytes (`PinSet` → `PinFile` → `sha256`), while `verify` also parses and
validates (`loadSet` → `LoadPack` → `ParsePack`). If those are separate, a lockfile could certify
bytes that `verify` refuses — "a green artefact for a red set."

**The premise is wrong.** `ParsePack` (rule.go:127) is not a plain unmarshaller. It:

- decodes with `KnownFields(true)` (unknown keys are a hard error);
- rejects a pack with no name (rule.go:134) and one with no rules (rule.go:137);
- calls `r.Validate()` on every rule (rule.go:149);
- rejects duplicate rule ids **within** that pack (rule.go:151-153).

`PinFile` calls `ParsePack`. So pinning gets parse **and** rule-shape validation from the same
choke point the collector uses. This is the same conclusion 539 reached from the other direction:
parse-and-validate is one gate, and it is where the static authoring path is defended.

A hypothesis that survives six findings of the same family is not evidence for the seventh.
This is the second time in three beats I expected a defect on the static path and found a
defended one. That is now a pattern worth writing down: **the defects are on the runtime
correlation path; the authoring path has a validator, and I should stop predicting around it.**

## What actually differs (the honest residue)

Two checks exist in `loadSet` and not in the pin path:

| check | `loadSet` (verify, collector) | `PinSet` (pin) |
|---|---|---|
| `CheckUniqueIDs` across the whole set | yes | **no** |
| rule total `== 0` | rejects | no guard (540) |

The first is the sharp one, and it is not reachable from `ParsePack`: the duplicate-id map is
**local to the pack** (`seen := map[string]bool{}`). Two *different* packs that each declare
`carbonato.exec.shell_spawn` each parse green, each hash into a pin line, and `pin` exits 0 —
while `verify` on the same directory fails with a duplicate-id error.

`CheckUniqueIDs`' own doc comment (rule.go:220-227) already explains why this matters: duplicate
ids ship duplicate alerts under one id and let a hostile pack silently shadow a real rule. The
comment describes the set-level rule accurately; the point here is narrower — **the pin tool does
not apply it**, so a committed lockfile can describe a set the fleet will refuse.

## Direction of failure, checked not assumed

- `verify` and the collector **both** go through `loadSet`, which calls `CheckUniqueIDs`. The set
  dies at load, loudly, every time.
- A pin file is an **integrity** record (bytes → sha256), not a **validity** record. It cannot
  launder an invalid set into a running one. There is no bypass.
- What it can do is mislead an **operator**: a lockfile with a plausible hash line per file reads
  like an approval, and the refusal arrives later at a different tool.

Ranked **below** 540, and deliberately: 540's artefact (0 packs) is empty and announces itself on
the next `verify` run, whereas this one looks complete.

## Fix (three lines, same shape as 540's)

The deeper fix is the same one both findings argue for: **one function that resolves a pack set
from paths**, used by `loadSet` and `PinSet` alike, so the two cannot drift again. The cheap fix
is to have `PinSet` build its pins through a shared loader, or at minimum to call
`CheckUniqueIDs`-equivalent set checking before formatting. `pin` should refuse to write a
lockfile for a set `verify` would reject — a lockfile's whole job is to be the thing the fleet
obeys.

## Coverage / limits

- `scripts/noloadprobe.py` — 17 checks green, source-level.
- **No Go run.** The build toolchain is absent on this host; nothing here claims a Go test was
  executed. The behavioural pair belongs in `cmd/rules/pins_test.go`: two packs, one shared id,
  assert `pin` exits non-zero (after the fix) and `verify` errors.
- Falsifier built in: if `Validate` ever moves out of `ParsePack`, check 1 goes red and the
  finding widens and must be re-measured. If the pin path ever gains the set check, check
  "adding the set check to the pin path closes the asymmetry" tells the reader the note retires.

## Reader bug of the hour (mine)

First draft asserted `"pin can exit 0 on a set verify rejects"` with an expression that was
always false — `"Validate(" in PINSET == False and ...` reads as `"Validate(" in (PINSET == False)`
in Python, and `==` binds looser than `in`. The check went red, which is the only reason I looked.
An instrument that cannot pass is as bad as one that cannot fail; the assertion now states the
mechanism instead of a compound-of-negations I mis-parsed.

`rules/` and `internal/` untouched. No commit.

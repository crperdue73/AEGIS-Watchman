# FINDING 2026-10-30e — `publish-scan.sh` prints PASS when it cannot run at all

**Status:** Instrument added (`scripts/scanblind.py`). **Live defect in the release gate**, not in the product.
**Instrument:** `scripts/scanblind.py` · **Beat:** 678 · **Family:** 664, 667, 670, 674, 677 — and the worst of them

## Credit first

The *design* of `publish-scan.sh` is right and deliberate. It scans the tracked tree with `git grep`, it has a separate
history mode for unpushed commits, it collapses repeated history hits to "N distinct lines across M commits" so the
finding stays readable, it supports an allowlist for generic fixture users, and it distinguishes FATAL from warn. The
header comment shows someone thought carefully about *what* to search and *where*. The defect is not a misunderstanding of
git. It is four characters.

## The find

I ran the gate's own scanner on this tree. It printed:

```
publish-scan: PASS — no tracked file and no unpushed commit carries a credential,
              an operator home path, or private infrastructure
```

Then I planted `docs/zz-leakcanary.txt` containing a `cpat_`-shaped token and ran it again. **PASS, rc=0.**

## The mechanism

```bash
out="$(git grep -n -I -E -- "$pat" 2>/dev/null)" || true
[[ -z "$out" ]] && return 0
```

This tree has **no `.git`** — destroyed in the 2026-10-08 migration. So:

| step | result |
|---|---|
| `git grep` | `fatal: not a git repository` · rc≠0 |
| `2>/dev/null` | the reason is **discarded** |
| `\|\| true` | the exit status is **discarded** |
| `out=""` | empty |
| `[[ -z "$out" ]] && return 0` | **"no hits"** |

All four checks route through `report()`, and `fatal` is only ever set from inside it — so nothing can contradict the
verdict. The script prints a sentence asserting a scan that never happened.

## Why this is the worst instance so far

Beats 662–677 found calm-looking screens *inside the product*: unbounded alert, no sink, `/healthz` that cannot say no,
a store that reports accepted, a read error that stops delivery, a monitor that cannot tell unreadable from present.

**This one is inside the tool that decides whether the product is safe to publish.** It has been dark since 2026-10-08, it
is not in `suitecheck.sh`'s SELFHARNESS (nothing runs it), and `MEMORY.md` lists `make publish-scan` among the green gates
that still run post-migration. A green gate that cannot fail is worse than a missing gate, because it consumes exactly the
attention that would have caught what it was supposed to catch.

## Claim / not claimed

**Claimed (measured here):** `publish-scan.sh` prints PASS with a `cpat_`-shaped token in the tree; `git grep` fails
"not a git repository"; `|| true` + `2>/dev/null` reduce that to an empty string; `report()` returns 0 on empty; `fatal`
has exactly one assignment site.

**Not claimed: that `publish-scan` was always broken.** It was green while a `.git` existed, and MEMORY.md records it green
pre-migration. **This is a regression *on* the migration, not an original sin.** **Not claimed: that a real credential is
public right now** — I did not and cannot audit the remote from this host. What is claimed is narrower and worse: the local
guard cannot answer the question, and says it can.

## Fix (Dad's call) — three parts, and the first is the one that matters

1. **Distinguish CANNOT-RUN from NO-FINDINGS.** Capture `git grep`'s status; if it is nonzero for any reason other than
   "no matches", **banner-exit FATAL** ("scanner unavailable"). Stop sending stderr to `/dev/null`, or tee it into the
   report. A scanner must never print PASS on a question it failed to ask. This is the same rule the product needs at beat
   672 (typed error) and 677 (readability, not existence) — I have now found it four times inside one week, and this once
   it was mine.
2. **Add `publish-scan.sh` to `suitecheck.sh`'s SELFHARNESS.** It is a pack-exempt guard that nothing runs, which is the
   mechanism by which it stayed dark for three weeks. (This is the beat-640 pattern: instruments on disk that nothing
   invokes.)
3. **Restore the `.git`** (already routed to Dad via Jade, hb202). Until then, fix 1 will banner-fail on every heartbeat —
   which is the *honest* state, and I would rather have the noise than the silence.

## Mutation proof

| mutation | result |
|---|---|
| **fix 1 applied** (nonzero-and-not-empty ⇒ FATAL) | **FAIL ×3** — finding self-retires; the canary check also flips · rc=1 |
| `fatal=1` removed from `report()` | **FAIL** — "fatal can only be set from inside report()" · rc=1 |
| control | 11 rows green · rc=0 |

The canary check (`plant a shaped token, require NOT-PASS`) independently detects fix 1 landing, so the instrument does not
rely only on source-shape assertions — but note that **the canary alone could not have found this**: before the fix, it
*passes*, and a passing canary is indistinguishable from a working scanner unless you also assert the cannot-run branch.

## Trigger

Callie's 7063 correction: *"the pattern I named and the next programmatic action I took were the same pattern."* Last beat
I grepped my own scripts for argv credentials and declared myself clean. That was a *reading*, not a *run* — so this beat I
executed `chatread.py`'s credential path and `publish-scan.sh` for real. `chatread.py` is genuinely clean (env-only, URL
never logged, error paths name exception classes). `publish-scan.sh` was not. Filing it would have been the same mistake
Callie just named.

## Gate

**91 suites / 1731 checks / 0 failures.** Baseline rewritten (`scanblind.py 11`).

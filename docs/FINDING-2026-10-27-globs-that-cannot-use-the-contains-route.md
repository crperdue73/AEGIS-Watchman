# FINDING 2026-10-27 (beat 612) — three globs that cannot use the contains route, and what that costs

**Status:** CONFIRMED, unfixed. Measurement + one new gate. No rule edits.
**Severity:** MEDIUM for `read_model_key` (narrow, not dead), LOW-but-real for `privileged_container`
(already filed as dead under beat 611).
**Origin:** Leo in family chat (msg 6669): *"nothing **could** catch it with the tests you had, because
the fixture shape and the matcher shape disagreed."* He named a class my beat-611 gate didn't cover,
so I went and looked for the rest of it.

## The class

`substringNeedle` gives the contains fallback its power, but it needs a glob of the exact shape
`*<core>*` where `<core>` holds no `*?[`. Everything else falls back to `path.Match`, where **`*` never
crosses `/`** — so the pattern's segments must line up with the path's segments one-to-one.

Two glob shapes therefore lose the fallback:
1. **Interior star** — `*-v /*:/`. Covered beat 611; dead.
2. **Two wildcard runs in ONE segment** — `*/openai*key*`. The single segment `openai*key*` must match
   one path segment containing `openai` *then* `key`. If the path splits those across directories, it
   can never match. **This beat.**

Scanned all 69 globs in `rules/carbonato.yaml` across `path_glob_any` and `argv_any`: **exactly 3** lack a
contains needle. So this is a small, finishable list — not a systemic rewrite.

| rule | glob | verdict |
|---|---|---|
| `secret.read_model_key` | `*/openai*key*` | narrow, not dead |
| `secret.read_model_key` | `*anthropic*key*` | narrow, not dead |
| `infra.privileged_container` | `*-v /*:/` | **dead** (beat 611) |

## What the two `read_model_key` globs actually reach

Measured over a 9-rung ladder balanced across both vendors (an earlier 6-rung version listed only
openai flat names and made `*anthropic*key*` look dead at 0/6 — a skewed ladder manufactures a finding,
which is the same failure as a fixture in the wrong shape):

| path | `*/openai*key*` | `*anthropic*key*` |
|---|---|---|
| `/etc/openai_key` | **yes** | no |
| `/srv/openai_api_key` | **yes** | no |
| `/home/u/openai-key.txt` | **yes** | no |
| `/etc/anthropic_key` | no | **yes** |
| `/srv/anthropic_api_key` | no | **yes** |
| **`/home/u/.openai-key`** | **no** | no |
| **`/etc/openai/api_key`** | **no** | no |
| **`/opt/openai/keys/prod.key`** | **no** | no |
| **`/root/.anthropic/key`** | **no** | no |

Measured reach: **2 of 9 rungs each**, and identically so — flat filenames (`openai_key`,
`openai_api_key`, `anthropic_key`, `anthropic_api_key`) and nothing else. Any credential laid
out as *directory then filename* — which is the common shape for a config tree — is missed:
`.openai-key` as a leading-dot file, `openai/api_key`, `openai/keys/prod.key`,
`.anthropic/key`, `anthropic/api_key`. Seven misses against two hits, and the misses are the more
realistic layouts.

Not dead. **Materially narrower than the rule title claims**, and narrower in a direction that is easy
to trust wrongly: the rule does fire on *some* OpenAI paths, so any test written from a firing example
passes and the gap never shows. That is Leo's point exactly — a fixture in the *matched* shape proves
nothing about the shape that isn't.

## Why it happens

The glob author wrote `openai*key` as a single-segment pattern intending "an OpenAI key path", where
`openai` and `key` were conceptually `*`-separated across directories. In `path.Match`, a `*` inside a
segment is bounded by `/`. The intent needs the whole path, not one segment.

The engine has no glob construct for "contains `openai` somewhere AND `key` somewhere". Expressed in
the pack's own vocabulary, the intent is two separate globs — `*openai*` and `*key*` — which is
useless, or the correct tool: two conditions ANDed on the same path, which `match` does not currently
offer for `path_glob_any`.

## Fix directions (for whoever owns the pack)

1. **Cheapest, honest:** rewrite to what the engine can actually express. `*/openai*key*` →
   `*openai*key*`? No — same class. Better: `*openai*s*key*` is also one segment. The shape that
   *works* is a pair of globs that each carry a needle and both must hold. Absent an AND, use the
   contains route honestly: `*openai*` alone is far too broad, so this needs (2).
2. **Right fix:** allow multiple glob groups ANDed on the same detail/path (the `detail_non_empty` /
   `detail_non_empty_any` precedent already shows the pack has both AND and OR vocabulary). Then
   `["*openai*", "*key*"]` ANDed says what the author meant.
3. **Interim, zero-risk:** add the layouts that matter as exact-ish globs —
   `*/.config/openai/*`, `*/.openai-key`, `*/openai/keys/*`. Verbose but truthful today.

I have **not** changed the pack: this is detection content, and beat 611 already has one pack edit
queued for a decision.

## The gate

Superseded by `scripts/nocontains_test.py`, kept separate from the dead-glob gate on purpose,
because the two assert different things:

- `scripts/bindargv_test.py` — the dead-glob fix itself (`*-v /:*` replacing the never-firing
  `*-v /*:/`). Replaced `deadglob_test.py` at beat 613: the tombstone asserted a literal `DEAD`
  instead of reading the pack, so it could not notice the literal had gone stale.
- `scripts/nocontains_test.py` — **this beat's sweep,** enumerating the pack and failing if the set
  of no-needle globs *grows* beyond the known ones. A new glob in this shape has to be noticed the
  day it is authored, not a year later.

> Note (beat 617): this section cited `scripts/deadglob_test.py`, which no longer exists. The
> instrument was replaced, the reference was not — a doc that names a deleted instrument reads as
> evidence and is not. Caught by `scripts/docsindex.py`, which is exactly what that check is for.

The sweep reads the pack rather than hardcoding, so it also reports the count, and it prints each glob's
reach over a fixed path ladder — the difference between "fires somewhere" and "fires where it matters"
stays visible.

## Gates

47 → 48 suites · **1178 checks · 0 failures** (was 1167), none below baseline.
No Go toolchain — source-reading plus the faithful port; the probes say so.

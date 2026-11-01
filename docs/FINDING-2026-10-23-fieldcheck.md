# FINDING 2026-10-23 — "39 globs checked" is 36% of the pack

**Disposition:** Open, pack owner's call. 56 of 108 matched `detail` values have no local guard; closing it is a test-and-fixture job, not a patch this tree can make unasked.
Companion to `FINDING-2026-10-23-globfix.md` and `FINDING-2026-10-23-neargo.md`.
Same day's thread, one level up: not *is the glob right* but *is anybody looking*.

## The claim that started it

Every guard in this tree reports green on the same sentence: `packcheck: 39 globs checked, 4 dead,
0 skipped`. Four dead globs is a number I have quoted in three findings today. I never asked which
39.

They are the `argv_any` values. `packcheck.py`'s key loop reads `argv_any` and nothing else — its
`SAMPLES` table has entries for the 7 rules that carry that key. The corpus-based probes
(`fpprobe`, `flagprobe`, `crossprobe`, `sigprobe`) all feed it `process.exec` argv lines, so they
inherit the same boundary. Not one local guard reads a **path**, an **endpoint**, or a **scope**
predicate.

## What the pack actually holds

`scripts/fieldcheck.py` walks the pack's match keys and prints the denominator:

```
fieldcheck — 17 match-key blocks, 108 matched value(s) total

  key                    globs  rules  read by
  argv_any                  39      7  packcheck.py + sigprobe.py (against the pack's own SAMPLES)
  path_glob_any             30      4  NOBODY
  endpoint_glob_any         25      3  NOBODY
  exe_any                   13      2  packcheck.py, as a candidate source only — no sample targets it
  detail_equals              1      1  NOBODY

  denominators: 108 values in the pack, 39 read by a sample-driven check (36%), 56 read by nothing.
```

Blind, by rule:

- `path_glob_any` (30) — `persist.file_write`, `secret.read_after_shell`, `secret.read_model_key`,
  `secret.read_order_follows_constraint`
- `endpoint_glob_any` (25) — `c2.blockchain_rpc`, `exfil.egress_after_change`,
  `exfil.egress_after_identity_change`
- `detail_equals` (1) — `tool.scope_new`
- `exe_any` (13) — not blind but not *proved*: it contributes candidates to the matcher and no
  sample table targets it, so no check would notice if it were wrong

## Why this is worse than the dead-glob finding, not better

A dead glob is a defect **someone can see**. `*/etc/cron*` is wrong in a way a reader with the
corpus in hand can catch, and did catch, three hours ago.

`endpoint_glob_any: "*slack.com*"` under a `net.egress` rule has **no reader at all**. Nobody is
looking at it, in either direction: no probe proves it fires, and no probe proves it stays quiet.
Note the shape it shares with today's other defects — the `substringNeedle` path that makes
`*slack.com*` work against `https://api.telegram.org/...` is *symmetrical*, so the same fallback
that gives it a generous hit also gives it a generous miss. That is a guess about the engine's
behaviour, flagged as a guess, on a key no test covers.

And the two dead globs I could not honestly fix this morning both live in blind territory:
`secret.*` rules use `path_glob_any`, where `*/.aws/*` and `*.env` have never been run against a
single sample by anything in this tree.

## The correction to my own headline

Three findings today said "4 dead of 39". The honest sentence is **"4 dead of 39 argv_any globs,
which is 36% of the pack's 108 matched values."** Both numbers were sitting in the same run of the
same tool the whole time; I read the one that flattered the coverage.

This is the same failure as this morning's, and yesterday's, wearing a third costume: **a number
with a denominator I never printed.** The rule I set for new probes — print the denominator and how
many cases were capable of failing — I applied to the tools I was building and not to the tool I was
quoting.

## Honest limits

- `fieldcheck` counts **values**, not behaviour. 30 `path_glob_any` entries are not 30 detections;
  several may be redundant or unreachable for reasons this count cannot see.
- "NOBODY" means no local check. A human reader of the pack is still a reader, and the pack owner
  may well have done this by eye. The claim is about automation only.
- It reports; it does not gate. Exit 0 always, by design — a coverage report that fails the build
  would be turned green by adding sample rows, which proves nothing.
- The parser is regex-based over YAML, matching what `packcheck` already does. A pack reformatted in
  a way this regex does not expect would under-count silently — hence the "key blocks parsed > 0"
  assertion in `fieldcheck_test.py`, which is the canary, not a guarantee.

## Recommended next step

Extend `SAMPLES` from argv lines to events: one sample per rule *per match key it uses*, so
`path_glob_any` and `endpoint_glob_any` acquire readers. That is a pure test-and-fixture job — no
Go required to write the fixtures, though running the real engine to confirm them still needs the
restored toolchain. It is the single highest-value hour available in Phase 0 right now, and it is
still the pack owner's file to touch.

Vivi 🌺 — 2026-10-23

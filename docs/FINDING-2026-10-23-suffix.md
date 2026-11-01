# FINDING 2026-10-23 — the credential glob aimed one level up, and it fires on the folder instead of the key

**Disposition:** Open — real, and worse than dead: the credential glob fires on the directory, not the key. Fix is the pack owner's; not patched here.
Closes the question I refused to publish at 4:00 PM. It resolves against the rule, not against my port — and the
way it resolves is worse than "dead".

## The question

`'*/.config/openai*'` did not reach `/home/x/.config/openai/api_key` in my transcription, and I could not tell
whether that was a defect of the port or of the pack.

## The answer, from `engine.go:296-304`

`pathSuffixes` appends `s[i+1:]` at every `/` — it only ever **removes leading** segments.

```
/home/x/.config/openai/api_key
  home/x/.config/openai/api_key
  x/.config/openai/api_key
  .config/openai/api_key      <- 3 slash-pieces: '.config', 'openai', 'api_key'
  openai/api_key
  api_key
```

The glob is `'*' / '.config' / 'openai*'` — **three** segments, and its first segment is a single `'*'`, which
under `path.Match` matches one segment and cannot cross `/`. For the glob to fit, the *first* segment of the
string must be exactly `'.config'`. The path's first segment is `home`. Every suffix that begins `.config/` is
three segments — `.config/openai/api_key` — and `'openai*'` must cover two of them. **It misses, in Go, exactly
as in neargo.** `scripts/suffixprobe.py`: 7 globs × 12 paths = 84 pairs; the canonical key path is reached by
**0** of the 7 globs.

So last hour's hedge was wrong in my favour: the transcription was right and the rule is what's missed.

## What it hits instead, which is the actual defect

```
/home/x/.config/openai        -> '*/.config/openai*' fires      (a directory, no key file)
/home/x/.config/openai/api_key -> 0 globs                        (the key file, in that directory)
```

A credential rule that fires on the *folder* and not on the *file inside it*. Same shape as this morning's
`*/etc/cron*` vs `/etc/cron.d/evil` — a glob aimed one level off. Twice in one day, in different rules, which
starts to look like a pattern in how this pack was written rather than two unlucky spellings.

The rest of the rule is thin as well: 12 realistic layouts, **3 reached** — and two of those three are the
wrong kind of path (`/home/x/.config/openai` the directory, `x/.config/openai` the same thing relative). Of the
credential stores an agent actually carries, `/home/x/.openai/api_key`, `/etc/openai/api_key`,
`/home/x/.config/openai/completions/api_key` and `/root/.aws/credentials` are all unreached.

`'*anthropic*key*'` reaches exactly one layout of three — `/opt/anthropic_api_key`, where the words happen to be
adjacent. The two-segment layouts (`~/.config/anthropic/api_key`, `~/.anthropic/key`) do not fire.

## Why this is still not a patch, and is a bigger finding than a glob

None of this is observable today, because `secret.read_model_key` never executes at all — the eBPF `file.access`
adapter is a stub in this build (R2.2), which 524 established. So the rule is **misspelled and silent**. If the
globs were corrected tomorrow, it would still be silent. The sensor is the finding; the glob is the second
finding; fixing the glob without the sensor buys nothing, and that ordering matters when it reaches the pack
owner:

1. **R2.2 eBPF adapter** — without it the rule is inert no matter what its globs say (Dad's provisioning call).
2. **This glob** — after (1), it must be spellable to reach a key file, not the directory holding it.
3. **What `detail["path"]` carries for a model-key read** — a file path or a directory watch. If the adapter
   reports a *directory* watch, the current glob is accidentally correct and my whole reading inverts. **That is
   the one assumption here I cannot test on this box**, and it goes on the list with the auditd PATH ordering.

## Two more of my own wrong answers, recorded

1. I asserted the glob was "2 segments". It is 3. My first count was right and my reading of it was wrong.
2. I asserted `'*anthropic*key*'` reaches *no* anthropic layout. It reaches `/opt/anthropic_api_key`. Both were
   caught by the test's failure output, not by my reasoning — which is now the third hour running where the
   printed table, not the intent, was what corrected me.

`scripts/suffixprobe.py` + `scripts/suffixprobe_test.py` (**16/16**). No change to `rules/` or `internal/`.

Vivi 🌺 — 2026-10-23, ~4:50 PM ET

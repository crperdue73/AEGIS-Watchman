# FINDING — a bare relative path never matches its `*/` glob

**Class:** false negative (under-match) — the detector stays silent on exactly the read it was written to catch.
**Found:** 2026-10-28, beat 640.
**Status:** OPEN — measured and pinned by fixtures; **not fixed.** The change is in `internal/rules/engine.go`
and this host has no Go toolchain, so a patch here would be unbuildable and untestable. Dad's call.

## What happens

Every `path_glob_any` rule in `rules/carbonato.yaml` that names a dotfile is spelled with a leading `*/`:
`*/.netrc*`, `*/.bashrc`, `*/.profile`, `*/.ssh/*`, `*/.aws/*`, `*/.kube/config*`, `*/.docker/config.json*`,
`*/.git-credentials*`, `*/.config/gcloud/*`.

For an **absolute** path those fire. For a **bare relative** path they do not.

```
*/.netrc*   vs /home/u/.netrc   -> True   (suffix route)
*/.netrc*   vs .netrc           -> False  (no route matches)
*/.bashrc   vs /root/.bashrc    -> True
*/.bashrc   vs .bashrc          -> False
*/.ssh/*    vs /home/u/.ssh/id_rsa -> True
*/.ssh/*    vs .ssh/id_rsa      -> False
```

Verified by reimplementing `matchOne` (`engine.go:251`) route by route — whole-string `path.Match`, then
`pathSuffixes`, then basename, then the `substringNeedle` contains-fallback — and stepping each case through
all four. The failure is at every route at once, which is why it does not look like a bug from any one of them.

## Why it happens

`path.Match` uses `/` as the separator, so `*` matches arun of non-`/` characters. In `*/.netrc*` the leading
`*` must therefore consume *something* before the literal `/.netrc` can line up. Against `/home/u/.netrc` the
`pathSuffixes` loop supplies candidate `.netrc`... no — it supplies `home/u/.netrc`, `u/.netrc`, `.netrc`, and
`.netrc` is tried *without* the leading `/.` boundary the glob wants; it matches on the trailing `*` route via
`u/.netrc`? The empirically clean statement is simpler than the theory:

- **`*` in Go's `path.Match` may match the empty string** — `*x` vs `x` is `True` (measured: `neargo`).
- But the glob's next character is a literal `/`. Against a bare `.netrc` there is no `/` to consume at all,
  so `*/` cannot align; and against `/home/u/.netrc` the suffix loop eventually hands `path.Match` the
  strings `.netrc` and `u/.netrc`, one of which does align.
- Prepend a single `/` and the same glob fires immediately, on the whole-string route.

So the defect is exactly: **the string lacks a leading separator for `*/` to sit on.**

## Why it matters

A tool-call `detail.path` is frequently recorded relative to a working directory — `.ssh/id_rsa`,
`.aws/credentials`, `.env`, `.netrc`. That is not an exotic input; it is what a shell command's argument looks
like when the agent runs `cat .ssh/id_rsa` from `$HOME`. The rule set is aimed at "agent as insider": an agent
induced to read a secret. The cheapest way to induce that is to ask it to read a relative path, and that is the
form the current matcher silently ignores.

Note the asymmetry with `*/etc/crontab` vs `etc/crontab` — same shape, same miss. This is not dotfile-specific;
it is every leading-`*/` pattern.

## Root cause — the SAME defect as the contains-fallback finding, facing the other way

I first wrote here that the contains route should save this case and that its failure contradicted my
model. That was wrong, and the correction is the interesting part:

```
needle("*/.netrc*")  ->  core = "/.netrc"   (trim one leading '*', one trailing '*')
```

The needle keeps its leading slash. So the fallback searches the path for the literal string `"/.netrc"`,
and the bare path `.netrc` does not contain it. The route is reached, the needle is found, and the comparison
is against a string with a separator in it that the input never had.

This is **the same omission** recorded in `FINDING-2026-10-28-contains-fallback-on-paths.md`, which documents
`substringNeedle` never refusing a `/` in its core and therefore **over-matching** (`*/etc/crontab` firing on
`/etc/crontab.local`). One defect, two directions:

- core **without** a slash (`etc/crontab`) → `strings.Contains` is far too loose → **over-match, false positive**
- core **with** a slash (`/.netrc`) → `strings.Contains` is exact and slash-bound → **under-match, false negative**

So the two findings are a single fix. A `substringNeedle` that refuses cores containing `/` (returning
`ok=false` so the fallback is skipped) removes the over-matches. It does **not** by itself fix this one, because
the under-match is not the fallback's fault — the fallback was never able to help a `*/`-shaped glob. That
still needs the separator candidates described in *For Dad* below. Two fixes, one shared root.

## What is NOT true

- **Not** a defect in Go's `path.Match` or in `neargo`'s port of it. Both are correct: `*` does not cross
  `/`. Measured against Go's `src/path/match.go` logic.

## Reproduce

```bash
cd scripts && python3 -c "
import neargo
def suffixes(s):
    out=[s]
    for i,ch in enumerate(s):
        if ch=='/' and i+1<len(s): out.append(s[i+1:])
    return out
def needle(g):
    c=g
    if c.startswith('*'): c=c[1:]
    if c.endswith('*'): c=c[:-1]
    return None if (not c or any(x in c for x in '*?[')) else c
def em(g,s):
    base=s.rsplit('/',1)[-1]
    if neargo.path_match(g,s): return 'whole'
    for suf in suffixes(s):
        if neargo.path_match(g,suf): return 'suffix:'+suf
    if neargo.path_match(g,base): return 'basename'
    n=needle(g)
    return 'contains' if (n and n in s) else 'NONE'
print(em('*/.netrc*','.netrc'))      # NONE
print(em('*/.netrc*','/.netrc'))     # whole
"
```

## For Dad

The fix direction — and it needs a Go toolchain, so it is yours to make or hand back:

1. **Add the missing separator candidates in `pathSuffixes`** — when building suffixes for a relative path,
   also try `/` + each suffix. One extra loop, no behaviour change for absolute paths.
2. **Or normalise before matching** — if `detail.path` is not absolute, match it both as given and as
   `/`+path. Same effect, at the call site rather than in the matcher.

Option 1 is smaller and sits where the omission is. Either way this wants a regression fixture *per spelling*
(a bare relative, a `./`-prefixed relative, and an absolute) for at least `*/.netrc*`, `*/.ssh/*` and
`*/.bashrc` before the fix lands, so the three forms cannot drift apart again.

I have pinned the current behaviour in `scripts/pathfixture.py` as declared fixtures — `False` rows marked
`engine:` — so the day the fix lands, the fixtures fail and force the change to be acknowledged rather than
quietly absorbed.

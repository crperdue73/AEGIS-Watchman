# FINDING 2026-10-25g — the disease is one function, and it is not about binds

**Status:** OPEN — measurement only, no code changed. Superseded in part by
`FINDING-2026-10-25h-where-the-blindness-stops.md`, which measures how far this actually reaches.
**Instrument:** `scripts/neargo.py` (line-for-line transcription of `engine.go:237-304`) and
`scripts/bindglob_test.py`; Go source read, not executed — no toolchain on this host.

Beat 578. Callie (6474) named it better than I had: *"the matcher is answering a question about tails
of bind specs because nobody told it what a path is."* She's right about the shape and I want to push it
one step further, because "binds" is too narrow. **Nothing in the matcher knows what a path is.**

## The real cause: one string is treated as both a path and a command line

`anyGlobMatches` (engine.go:251) is handed three kinds of thing and applies the same three candidates
to all of them:

| caller | what it passes | is it a path? |
|---|---|---|
| `detail_glob_any` | `binds` = `"/:/host,/data:/data"` | **no** — comma-joined `src:dst` specs |
| `path_glob_any` | one file path | yes |
| `argv_any` | one argv element, plus the joined line | **no** — a token, or a whole command |

It then tries: whole string, **every suffix after a `/`** (`pathSuffixes`), basename, and a substring
fallback. `pathSuffixes` is the piece that assumes "this is a path," and it runs on all three.

## Two measured consequences, both new in this beat

**1. A bind spec satisfies a persistence rule.**

```python
match_one('*/etc/crontab', 'x:/etc/crontab')  -> True
match_one('*/etc/cron*',   'x:/etc/crontab')  -> True
```

`docker run -v x:/etc/crontab:/dest` — mounting a file *onto* `/etc/crontab` inside a container — is not
writing a crontab on the host. The rule is `carbonato.persist.cron_or_rc`, likely critical. **A container
that mounts a cron file into itself looks like host persistence.**

**Correction to my own first explanation** (I checked it instead of shipping it): I said this was the
`pathSuffixes` artefact. It is not. `path_match('*/etc/crontab', 'x:/etc/crontab')` is **True on the
whole string** — segment one is `*`, which happily absorbs `x:`, and the rest is literal. So there are
*three* distinct mechanisms in one function:

1. **suffix split** (`pathSuffixes`) — `/:/host` → `:/host` reaches `*:/host*`;
2. **wildcard-over-the-separator** — `*` absorbing `x:` so a `src:dst` spec matches a path glob on the
   whole string (`x:/etc/crontab` → `*/etc/crontab`);
3. **joined line** — argv's multi-element candidate.

I originally attributed (1) to a case that is (2). Both are real; they are not the same bug, and a fix
aimed at `pathSuffixes` alone closes #1 and leaves #2 wide open. **Proof: the mutant that deletes
`pathSuffixes` leaves all five crontab assertions green** — see the mutation section.

**2. `argv_any` does not save you, and the reason is specific.**

`neargo.any_argv(['*/etc/crontab'], ['docker','run','-v','x:/etc/crontab:/dest'])` → **False**. The
glob is tried against each element and against the joined line — the joined line is
`docker run -v x:/etc/crontab:/dest`, and `pathSuffixes` on that yields `etc/crontab:/dest`, never
`/etc/crontab`. So the **argv** route misses. The **bind** route catches it. A detector that fires
through the route you weren't thinking about is the hardest kind to reason about, and it's how this
would have shipped.

## What is NOT true — I checked, and my first instinct was wrong

I assumed "the `/etc/...` anchors protect the argv rules." Then I counted: **103 of 103 globs in the
pack begin with `*`. Zero are anchored.** So there is no anchor doing any protecting. What actually
happens is narrower:

```python
pathSuffixes('/data:/etc/cron.d/evil')  -> ['data:/etc/cron.d/evil', 'etc/cron.d/evil', 'cron.d/evil', 'evil']
path_match('*/etc/cron*/*', 'etc/cron.d/evil') -> False   # no leading '/', so '*/etc' can't be satisfied
path_match('*/etc/cron*/*', '/etc/cron.d/evil') -> True
```

The suffix has to begin **at a `/`** to keep the literal `/etc`. In `x:/etc/crontab` the `/` right before
`etc` supplies it; in `/data:/etc/cron.d/evil` the suffix that would work starts `etc/` without a slash,
and the whole-string candidate fails on segment count. So the artefact is **data-dependent**: it fires
on some bind strings and not others, which is worse than firing on all of them — it looks random.

## Why this matters more than the three globs

Beat 577 found `host_root_mount` had one silent glob and one lying glob. This beat says **`host_root_mount`
was an instance, not the disease.** The same function feeds every `path_glob_any`, every
`detail_glob_any`, and the joined-line candidate of every `argv_any`. Any two of those sharing one string
can produce a match on a boundary that exists only because two unrelated things were concatenated.

I do not have a fix to propose that isn't a design change, and I'm not going to invent one to look
useful. The honest statement is: **the matcher needs to know what kind of string it's holding** —
`path_glob_any` should be path-only, `detail_glob_any` on binds should split on `,` and then on `:`,
and `pathSuffixes` should not run on argv or on bind specs at all. That's a change to the core matching
contract of a critical detector. Pack/engine owner's call, with these measurements attached.

**Mutation evidence for the split:** deleting `pathSuffixes`'s per-segment output (`out.append(...)` →
`pass`) turns exactly 5 assertions red — the `/:/host`, `/etc:/hosts`, `/data:/hostdata`, `/srv:/host`
and socket-only cases — and leaves **all five crontab assertions green.** That is the measurement that
proved mechanisms #1 and #2 are different, after I had written them down as one.

`bindglob_test.py` is now **24 ok / 0 fail**; the 17-ok version was a floor, not a ceiling: it pins the binds case and I'll
extend it with the argv-side cases next beat rather than claim this beat covered them.

Pack untouched: **117 / 39 / 78**.

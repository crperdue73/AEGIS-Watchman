# FINDING 2026-10-27 — interior-slash globs are contains tests, not path globs

**Status:** CORRECTED (beat 608, same beat). Original diagnosis was wrong; see "Correction" below.
**Severity of the surviving item:** Documentation only. No matching change.
**Found:** beat 608, sweeping `engine.go` doc comments after beat 607 found one that overclaimed.

## The surviving defect: comments that describe a restriction the code does not implement

Two doc comments say the contains fallback is restricted to globs with no `/`:

- `engine.go:234` — "*and — for globs that contain no `/` — against the raw string with a fallback
  'contains' test*"
- `engine.go:269` — "*Only applied to globs with no interior `/` beyond the wildcard.*"

**Neither restriction is in the code.** `substringNeedle` (`engine.go:287`) rejects only `*`, `?`, `[`.
Every slash-containing glob gets a needle, and `matchOne` applies the contains test with it.

Nine `path_glob_any` globs are affected. That is a real divergence between the comment and the code, and it
should be fixed — **in the comment**, because the code's behaviour turns out to be correct.

## Correction — my first version of this finding was wrong

I wrote this up as a **medium-severity false positive**: `*/etc/cron.d/*` appeared to fire on
`/tmp/build/etc/cron.d/payload`, a build-directory decoy, and I blamed the contains fallback.

**The decoy does fire — but not because of the fallback.** The match comes from *suffix* matching
(`engine.go:232`, `pathSuffixes`), which is deliberate and documented:

```
suffixes of /tmp/build/etc/cron.d/payload:
  ['/tmp/build/etc/cron.d/payload', 'tmp/build/etc/cron.d/payload',
   'build/etc/cron.d/payload', 'etc/cron.d/payload', 'cron.d/payload', 'payload']

path.Match("*/etc/cron.d/*", "build/etc/cron.d/payload") -> true
```

Suffix matching is the mechanism that makes `*/etc/cron.d/*` work regardless of which root the watched
directory sits under. Narrowing it would break the glob's actual purpose.

**I caught this by mutation-testing my own candidate fix.** I applied fix (a) — adding `/` to
`substringNeedle`'s reject set — expecting my new pins to fail. They did not:

```
mutant */etc/cron.d/* vs /tmp/build/etc/cron.d/payload -> True
orig   same case                                   -> True
```

The fix changed nothing, because the contains fallback was never the cause. Had I published without that
mutation test, I would have shipped a "medium false positive" finding whose proposed fix does not fix it, and
the next person to apply the fix would have found the alert still firing.

## Is the suffix behaviour itself a problem?

**No, and it should not be changed.** A payload dropped at `/tmp/build/etc/cron.d/payload` is not the system
cron directory, so one could argue for `/etc/cron.d/*` anchored at the root. But the pack is written for hosts
where the watched tree may be relative or chrooted, and suffix matching is how `*/etc/cron.d/*` expresses
"an `etc/cron.d` directory at any root".

If the owner wants anchoring, that is a **rule rewrite** (candidate (c) from my incorrect draft): use
`/etc/cron.d/*` for the absolute case. That is a per-glob judgement call about which roots are in scope, not an
engine change — and it is the owner's decision. Nothing here is a bug in the engine.

## Guards

`scripts/endpointglob_test.py` section 9 now pins:

- the header's claim that the contains fallback applies to slash-globs (current, current-but-undesired as
  documentation),
- that `*/etc/cron.d/*` matches its real target, **and** the decoy, **and** that the decoy is reached by
  *suffix* matching rather than the fallback — so a future change to either mechanism is distinguishable,
- that both comments still claim the non-existent restriction.

## Lessons

1. **Mutation-test the proposed fix, not just the guard.** The guard passing proved my pins worked; only
   breaking the code proved my *diagnosis* was right. Here it was not.
2. **A matching result is not a matching cause.** The decoy fired; I inferred which of three fallbacks fired
   from the glob's shape instead of instrumenting it. `engine.go:251` tries four mechanisms in order.
3. **Beat 607's actual error was scope** — I measured two globs and wrote a claim about the population. The
   correction here is the opposite error: I measured the population and misattributed the mechanism.

# FINDING 2026-11-03h — the excuse outlives the reason, so I built a watch on the excuse

**Status:** MEASURED · **FIXED at beat 780** · mutation-proven five ways, `gofmt` clean.
No production code changed; `rules/carbonato.yaml` untouched.
**Instrument:** `scripts/staleclaim.py` + `internal/rules/exeanchor_engine_test.go`.
**Follows** `FINDING-2026-11-03d` (the declaration nobody ran), `-e` (the mirror was faithful),
`-f` (the endpoint family), `-g` (the third family).

## The shape, three times in three beats

| beat | file | the note | why it was written | why it was wrong |
|---|---|---|---|---|
| 776 | `argv_semantics_test.go` | "NEVER BEEN RUN" | no Go toolchain, Oct 08 | toolchain back 11-01 |
| 778 | `needleprobe.py` | "no Go toolchain on this host" | same | same |
| 779 | `argvfixture.py` | "the toolchain is the only path and this host does not have it" | same | same |

Each was found by hand, one at a time, while doing something else. Three for three is not a
coincidence, it is a class: **a note explaining why something cannot be checked outlives the
condition that made it uncheckable, and nothing in this repository was watching for that.**

The obvious response — grep for the words "no Go toolchain" — is the wrong one. A phrase list is a
guess about how people write, it would have caught these three by luck of phrasing, and the fourth
would be written differently. Beat 645's lesson applies: the instrument must ask a question that
has an answer.

## The question

> For every file that says it cannot verify something, **is the cause still present?**

`scripts/staleclaim.py` enumerates causes with a live predicate each:

| cause | predicate run on this box, now |
|---|---|
| `toolchain` | Go toolchain absent from PATH and `/usr/local/go/bin` |
| `ebpf` | no bpffs mount, or no clang |

A file declares its claim in a comment:

```
# STALECLAIM cause=toolchain since=2026-10-08 checked=YYYY-MM-DD
```

Three outcomes, genuinely different: **live** (cause real, note honest), **STALE** (cause gone, note
describes a world that ended → fails), **UNKNOWN** (cause not registered → fails).

The UNKNOWN branch is the important one. A guard that only knows the excuses I thought of cannot
catch the fourth, and the cheapest thing in the world is a tenth excuse. An unregistered cause makes
the guard say *"I don't know what you mean by `cause: wifi`"* instead of passing.

## What it found immediately

`scripts/exeanchor.py:25`, which has carried this since October 08:

> *"No Go toolchain on this host: the engine semantics are mirrored from the cited lines, not
> compiled. When the toolchain returns, this should be replaced by a Go test that calls the real
> `matches`."*

Genuinely stale — the toolchain returned 2026-11-01. The right response was not to delete the note
but to **do the thing it asked for.** `internal/rules/exeanchor_engine_test.go` now calls the real
`exeMatches` and proves its central claim: an exe anchor is literal, not a glob, so
`exe_any: ["*/crontab"]` silently matches nothing while `exe_any: ["crontab"]` works — the difference
between an operator's rule working and doing nothing at all, which is what `exeanchor.py` exists to warn about.

The annotation is updated to `resolved=2026-11-03 by=internal/rules/exeanchor_engine_test.go` and
**left watchable on purpose**: if that Go test is ever deleted, the unverified mirror returns
silently, and a guard that watched the excuse once should keep watching it. `resolved` without `by`
fails, so the resolution has to name an artifact rather than assert one.

## Three defects in the guard itself, all found by running it

1. **It reported on its own docstring.** The regex matched the `STALECLAIM` example inside its own
   prose and produced a phantom unknown cause. Fixed by requiring a comment leader (`#` or `//`) —
   same shape as beat 650's fixture that supplied the answer: the instrument generating its own
   finding.
2. **It exited 0 while printing a FAIL.** Unknown causes only returned non-zero under `--check`, so
   a real failure printed and left the gate green. Beat 646's crash-vs-fail lesson applied to exit
   codes: a guard whose status disagrees with its output is worse than silent.
3. **Its predicate was inverted.** `_go_toolchain_present()` returned True when the toolchain was
   there — so every claim read as "still live" on precisely the box where it was stale. The
   predicate must answer the *cause's* question, not the convenient one.

## Mutation proof (5/5)

| mutant | expected | measured |
|---|---|---|
| M1 add `path.Match` to `exeMatches` | Go test red, names it | `exeMatches("cr*tab", "/usr/bin/crontab") = true — GLOBS NOW WORK` |
| M2 delete `resolved=` from the claim | STALE | `FAIL ... this cause is GONE on this host` |
| M3 rename the cause `wifi` | UNKNOWN, not silently ignored | `names an unregistered cause 'wifi'` |
| M4 pack absent | premise failure | `rules/carbonato.yaml is absent — nothing to protect` |
| M5 control | all green | 1 claim live, rc=0 |

### M1 is the one that matters: my first test could not fail for the reason it named

The first version of `TestExeAnchorGlobIsDead` used `*/crontab` as the glob probe and **passed
against the mutant**. It looked like proof and proved nothing, because `path.Match("*/crontab",
"crontab")` is false *regardless* — the pattern needs a `/` and a basename has none, so that input
cannot tell literal-only from globbing.

I only found this because the mutant was run. A test whose distinguishing case cannot distinguish is
the exact failure this project exists to catch, and I wrote one while writing the catcher. It now
uses `cr*tab` — a glob that *would* match the basename if globbing were enabled — and fails by name.

**Second time in two beats** (beat 779: a deleted escape branch no test could reach). The pattern in
both: *the test was written against the behaviour I expected, not against the behaviour that would
differ if the code changed.*

## What this does NOT claim

- **Not that the tree is free of stale claims.** Only annotated claims are visible to this guard.
  The 15 other files that mention the toolchain have not been audited; the list is in the beat 780
  WORKLOG entry and is queued. Grep found them; the guard watches the ones that declare themselves.
- **Not that `CAUSES` is complete.** It knows two causes. A third excuse would be caught only as
  UNKNOWN if someone remembers to register it, which is the honest limit — the guard can refuse an
  unknown cause, but it cannot know one exists.
- **Not that `exeanchor.py` is retired.** It still does pack lint (mode 4) and argv routing, which
  the Go test does not. Each now covers the half it is good at.

## Lesson

*Don't sweep for a recurring defect; build the detector for it.* Beats 776 and 778 each ended with
"eleven more files carry this, queued", and the queue did not shrink because sweeping is work that
only happens when someone remembers. The guard turns remembering into a gate. That is the whole
difference between having found a thing three times and having fixed the class of thing.

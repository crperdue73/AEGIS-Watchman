# FINDING 2026-10-27 — `packcheck.py` carried the same `/`-refusal I fixed in `neargo.py`, and it was calling my fix dead

**Status:** fixed. `scripts/packcheck.py` corrected; `packcheck_test.sh` case C re-derived; `packpresent.py` added (new guard, new suite).

## Where this came from

Not from a ticket. From my sister Nadia, who posted to the family floor that her own "clean" check had
been green because it pointed at the wrong directory:

> "a clean check pointed at the wrong folder looks exactly like a quiet day, and it would have passed."

Her rule — *verify the path before trusting the silence* — is the reason I moved `rules/carbonato.yaml`
off disk and re-ran every script. Four suites that read the pack still exited 0 with nothing to look at.
Chasing that led somewhere I did not expect.

## The real defect

`scripts/packcheck.py` — the tool that reports "N dead globs" and is wired into `make preflight` — had
this in its `match_one`:

```python
# contains-fallback, only for slash-free *needle* globs
if "/" not in glob and glob.startswith("*") and glob.endswith("*"):
    if glob[1:-1] in s:
        return True
```

`engine.go:281` `substringNeedle` has no such refusal:

```go
core = strings.TrimPrefix(core, "*")
core = strings.TrimSuffix(core, "*")
if core == "" { return "", false }
if strings.ContainsAny(core, "*?[") { return "", false }
return core, true
}
```

It trims one leading and one trailing `*`, guards only `*?[`, and **`/` is not special**. The "no interior
`/`" idea exists only as a comment in `matchOne`/`anyGlobMatches` and is never implemented in the code.

This is the identical defect I found and fixed in `neargo.py` at beat 610. I fixed the port I was
looking at and did not sweep for the others. There were three copies of the stale rule:

| file | line | verdict |
|---|---|---|
| `scripts/packcheck.py` | 73 | **real bug — fixed** |
| `scripts/endpointfixture.py` | 172 | deliberate: it models the *old* mirror to demonstrate the divergence. Left alone. |
| `scripts/segmentaudit.py` | 76 | neededle-vs-path classification heuristic, not the engine rule. Left alone. |

## Why it matters — measured, not argued

**27 of 74** `*…*` globs in the pack had the contains route disabled inside `packcheck.py`:

```
* -O /home/*     *.ssh/id_*      */.aws/*          */.bashrc*
*/.docker/config.json*           */.git-credentials*   */.kube/config*
*/.netrc*        */.ssh/*        */etc/cron.d/*     */etc/cron.daily/*
*/etc/systemd/system/*           *bscscan.com/api*  *etherscan.io/api*   ... (27 total)
```

Most are path globs, which the suffix chain rescues — so the bug was usually invisible. It was **not**
invisible for `argv_any` globs, where matching is on whole argv elements and a space-joined line:

```
glob            sample                            neargo  packcheck
*-v /:*         docker run -v /:/host img         True    False     <-- DISAGREE
```

That single divergence is why `packcheck.py` reported my beat-613 fix (`*-v /:*`, which replaced the
dead `*-v /*:/`) as **dead**: `packcheck: 39 globs checked, 4 dead`. The glob was fine. The instrument
could not see the route. After the fix:

```
packcheck: 39 globs checked, 0 dead, 0 rule(s) skipped for want of samples
```

**Four "dead globs" were one broken copy of the engine.** I nearly filed them as pack defects.

## My own two mistakes, on the record

1. **My first patch was half a fix.** I required *both* a leading and a trailing `*` before taking the
   needle — but `engine.go` accepts `*x` as well as `*x*`. Diffing against `neargo` (not reading my own
   patch back) showed **24 single-star globs still disagreed**. Corrected to trim each star independently.

2. **A guard I wrote this beat broke its neighbours.** `packpresent.py` moved the real pack aside to
   test whether suites notice. Run alone: fine. Run under `suitecheck.sh`: it failed *other* suites that
   read the pack mid-run. A guard that damages the thing it guards is not a guard. Fixed by building a
   **hardlink mirror** inside the repo (same filesystem — `/tmp` is tmpfs here, so `os.link` gave EXDEV)
   and deleting the pack from the mirror only. The real pack is never touched, asserted in the suite.

## What `packcheck_test.sh` was actually asserting

Case C demanded that `*/etc/cron*` **FAIL** on the sample `/etc/cron`, on the reasoning that "the trailing
`*` cannot cross `/`". That is false — `*/etc/cron*` catches `/etc/cron.d/x` through the needle
`/etc/cron`. The test was pinning the port's bug as the specification, which is the same failure mode as
the stale transcriptions at beat 610: **a check that asserts the instrument's current output cannot
detect that the instrument is wrong.**

Rewritten: case C now asserts the contains route makes the glob **LIVE**, and the genuinely-dead case is
B (a needle no path contains). `packcheck_test: 4 behaved, 0 misbehaved`.

## New guard: `scripts/packpresent.py` (+ `packpresent_test.py`)

Asserts that **no suite reports green over a missing rule pack**. Runs every script twice — once with
the pack present, once with it absent from a hardlink mirror — and requires that any script still green
with no pack is either genuinely pack-free (14 declared, each with a hand-checked reason) or a failure.

Design history, because three designs were wrong first: regex-matching `PACK = os.path.join(...)` →
43 false "unexplained"; evaluating every `os.path.join(...)` → 32; importing the module and reading its
`PACK` attribute → 32 (most modules have no `PACK`; they use `fieldcheck.PACK` or open the path inline).
Every attempt was a *static approximation* of a question the script can answer itself, and each was
wrong in a way only visible by checking its output against reality. **A checker that understands only
some spellings of the thing it checks is the same pathology it is hunting.** So it runs them.

`packpresent_test.py` mutation-tests the guard: M1 pack deleted, M2 empty, M3 zero rules, M4 rule with no
globs. All four caught and named. M1 also exposed the guard **crashing** with a traceback when the pack
was already absent — it now answers cleanly. A guard that dies with a stack trace reports a fault as an
accident rather than a finding.

## What this beat did NOT fix (real, and separate)

Six scripts are red **with** the pack present, for reasons that are genuine repo findings, not artifacts:

- `docsindex.py` — the finding index is stale (`--write` needed)
- `endpointfixture.py` — `*bscscan.com/api*` and `*etherscan.io/api*` are unreachable endpoint globs
- `fpprobe.py` — 4 unexpected results across 36 commands
- `dispose.py` — `internal/` mtime assertion
- `greenprobe.py` — 2 guards on the exit-0 path with no ruling
- `guardreach.py` — 29 guards print no denominator

`packpresent.py` reports these as out-of-scope context rather than failing on them, because otherwise one
stale index would mask every question about the pack. **They still need fixing; they are not excused.**

## Gates

`bash scripts/suitecheck.sh` → **50 suites held, 1196 checks ok, 0 reported failures, none below baseline.**
(No Go toolchain on this host: this proves the script tests ran and passed, NOT that the Go tests do.)

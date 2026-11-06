# CENSUS — 2026-11-02e: the instrumentary audit (beat 747)

**Status:** measured this beat. **Nothing to fix.** This is a census, not a finding.
**Instrument:** `scripts/gateaudit.py` (wired into `suitecheck.sh`).


## Why audit the auditors

Beats 725–746 produced 20+ census scripts. Every one of them was mutation-proven when written, and every one has
been green for many beats since. That is exactly the condition in which a guard stops being run and starts being
*assumed* — the failure mode that beat 647 found in `suitecheck_test.sh` (a test of the gate invoking `sh` on
bash-only code, reporting "caught" for weeks) and beat 648 found in M5 (green against the very defect it existed to
catch).

So this beat asked three questions of the instrumentary itself, in increasing order of usefulness.

## 1. Are all my recent instruments actually in the gate?

```
walkerrorcensus          gate=1 run=green
modtimecensus            gate=1 run=green
alertbulkcensus          gate=1 run=green
retainfailcensus         gate=1 run=green
keycollisioncensus       gate=1 run=green
manifestkeycensus        gate=1 run=green
fleetcoveragecensus      gate=1 run=green
ingestceilingcensus      gate=1 run=green
```

**Yes — 8 of 8 wired and green.** Recorded as a zero. This question was already answered by beat 640's discovery that
`pathfixture.py` and `redundant_mut.py` were "in the suite directory but in no gate" — the wiring has held since.

## 2. Does every script in the gate have a path to a non-zero exit?

68 distinct scripts are named in `suitecheck.sh`. Swept all 68 for `sys.exit(1)` / `return 1` / `exit 1` /
`raise SystemExit(1)` / `Fail` / `FAIL`:

**68 of 68 have a red path. Zero exceptions.**

This was worth asking because a script that can only print `ok` is not a check, it is a log line with a `#!/usr/bin/env`
shebang. **No such script exists in this tree.** That is a real zero, and a zero recorded is worth more than another
green.

### Own mistake, caught mid-audit

My first pass counted `fail()` **function calls** as the red-path test and flagged `debriscensus.py` and `claimcensus.py`
as having none. That was the grep being wrong, not the scripts: `debriscensus` exits via `print(...)` + `return 1` on
four distinct conditions — missing Makefile/README anchors, UNKNOWN modules, and (beat 636) fewer than 20 modules
found. It has **more** red paths than most. **A checker must not assume one spelling of failure** — the same error shape
as beat 645, where the stale-premise detector only recognised `ContainsAny` and could not see `strings.Contains(...)`.

## 3. When a new instrument goes red, does it say *which* claim broke?

The useful question, and the one that separates a checklist from a detector. Re-applied beat 746's M2 mutation (log
once per cycle instead of per event) and read the full output:

```
ok   A gap: retain appends ONE RECORD PER EVENT in a loop ...
FAIL A: the per-failure log changed — re-derive
ok   B gap: `retain` returns NOTHING — quoted from the signature ...
ok   B gap: Ingest ends `return &IngestResult{...}, nil` ...
ok   C gap: the comment says "Delivery failures are logged, never fatal ..."
ok   D latent: 2 sites guard with `err != nil && s.logger != nil` ...
ok   D latent: and NewService leaves logger nil ...
ok   E sound: resolveActors runs BEFORE retain ...
```

**One FAIL, thirteen still `ok`, and the failure names the claim that broke.** Not a cascade. That is what I wanted to
know and could not have known from the exit code — `rc=1` alone would have been indistinguishable from a script that
crashed on an import, which is exactly the defect beat 645 found in `needleprobe.py` and beat 646 fixed in
`suitecheck.sh`'s CRASH-vs-FAIL split.

## What this census does NOT establish

- **Not** that green means correct. Every one of these instruments asserts *current* state; if the underlying code
  changed in a way both the code and the check agreed on, they'd stay green together. Mutation proof is the only
  defence against that, and it is per-beat, not continuous.
- **Not** that all 68 scripts are *necessary*. A script in the gate that can go red but checks something trivial is
  still furniture. This audit asked "can it fail", not "is it worth failing".
- **Not** re-run of the historical mutation proofs. They were run when written; re-running all of them is a beat of its
  own and would be the honest next step if the instrumentary grows much further.

## Sound parts

- `debriscensus.py` refuses to publish a verdict when its load-bearing anchors are missing, and (beat 636) fails on a
  shrunken module set — "a census of almost nothing is not a clean census." **That is the best-written guard in the
  tree** and it predates most of the rules I've been re-learning since beat 725.
- `suitecheck.sh` distinguishes CRASH from FAIL (beat 646) and checks its own baseline (beat 647/648).
- The gate prints its own scope honestly: *"this proves the script tests ran and passed; Go tests are a separate gate"*
  — corrected this beat-series after twenty beats of claiming no toolchain existed.

---

## Beat 748 — the archive check could not see three of its own documents

Found while closing beat 747. `scripts/docsindex.py` globbed **`FINDING-*.md` only**, so every
`CENSUS-*.md` was invisible to it — and it still printed *"archive consistent: 150 findings indexed"* over documents
it could not see. **Three census documents sat unindexed while I reported the index current** (beats 723, 725, 747).
This is the same pathology the product is about: a check reporting success over a population it never enumerated.

The fix was four steps, and **three of them were my own mistakes found by the fix itself**:

1. **The glob** now covers `FINDING-*` **and** `CENSUS-*`. Immediately three documents went red.
2. **The instrument regex** demanded a `scripts/` prefix, so `` `claimcensus.py` `` (a bare basename, an entirely
   normal way to write it) read as "names no instrument" — **an accuser pointed at an honest document.**
3. **The widening was too greedy**: `(?:scripts/)?…(census|claim|probe|check|audit|fixture)…\.py` matched *prose*, and
   **sixteen honest documents were called liars.** Constrained to backticked code spans.
4. **The existence check** then still rejected bare basenames, because it tested `os.path.join(ROOT, name)`. A bare name
   now resolves against `scripts/` before being declared missing — the check is *"does this instrument exist"*, not
   *"did the author spell out the path"*. **Sixteen false accusations cleared, and the real finding stood.**

**Measured:** `archive consistent: 153 findings indexed` (was 150 — the three censuses). M1 (new unwatched doc) → red.
Control green, md5 verified.

### New permanent check

Because widening a glob fixes *today* and nothing stops the **next** unlisted prefix, `docsindex.py` now compares the
indexed population against an **independent walk of `docs/`**. A set comparison, not a count (beat 734).

### A weakness in a check I did NOT fix, stated rather than papered over

Mutating a census doc to delete its `**Instrument:**` field left the check **green** — because the doc names six
instruments in prose and the check accepts "the first instrument named anywhere". The field is therefore not enforced;
it is merely satisfied. That is beat 735's species (**a declared variable is not an enforced rule**) sitting inside the
archive checker. Not fixed this beat: making it strict would redden documents across the archive and is a bigger
decision than a heartbeat. **Recorded so the next reader knows the green means less than it looks.**

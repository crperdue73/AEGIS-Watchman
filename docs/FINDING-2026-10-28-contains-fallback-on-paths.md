# FINDING-2026-10-28: the contains-fallback is applied to paths, and it makes five path rules over-match

**Date:** 2026-10-28 (beat 634)
**Found by:** adding the missing *negative* fixtures to `scripts/pathfixture.py`
**Class:** over-match — the detector fires on a file that is not the file the rule names.
**Status:** OPEN — measured and recorded as 7 declared XFAIL in `scripts/pathfixture.py`; not fixed. The fix
is a one-line change to `substringNeedle` in `internal/rules/engine.go` and needs a Go toolchain this host
does not have, so it goes to Dad rather than into an unbuildable commit.
**Severity for an intrusion detector:** this is the dangerous direction. A false positive here is not a
cosmetic annoyance; it is an analyst's pager going off on `/home/agent/.zshrc.bak`.

## What happened

`pathfixture.py` had 34 fixture rows: 31 saying "matches", 3 saying "does not match". I needed a check that
this file can *say no*, so I added one (beat 634): run the same fixture table against deliberately broken
matchers and require a majority disagreement. That check passed. Then I noticed why it passed so easily —
a matcher that answers `True` to everything only disagreed with **3 of 34 rows**. A table that is 91% yes
cannot reject a permissive matcher, so it had never tested the thing its own title claims ("a reader for
the 30 path globs").

Fixing that meant writing the negative fixtures the globs actually imply — 16 of them, each a real path a
real attacker would use. **Seven failed.** Not because my expectations were wrong. Measured against the
port, and against `internal/rules/engine.go` itself:

| glob | path | expected | got |
|---|---|---|---|
| `*/.ssh/*` | `/home/agent/.ssh/` | False | True |
| `*/.aws/*` | `/home/agent/.aws/` | False | True |
| `*/.netrc*` | `/home/agent/.netrcfile/example` | False | True |
| `*/.zshrc` | `/home/agent/.zshrc.bak` | False | True |
| `*/.bashrc` | `/home/agent/.bashrc.bak` | False | True |
| `*/.git-credentials*` | `/home/agent/.git-credentials-backup/x` | False | True |
| `*/etc/crontab` | `/etc/crontab.local` | False | True |

## Why — and it is not a porting bug

I assumed I'd find my own transcription error. I did not. Tracing each case through `packcheck.match_one`
shows the exact route:

* `*/.ssh/*` vs `/home/agent/.ssh/` — whole-string match **False**, suffix loop matches on `agent/.ssh/`, so
  the *suffix loop* is the trigger (the trailing slash leaves an empty last segment that `path.Match` will
  not let `*` consume on the full path, but the suffix `agent/.ssh/` still has `agent` for the star).
  **This one is the suffix loop, faithful to `engine.go`'s `pathSuffixes` + `path.Match`.**
* The other six — whole False, suffix loop **empty**, so the trigger is the **contains fallback**.

`engine.go:290` `substringNeedle` trims one leading and one trailing `*` and, if what remains has no
`*?[`, returns it as a literal needle. `matchOne` then does `strings.Contains(s, needle)` — on **any**
string, with no check that the call came from a *path* context. So:

* `*/.zshrc` → needle `.zshrc` → `Contains("/home/agent/.zshrc.bak", ".zshrc")` → **true**
* `*/.bashrc` → needle `.bashrc` → `.bashrc.bak` contains it → **true**
* `*/.git-credentials*` → needle `.git-credentials` → `-backup/x` contains it → **true**
* `*/etc/crontab` → needle `/etc/crontab` → `/etc/crontab.local` contains it → **true**
* `*/.netrc*` → needle `.netrc` → `.netrcfile/example` contains it → **true**

`anyGlobMatches` is called from `engine.go:131` for `m.PathGlobAny` with the action's `path` — a genuine
filesystem path — using the same function that serves `EndpointGlobAny` (line 138, domain-shaped strings)
and the argv candidates (line 223, command lines). The header comment on `anyGlobMatches` says the
contains fallback exists *"for globs that contain no '/' … so `*slack.com*` still matches a full URL"*.
**That is a domain-and-command-line need.** `*/.zshrc` and `*/etc/crontab` contain `/`, and they still get
the needle treatment, because `substringNeedle` only refuses needles containing `*?[` — not needles
containing `/`.

The port is faithful. The engine's own comment describes an intent (domain globs) that its code does not
restrict to (paths too). That is the more interesting kind of defect: the comment says one thing, the code
does another, and every test agreed with the comment.

## What this means for the pack

Five `path_glob_any` rules fire on files they do not name:

* `carbonato.persist.file_write` — `*/.zshrc`, `*/.bashrc`, `*/.git-credentials*`: fires on `.zshrc.bak`,
  `.bashrc.bak`, `.git-credentials-backup/*`. Writes to a backup are *not* persistence tampering, and
  `.zshrc.bak` is exactly what a careful attacker leaves behind by accident (and what a careless admin
  leaves behind constantly).
* `carbonato.secret.read_*` — `*/.netrc*`: fires on any path containing `.netrc` as a substring, including
  a directory named `.netrcfile/`, i.e. it reads `$HOME/.netrcfile/example` as a credential access.
* `*/.ssh/*` and `*/.aws/*` fire on the bare directory (`/home/agent/.ssh/`) — a `chmod` or `ls` on the
  directory is indistinguishable from touching a key inside it.

None of these are hypothetical: `.bashrc.bak` is generated by `sed -i.bak`, and `~/.ssh/` is touched by
every `ssh` invocation.

## What I did NOT do

**I did not change the fixtures to match the engine.** All seven are recorded as `xfail` with the measured
cause (suffix loop vs contains fallback) so the suite stays honest — red where the engine is wrong, with
the reason attached — rather than turning green by adopting the defect as the expectation. That is the
same rule this project keeps relearning: an expectation derived from the implementation tests the
implementation against itself.

**I did not change `engine.go`.** The Go toolchain is absent on this host (`go`, `gofmt`, `make` all
gone), so any edit to the engine is unbuildable and untestable here — it would be a claim, not a fix. The
one-line shape of the fix is `substringNeedle` returning `ok=false` when the needle contains `/`, which
would restrict the fallback to the domain/argv case its comment describes and leave path matching to
`path.Match` + suffixes. **That is a behaviour change to a shipped detector and it is Dad's call, not
mine.** It would also, I expect, turn some `endpoint_glob_any` behaviour red — which is why it needs a
build box, not my opinion.

## The instrument defects this exposed (fixed here)

1. `pathfixture.py` could not say no — 3 negatives in 34 rows, and no check that the table can reject a
   wrong matcher. Now: 16 negatives + two mutation checks (a matcher that says yes to everything; a matcher
   that ignores the glob) that the table must reject.
2. **Nothing in the tree ran `pathfixture.py` at all.** The census below found it as an *orphan*: 0
   references in `scripts/`, the Makefile, or any `*_test`. The only reader of the 30 path globs was
   invoked by hand, by memory. Same for `redundant_mut.py`, the mutation proof for `redundant_test.py`.
   Both are now wired into `suitecheck.sh`, with the baseline counts recorded.

## How this was found

Not by reading `engine.go` — I read it after, to explain a measurement. It was found by
`debriscensus.py` (written this beat) asking a question no file in this tree answered: *which of the 56
non-test modules has a reader?* The census classified 56/56 with zero unknowns and surfaced two orphans;
one of them, when finally executed, over-matched seven paths on its first honest run. A file nobody runs
cannot report a defect, and this one had been silent for as long as it had existed.

## Addendum — what landed (same beat)

* `pathfixture.py`: 16 negative fixtures added (50 rows total); 7 declared `XFAIL` with the engine route
  in each entry; two mutation checks added so the table must reject (a) a matcher that says yes to
  everything, (b) a matcher that ignores the glob. A third check fails if any XFAIL entry starts passing,
  so the list cannot outlive the defect.
* `suitecheck.sh`: `SELFHARNESS` — `pathfixture.py` and `redundant_mut.py` now run under the gate. Before
  this beat neither was invoked by anything in the tree.
* `needleprobe.py`: reads `engine.go` relative to its own file, not `$PWD`. It had been crashing under
  suitecheck, and `needleprobe_test.sh`'s mutation section read the crash as the expected red.
* `needleprobe_test.sh`: paths resolved against repo root.
* `packpresent.py`: `debriscensus.py` declared pack-free with a reason.
* `suitecheck.sh --update-baseline`: **60 suites, 1369 checks.**

Still open, and not mine to close: whether `substringNeedle` should refuse a `/` in the core. That is a
behaviour change to the shipped engine, needs a Go toolchain this host does not have, and would move
`endpoint_glob_any` behaviour too — so it goes to Dad with the measurement attached rather than into a
commit.

---

## RETRACTION / MATERIAL CORRECTION — beat 644, 2026-10-28

This finding recommended fixing `substringNeedle` to refuse a `/` in the core. **That recommendation is
wrong for the endpoint family, and I nearly sent it to Dad as a one-line fix that would have silently disarmed
every exfiltration rule in the pack.**

Measured, beat 644. Take the positive case that each endpoint glob exists to catch and ask what the engine
would do with the contains fallback removed:

| glob | its own obvious URL | whole | suffix | base | **after the "fix"** |
|---|---|---|---|---|---|
| `*slack.com*` | `https://hooks.slack.com/services/T/B/X` | F | F | F | **MISS** |
| `*infura.io*` | `https://mainnet.infura.io/v3/key` | F | F | F | **MISS** |
| `*transfer.sh*` | `https://transfer.sh/abc` | F | F | F | **MISS** |
| `*/api/v2/events*` | `https://api.example.com/api/v2/events` | F | T | F | ok |

**Every `*domain.com*` endpoint glob depends on the contains fallback to fire at all.** A URL is
`https://host/path`; `*` cannot cross `/`, and no suffix of that string begins at the hostname. The fallback is
not a wart on the endpoint family — it is the only route that works.

So the fallback is load-bearing in one family and a defect in the other. Same function, opposite verdicts,
decided by what is being matched:

- **paths** — the fallback is pure loss: every slash-bearing-needle over-match in this document and in
  `FINDING-2026-10-28-glob-negative-coverage.md` comes from it, and *no* path rule needs it.
- **endpoints** — the fallback is the whole mechanism, and removing it disarms ~20 rules.

### What the fix must actually be

Not "refuse a `/`". A single change to `substringNeedle` cannot be right for both callers. The shape has to be
chosen per family at the call site (`anyGlobMatches` already knows which key it is matching — `path_glob_any`
vs `endpoint_glob_any`), or the endpoint rules must be re-expressed in a form that does not need substring
matching at all (e.g. match on a pre-parsed host, which is the real fix and is a pack-format change).

### And the over-match is still real

Six lookalike URLs are flagged today, and they are not sloppiness — they are what `*x*` means:

```
*slack.com*   fires on  https://slack.com.evil.tld/exfil     <- attacker-controlled, deliberate
*slack.com*   fires on  https://notslack.com/x               <- suffix in the name
*infura.io*   fires on  https://infura.io.evil.tld/k
*0x0.st*      fires on  https://0x0.st.evil.tld/x
*pastebin.com* fires on https://pastebin.com.evil.tld/raw
*/api/v2/events* fires on https://x.com/api/v2/events-log
```

The first five are the dangerous direction for a *detector's credibility* (an alert an attacker can trigger at
will), and the sixth is a segment-boundary miss. **No glob of the form `*x*` can express "x is a domain
label."** That is not a matcher bug; it is the pattern language being too weak for the question. The honest
options are a host-aware matcher or accepting these as known false positives with a stated tolerance.

### Status change

This finding's Status is **OPEN** and stays OPEN, but its *recommended fix* changes from
"refuse `/` in substringNeedle" to **"the contains fallback is needed by the endpoint family and must not be
removed globally"**. I am not deleting the original text: it was my real reasoning and it led here. The
correction is the finding.

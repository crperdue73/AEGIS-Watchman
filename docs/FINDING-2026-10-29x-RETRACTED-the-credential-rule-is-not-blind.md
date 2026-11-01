# FINDING 2026-10-29x — the credential rule is NOT blind; I measured the wrong function and nearly shipped a false security finding

**Status:** Instrument added (a local audit script (unpublished)). **The claim in my first draft was WRONG and is retracted below, kept in place.**
**Instrument:** no instrument is published for this finding.
**Original instrument:** a local audit script (unpublished) · **Beat:** 673 · **Follows:** 634/641/644 (contains fallback), 642 (oracle is a mirror)

## The question

Tonight I moved my own bus and portal tokens out of `TOOLS.md` into `memory/secrets/agent-credentials.env` (following
Gemma's control test and her rule: *prove it works from the new place, prove the old place is empty*). Then I asked the
question a detection tool should ask: **would Watchman have seen me read that file?**

## RETRACTED: "the credential rule matches nothing"

**What I measured.** The rule `carbonato.secret.read_order_follows_constraint` matches on 8 `path_glob_any` globs. I ran
them through `packcheck.go_path_match` — the port of Go's `path.Match` — against three realistic paths. Result: **matched
by NOTHING**, all three. Reason: `*` does not cross `/`, so `*.env` only sees a bare filename, and `*credentials*` cannot
match a path containing `/`. The glob list *reads* thorough (ssh, aws, gcloud, kube, credentials, env) and I concluded
five of eight were dead on the only paths a detector receives.

**Why it was wrong.** `path.Match` is not what the engine calls. `matchOne` (rules/engine.go:250-276) tries the whole
string, then **every suffix after a `/`**, then the basename, then a **contains fallback**
(`substringNeedle` + `strings.Contains`). `substringNeedle` trims one leading and one trailing `*`, so `*.env` yields the
needle `.env`, and `strings.Contains(absolute_path, ".env")` is **true**.

**Measured properly** through `packcheck.match_one` (the faithful routing):

| path | matched by |
|---|---|
| `/home/u/.../memory/secrets/agent-credentials.env` | `*credentials*`, `*.env` |
| `/root/.openclaw/workspace-jade/.../agent-credentials.env` | `*credentials*`, `*.env` |
| `/etc/aegis/agent-credentials.env` | `*credentials*`, `*.env` |

**The detector sees me. There is no gap.** Had I written the draft up, it would have been a confident, clean, entirely
false security finding in a project whose whole purpose is not producing those — and its natural fix (drop or re-anchor
the two unanchored globs) would have **disarmed** a working rule. Mutation 2 below proves that concretely.

**Root cause, named plainly:** I imported the right module and called the wrong function out of it. `go_path_match` is the
*inner* primitive; `match_one` is the routing the engine actually performs. That is the tenth instance in this project of
reading something adjacent to the code and calling it the code — and the first one that would have shipped a *false
positive finding* rather than a missed defect.

## What the measurement actually shows: over-match, not blindness

Through the same routing, all of these also fire:

| path | what it is | matched by |
|---|---|---|
| `/usr/share/doc/credentials_help.html` | documentation | `*credentials*` |
| `/opt/app/credentials-guide.md` | a markdown guide | `*credentials*` |
| `/home/x/.env.example` | a template, not a secret | `*.env` |
| `/srv/app/.env.dist` | a template, not a secret | `*.env` |
| `/home/x/node_modules/dotenv/lib/main.env.js` | library source | `*.env` |

This is beats **634 / 641 / 644** again, now confirmed on the credential rule specifically: **the contains fallback is
load-bearing and indiscriminate at the same time.** Remove it and the unanchored credential globs go dead on absolute
paths (measured: with fallback `*id_rsa*` fires on `/home/x/.ssh/id_rsa`; with `path.Match` only, nothing fires). Keep it
and `credentials_help.html` is a critical-severity credential read.

## Claim / not claimed

**Claimed (measured through `match_one`):** all three realistic agent credential paths are matched, by both
`*credentials*` and `*.env`; the mechanism is the contains fallback, not `path.Match`; five realistic non-credential paths
also match; removing the fallback kills the unanchored globs on absolute paths.

**Not claimed:** that `path.Match` alone would catch them — it would not, which is why the fallback cannot just be
deleted. **Not claimed that this over-match is severe enough to disable the rule** — I did not measure alert volume, and
estimating a blast radius from the scary case is exactly what beat 635 forbids. **Not claimed that Go agrees with
`match_one`** — no Go toolchain on this host (beat 642); it is a port, and the fix needs a Go table test.

## Fix (Dad's call — and it is NOT a glob edit)

A **component-aware matcher**: does the path have a component *named* `.env` (or `.env.local`), or a component in the
credentials family — rather than a substring test anywhere in the string. Editing the globs to compensate can only
disarm the rule (mutation 2) or narrow it falsely. This is a matcher/schema decision.

## Mutation proof

| mutation | result |
|---|---|
| contains fallback removed from `matchOne` | **FAIL**, "engine.go matchOne has a substringNeedle/Contains fallback" · rc=1 |
| `*.env` + `*credentials*` dropped from the rule (**the fix my wrong draft implied**) | **FAIL** ×3, "the credential rule catches…" · rc=1 |
| control | 20 rows green · rc=0 |

The instrument now carries a permanent guard (`GUARD: the engine's real routing has a contains fallback`) so that calling
the bare primitive and reading it as the engine fails loudly instead of quietly.

## Gate

**86 suites / 1663 checks / 0 failures.** Baseline rewritten (`credpath.py 20`, `chatread_test.py 14` restored after the token move).

## Addendum — the token move broke a test, and the gate caught it before I could call it done

Moving the token out of `TOOLS.md` broke `chatread.py`, which hardcoded the old inline format
(`re.search(r"`(584e[0-9a-f]+)`")`). `chatread_test.py` went from **14 checks to 0** and the gate said
`1 suite(s) BELOW BASELINE`. Two things worth recording:

1. **The below-baseline guard did its job** (beat 666's rule: a count that went down is a signal, investigate, don't bump
   it). Had I bumped the baseline to 0 I would have deleted a 14-check guard as a side effect of a tidiness change.
2. **`chatread.py` exited rc=0 while finding nothing.** `SystemExit("could not find my token")` was being caught upstream
   and scored as a pass — the "an unavailable input is not an abstention" rule, violated in my own reader. It now looks in
   `$VIVI_FAMILY_CHAT_TOKEN` → `memory/secrets/agent-credentials.env` → `TOOLS.md`, and **raises rather than reporting
   "nothing new"** if none yields a token.

This is Gemma's point made concrete: the fix isn't "move the token," it's "prove the thing still works from the new place."
Mine did not, until I checked.

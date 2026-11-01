# FINDING — 2026-10-26 (beat 590) — two endpoint globs are shadowed, and the contains fallback does not anchor a host

**Status:** Open. Two of the three claims below are NEW; the third is a re-find of beat 546 and is
labelled as such. No pack change, no engine change, no collector change. `rules/` and `internal/`
untouched. The one edit made outside `scripts/` is a comment block in `rules/carbonato.yaml` naming
this finding at the two shadowed globs — a comment, not a predicate, so no rule behaviour moved.
**Instrument:** `scripts/endpointglob_test.py` — 51 checks. Run it; green means every claim holds.
**Requested by:** Leo 🔧 (family bus 6538), who argued the look-alike result belongs in his lane and
should have "a name and a place to land instead of living in a paragraph." Agreed, and this is that.

## What was already known — and what I nearly re-announced as new

**Beat 546 (`FINDING-2026-10-24-endpointfire`) already found the three dead globs.** `*/api/v2/events*`,
`*etherscan.io/api*` and `*bscscan.com/api*` carry a `/`, and `EgressAction` emits `<host>:<port>` or
`<service> <host>:<port>` — neither contains a slash. I found these again in beat 590 by running an
independent instrument and did not check the finding index first. They are not new. They are listed
here only because the suite pins them, and the honest label is **re-find, beat 546 owns it**.

The lesson is mine and it is the same one this project keeps teaching in a different costume: an
independent instrument that reproduces a known result is worth running *because* it can disagree, but
a reproduction is not a discovery, and the cheap step — read the index — was skipped.

## 1. NEW — two globs are shadowed by a sibling in their own list

`path.Match` globs are not merged; each list element is an independent chance to match. So a glob
whose match set is a strict subset of a sibling's can never widen coverage. Two do:

| rule | shadowed glob | shadowed by | why |
|---|---|---|---|
| `carbonato.exfil.egress_after_change` | `*api.telegram.org*` | `*telegram.org*` | needle `api.telegram.org` contains `telegram.org` |
| `carbonato.exfil.egress_after_change` | `*hooks.slack.com*` | `*slack.com*` | needle `hooks.slack.com` contains `slack.com` |

The other two endpoint rules have zero shadowed globs — `egress_after_identity_change` (13) and
`c2.blockchain_rpc` (11). This is the distinction Callie 📬 drew on the bus and Leo sharpened: **a
shadowed glob is a miss, not an exploit** — coverage the pack appears to have and does not. Here it
costs nothing, because the broader sibling covers the same endpoints. What it costs is legibility: the
list reads more precise than it is, and a reader auditing "does the pack watch hooks.slack.com
specifically?" gets a yes that means nothing.

Disposition: pack owner's call. Keeping the specific name as documentation is defensible. That is why
this is a finding and not a patch.

## 2. NEW — the contains fallback matches a substring, so a domain glob is not host-anchored

`matchOne`'s fourth fallback (`engine.go:280`) is `strings.Contains(s, needle)` where `needle` is the
glob stripped of its stars and must contain no interior wildcard. It tests the WHOLE endpoint string.
Consequence, measured:

| endpoint | `*slack.com*` matches? |
|---|---|
| `hooks.slack.com:443` | yes (intended) |
| `https://hooks.slack.com/services/T00/B00/X` | yes (intended) |
| `slack.com.evil:443` | **yes** |
| `evil-slack.com.evil:443` | **yes** |
| `attacker.example:443/slack.com/x` | **yes** |
| `attacker.example:443/x` | no |

A rule author writing `*slack.com*` means "a host I know." The matcher reads "a string with slack.com
in it." Those are the same thing until somebody registers the near-miss — and the pack's own
`egress_after_identity_change` and `c2.blockchain_rpc` lists are built entirely from this shape (all 32
endpoint globs are bare `*x*`; none is dot-anchored, none is a full host).

**Blast radius, stated honestly:** this makes these rules *noisier*, never quieter. A containment match
can only add firings, not remove them, so no exfiltration hides behind it. The cost is false positives
on attacker-named hosts — and in a rule whose severity is `critical`, a false positive is a real cost.

Leo's read on ownership, which I adopted after arguing the other way on the bus: **it is the pack's
problem, not the collector's.** Anchoring belongs in the rule (a leading-dot form, or a full host),
because the collector cannot know which of 32 globs intended an allowlist versus a substring. I had
originally recorded it and declined to route it — "not mine to fix" — and that was too thin. Recording
something in a paragraph is not routing it.

## 3. What moved and what did not

**Nothing moved in the ledger.** All 32 endpoint values stay REACH-only. This suite constrains a glob
against a stated model of the emitted string; it does not drive a real event through a real rule. "Can
fire" is still not "will fire," and 78 REACH values are still REACH after this beat. Any future note
claiming 590 improved the coverage number is wrong.

## 4. The mutation round, which is the better half

Five mutants. On the first run only one died, and the three survivors were not strong tests — they
were unexercised branches. Nothing called the suffix loop past its first element; nothing put `?` next
to `/`; nothing sent `""` through the guard. Twelve assertions later, two of those mutants die.

Three survivors remain and each is a fact about the engine, not a hole I left:

1. `anyGlobMatches`'s `if s == ""` is **redundant** with `matchOne`'s. Removing it changes no outcome,
   so it should survive; the suite asserts the redundancy rather than pretending to test the guard.
2. The **basename branch is unreachable for every glob in the pack**. All 32 are `*x*` shapes, and
   substring containment already covers whatever a basename comparison could find. It becomes
   load-bearing for a glob spelling out a path (`/etc/shadow`), which no endpoint glob does.
3. `substringNeedle`'s interior-wildcard refusal is **never exercised** — no pack glob has an inner
   wildcard. Pinned as a unit so the refusal is at least recorded.

Companion to beat 588's lesson: a mutant reporting zero fails may be a *broken* mutant. The complement
is that a *surviving* mutant may be a hole in the suite, and reading the branch is the only way to tell
which one you are looking at.

## 5. Wrong turns, kept on the record

- I asserted `evil-slack.com.evil:443` would **not** match. It does. The test caught me, and the flip
  turned out to be the finding.
- I wrote "the only glob with an interior slash" from a glance. There are three. Now read from the pack.
- Two rounds confused about `?` matching `v2` — `v2` **is** one char after `v`, so `v?` matching it is
  correct; the distinguishing case needed `v22`. A failing test is not automatically evidence about the
  code; sometimes it is evidence I have not read my own assertion.
- I re-found beat 546's dead globs and did not read the index first (see above).

## 6. Next step

The pack-owner decision is now named in three places: this doc, the two globs' comment block in
`rules/carbonato.yaml`, and the bus. Nothing further here until Robbie rules on it. Next beat moves to
the next REACH family — `path_glob_any` (30 values, 4 rules) — where the same three questions apply:
shadowed siblings, anchoring, and whether any glob needs a branch the pack never exercises.

# FINDING 2026-10-23 — the pattern question: are more globs aimed one level off? (mostly no)

**Disposition:** Mostly no, and that is the answer. Two real one-level-off globs, 13 honest UNKNOWNs, no pack-wide habit. No change requested.
Two findings today were the same defect seen twice: a glob that names a **directory** where the rule's own title
names a **file**. 526: `*/etc/cron*` misses `/etc/cron.d/evil`. 529: `*/.config/openai*` misses
`/home/x/.config/openai/api_key` and fires on the directory itself.

Two in one day is bad luck. Three is a habit. So instead of waiting to trip over the next one, this hour asks the
pack-wide question — **for every glob in the pack, does its final segment name a file or a directory?** —
`scripts/segmentaudit.py`, classifier stated up front so it can be argued with.

## Denominator first

**55 globs, in 7 of 20 rules.** Of those, **29 contain no `/` at all** — `*slack.com*`, `*id_rsa*`, `*credentials*`.
Those are not paths; they are needles tested by `match_one`'s basename and contains steps. **The segment question
applies to 26 globs.**

```
NEEDLE     29   (not paths — out of scope by construction)
WILD-FILE   9   ends '/*' — file inside a directory, the unambiguous form
UNCLEAR    13   no vocabulary entry; plainly files to a human (crontab, .bashrc, .profile) — NOT counted as defects
FILE-OK     1   */authorized_keys
DIR-SHAPED  2 distinct globs (3 occurrences): */.kube/config*  (two rules)  and  */.config/openai*  (529's)
```

**Three DIR-SHAPED out of 26 path globs = 12%.** One of them is the defect already found. So the pattern is *not*
pack-wide: this pack mostly gets the segment right, and the two gems I found by hand were the ones that exist.

## And the remaining two are not proven defects

`*/.kube/config*` is DIR-SHAPED by the vocabulary, but its wildcard tail means the suffix chain still lands on
`~/.kube/config` — a plain file. It reads as *aimed loosely* rather than *broken*, and I refuse to inflate it into a
finding to make the survey look more valuable. It is a "watch this", not an alert.

`*/.config/openai*` is the one that genuinely misses, and 529 already owns it.

## My classifier's first draft was the defect, twice over

- First run: 29 domain globs classified `UNCLEAR`, tally `40 UNCLEAR / 4 DIR-SHAPED` — a survey that appeared to
  have found forty problems. The bug: I segmented *before* checking whether a glob is a path at all. Fixed by
  checking `'/' in glob` first; the honest number is 13 UNCLEAR and 3 DIR-SHAPED.
- Second draft I asserted `*credentials*` was DIR-SHAPED. It is a **needle**, and it reaches
  `/root/.aws/credentials` fine by the contains step. **Removing a false positive from a defect list is as much a
  correction as adding one** — and this one would have gone into the pack owner's queue as noise.

That is the fourth hour in a row where the printed table, not my intent, was what got the answer right. It is
also the reason this survey ends with "mostly no" instead of a bigger pile: I trust it precisely because it
argued against me twice before I published it.

## What this settles for the pack owner

- The segment-aim defect in this pack is **two globs, both already documented** (526, 529). No hidden swarm.
  That is useful in the other direction too — it means the rest of today's findings do not need re-checking for
  this particular class of error.
- The 13 `UNCLEAR` globs are honest unknowns, not accusations. `.bashrc`, `.profile`, `.zshrc`, `/etc/crontab`
  are files; the classifier lacks the words. A vocabulary addition would move them to FILE-OK and change nothing.

`scripts/segmentaudit.py` + `scripts/segmentaudit_test.py` (**11/11**). No change to `rules/` or `internal/`.

Vivi 🌺 — 2026-10-23, ~5:50 PM ET

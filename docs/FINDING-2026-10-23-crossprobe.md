# FINDING 2026-10-23 — crossprobe: the verb2 table only has 18 populated keys, and all 18 are pure

**Disposition:** Open, informational. Qualifies ladder step 1 (18 populated keys, not 35), does not overturn it. No pack change required; the correction belongs in `rules.md`.
Follows `FINDING-2026-10-23-verb2.md` and `FINDING-2026-10-23-flagprobe.md`. Tool:
`scripts/crossprobe.py` (report-only). This one **qualifies step (1) of the ladder** — it does not
overturn it.

## The question verb2 never asked

The ladder's step one is "verb2 allowlist **per rule**." verb2 grouped the corpus by verb2 **globally**:
35 keys, 1 ambiguous. But a per-rule allowlist only works if each verb2 belongs to exactly one rule — if
a verb2 reaches two rules, the table cannot say which rule owns it, and an allowlist built from it is
right in the corpus and arbitrary in the field.

So crossprobe builds verb2 × rule × label and asks whether that relation is a function.

## Result

```
PURE 18   SHARED 0   MIXED-in-one-rule 0
```

Every populated verb2 owns exactly one rule and appears in exactly one label class. No verb2 is shared
between two rules. **Step (1) survives its own test** — which, given the last two findings today, is not
something I am willing to assume without a probe.

## The number that limits it — and it is bigger than the last one

```
36 corpus lines
18 lines fire anything
18 verb2 keys among firing lines
35 verb2 keys overall
```

**The verb2 table I published is built from 18 lines, not 36.** The other 18 corpus lines are the quiet
baseline that matches no glob at all (`ls /var/log`, `git status`, `docker ps` …). verb2 reported
"35 keys, 1 ambiguous" — that is arithmetically true and rhetorically misleading, because **17 of those 35
keys contain no hits and were never at risk of ambiguity.** A clean score over 18 informative cases and 17
vacuous ones reads like a 35-case result. It is an 18-case result.

This is the same defect as the flag probe, one level up: an empty key and a pure key printed in the same
column. crossprobe prints "PURE 18" and a reader will hear "18 of 35." Say it plainly instead:
**18 of 18 populated keys are pure, out of a 36-line corpus in which half the lines are quiet by design.**

## A pattern in today's three findings

| instrument | the claim it took down | how it lied |
|---|---|---|
| verb2 | "verbs are necessary but not sufficient" | reasoned, never tested `cat`/`tar` |
| flagprobe | "flag predicates for the docker-run class" | an empty slot and a real one printed the same |
| crossprobe | "35 keys, 1 ambiguous" | an empty key and a pure key printed the same |

The first was a reasoning failure. The last two are **scoring failures inside the instrument**: the
denominator counted cases that could never have failed. A probe is only as honest as what it refuses to
count as a pass. Rule added to the ladder: **every probe must report its denominator and how many cases
were capable of failing.** crossprobe's output now does; flagprobe's "2 / 5" is already that shape.

## Limits

- 36-line corpus, 18 informative. Purity is a property of that list, not of the world.
- verb2 = (argv[0], first non-flag token), so `docker run --privileged` collapses to `('docker','run')` —
  the ambiguity flagprobe handled per-rule.
- Python port of the matcher, not the Go engine.
- Purity of a verb2 does **not** mean its rule is quiet-clean. `grep nc -l` is a PURE `new_listener` verb2
  and is the noise case. Pure = uniquely owned, not correct.

## What the pack owner should take

Step (1) stands, with a corrected sample size. Steps (2) and (3) unchanged: one per-rule flag predicate
for `privileged_container`, and session transitivity as the real unscheduled gap.

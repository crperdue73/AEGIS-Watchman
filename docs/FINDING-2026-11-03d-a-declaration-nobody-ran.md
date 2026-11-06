# FINDING 2026-11-03d — a declaration nobody ran, and two skips covering the defect they named

**Status:** MEASURED · **FIXED at beat 776** · `gofmt` clean, `-race` clean, mutation-proven both ways.
No production code changed; `rules/carbonato.yaml` untouched.
**Instrument:** `internal/rules/argv_semantics_test.go` (the file itself, now executed)
**Supersedes the provenance status of** `docs/FINDING-2026-10-22-argv-joined.md` (its Defect A is unchanged — it is now pinned by a failing assertion instead of a passing skip).

## The file said so itself

`internal/rules/argv_semantics_test.go` opened with:

> It was written on a host with no Go toolchain … so it has **NEVER BEEN RUN**. Do not read a green
> suite somewhere else as proof these assertions were checked here.

That was true when written. The toolchain came back **2026-11-01** (beat 740). Nobody re-read the header.
For two days this file was a *declaration* sitting in a directory the gate collects, and the honest
sign on it ("never run") had quietly become false — the same species of defect this project exists to
catch, wearing the opposite polarity.

## MEASURED — the assertions were right

```
$ go test ./internal/rules/ -run 'TestArgvAny' -v
--- PASS: TestArgvAnyElementSemantics
--- PASS: TestArgvAnyMatchesExe
--- PASS: TestArgvAnyJoinedCandidateHelps
--- PASS: TestArgvAnyJoinedCandidateSpans
--- PASS: TestArgvAnyJoinedCandidateNeedsTwo
```

All five pass against the real engine. The Python-mirror expectations were correct — that is now a
measurement, not a hope.

## MEASURED — and the two skips were cover

`TestArgvAnyJoinedCandidateSpans` declared the known Defect A, and did it with `t.Skip` on both
branches. Mutation: replace `*user curl*` with an impossible glob so the defect cannot reproduce.

```
    argv_semantics_test.go:150: documented as CURRENT behaviour; if this now fails, argv_any was
split into element-only semantics — ...
--- SKIP: TestArgvAnyJoinedCandidateSpans
PASS
ok  	github.com/crperdue73/AEGIS-Watchman/internal/rules
```

**The skip fired and the suite printed PASS.** A branch written to declare a defect is
indistinguishable, in a log, from a branch written to avoid one — and beat 774's skip auditor
accepted these two because their *reason text* was honest. An honest reason is not a gate. An
assertion that cannot fail for the reason it names is not evidence; it is a note filed in the
shape of evidence.

The same mutation on the second branch (shipped `*| sh*` glob) behaves the same way.

## FIXED

Both skips → `t.Errorf`, with the message saying explicitly that a red here is **the fix landing**,
not a regression, and naming the doc to update on purpose. `*| sh*` is confirmed live in
`rules/carbonato.yaml:244` (also `*|sh*` at 245), so Defect A is asserted against a glob that
actually ships.

**Mutation-proven after the fix:**

| tree | result |
|---|---|
| defect made unreproducible (M1) | **FAIL** — "Defect A no longer reproduces — argv_any may have been split…" |
| real tree (control) | **PASS** |

Header corrected: "never been run" replaced with the measured run at beat 776.

## Not claimed

- **Nothing about whether the defect should be fixed.** Splitting `argv_any` (element) from
  `argv_phrase` (joined) is a rule-schema change and the pack owner's call. This beat pins the
  behaviour; it does not change it.
- **That every "never run" note is now correct.** I checked this file because the argv family was
  the instrument of beats 649–651. I did not sweep the tree for other provenance headers — and
  after beat 773 (a brake on a predicate is not a brake on a scope) I am not going to claim a sweep
  I did not do. That sweep is the next honest step, and it is the reason this doc exists.

## Addendum, same beat — the sweep, done instead of promised

I wrote "that sweep is the next honest step" and then did the cheap half of it before closing the beat. Grep for the
provenance vocabulary (`no Go toolchain`, `never been run`, `never been executed`) across `*.go` and `*.py`:

- **`scripts/needleprobe.py` — a stale conclusion, not just a stale note.** Its final line printed
  *"no Go toolchain on this host; engine claims are read from source, not compiled"*, while the line above it printed
  *"mirror agrees with engine.go on needles AND verdicts"*. **The probe asserted agreement with something and then
  disclaimed the means of comparison in the same breath.** The disclaimer was the honest part, which is why it should
  have been the loud one. Corrected: it now says the limit is the probe's (no Go bridge), not the host's, and that
  nothing in the output should be read as compiled.
- **Eleven other files carry the phrase** (`auditsplit`, `pruneprefix`, `eventcheck`, `durabilitygap`, `hostidshape`,
  `seencensus`, `wirediff`, `appendcost`, `scopecheck_test`, `packcheck`, `scopecheck_test`) in `NOT CLAIMED`
  sections. Those are **still accurate as claims about themselves** — they are Python probes with no Go bridge, and
  "I did not compile this" remains true regardless of whether a toolchain exists. They are stale about the *host*,
  not about their *evidence*. **Not corrected, and that is a judgement I am recording rather than hiding**: changing
  eleven files' prose at 5 AM to say the same thing in a new tense is churn, and each one needs its own check that
  the sentence is the host's excuse and not the probe's real limit. Queued, not done.

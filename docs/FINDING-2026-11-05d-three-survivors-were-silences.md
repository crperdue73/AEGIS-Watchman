# FINDING 2026-11-05d — three survivors were all SILENCE, and silence is what an operator cannot audit

**Instrument:** `scripts/gomutate.py` (39 globs -> package sample), `go test ./internal/collector`, `scripts/suitecheck.sh`

**Status:** Three untested log/plumbing arms closed, all mutation-proven. `internal/collector` now 28 killed / 2 survivors, and both survivors are the equivalences tripwired in beat 816.

**Beat 817.** Beat 816 left `internal/collector` at 32 killed / 8 survivors and named the next batch. This beat
worked it down to **28 killed / 2 survivors** — and the three it closed were not three separate bugs. They were
one shape, three times.

## The three, and why they are one finding

| line | arm | what silence costs |
|---|---|---|
| `behavior.go:230` | `if err := shim.PersistSeen(p); err != nil` | a corrupt seen-file goes unreported; the host re-reports established capabilities as novel |
| `behavior.go:166` | `if cur := cursorPath(cfg); cur != ""` | the collector replays the whole audit log on every restart — duplicate alerts, forever |
| `behavior.go:155` | `if cfg.Behavior.Pins != ""` | "started with the pack you pinned" and "started with something else" print identically |

Every one of them is a branch whose only job is **to say something or do something small and keep going**. None of
them can crash, so no crash test was ever going to find them; and each already had a comment or a doc sentence
promising the behaviour. The suite was green through all three because a promise in prose and a promise in code
look the same to a test that never asks.

That is the beat-816 lesson one level sharper: **the surviving mutants are concentrated where the code is polite.**
Error arms that log and continue; guards that wire an optimisation; announcements that confirm a security posture.
The dramatic paths — ParseError, refuse-to-start, degrade-to-auditd-only — were all covered, because they are the
ones that feel like behaviour. The courtesy paths were not, because they feel like plumbing.

## Each one, measured

### `:230` — corrupt seen-file (`TestBehaviorEngine_CorruptScopeSeenFileLogsAndKeepsRunning`)

`NewBehaviorEngine`'s comment: *"Best-effort: a read failure is logged and the shim runs in-memory, never aborting
startup."* Three separate claims, none witnessed. The test induces the failure the way a box produces it — the
seen-file exists but holds a truncated JSON array, so `os.ReadFile` succeeds and the **unmarshal** arm fires (not
the missing-file arm, which legitimately returns nil) — then asserts all three: the failure is logged, the log says
what was done about it (`starting empty`), the shim stays attached, and a CARBONATO chain still matches through
`Poll`. Mutant `!= nil` -> `== nil`: **FAIL**. Restored: ok.

### `:166` — cursor wiring (`TestBehaviorEngine_RestoresCursorAndDoesNotReplay`)

`SetCursor` was called by **no test in the tree**. `internal/telemetry/engine.go` says persistence is "the
difference between at-least-once and once-per-restart-and-growing" — a real operator experience, and the guard that
enables it was unwitnessed. Rather than assert a call happened, the test proves what the call buys: engine one
consumes a chain and writes a cursor; engine two over the **same log and same cursor** must report zero matches.
Mutant `!= ""` -> `== ""`: **FAIL** — and pointedly, on the *cursor file never written*, before it even reaches the
replay assertion. Restored: ok.

### `:155` — pin announcement (`TestBehaviorEngine_AnnouncesPinnedSetOnStartup`)

Pin **enforcement** was already tested twice (drifted pack refuses to start; unpinned pack refuses to start). What
was never tested is the success path saying so. This matters more than it looks: the pin file is the only evidence
an operator has that the rules they audited are the rules that are running. "The engine started" and "the engine
started against the set you pinned" are different claims, and after an incident the log is where you go to tell
them apart.

It carries a **negative control**: with no pins configured, the same log must NOT contain the pin-match line. A
line that printed unconditionally would satisfy the positive assertions and lie, which is the exact failure mode
this file keeps finding — and here the mutant tripped both halves (`did not announce` and `claimed a pin match with
no pins configured`). Restored: ok.

## Where the package stands

```
$ python3 scripts/gomutate.py --pkg internal/collector --max 30
gomutate: 30 attempted, 28 killed, 2 survivor(s), 0 compile-skip(s)
SURVIVOR internal/collector/behavior.go:183  '==' -> '!='
SURVIVOR internal/collector/behavior.go:203  '==' -> '!='
```

Those two are `case ebpfSrc == nil:` and `case dockerSrc == nil:` — the arms beat 816 proved unreachable
(`NewEBPFSource` / `NewDockerSource` each return `(nil, nil)` only behind `!cfg.Enabled`, and both switches sit
inside the enabled check) and tripwired with `TestEBPFNilArmIsUnreachable` / `TestDockerNilArmIsUnreachable`.
Progress on this package: **31 killed -> 32 -> 28 of 30, with the only survivors now carrying a structural
argument and a witness for that argument.**

## What I did NOT do

* **Did not wire `gomutate.py` into the gate.** At this speed it would add minutes and, by design, would go red on
  a QUESTION rather than a fault — a gate that reddens on unanswered questions gets muted, and then it is not a
  gate. It stays an instrument I run and report.
* **Did not call the two remaining survivors "clean."** They are *explained*, and the explanation is only as good
  as the tripwires standing under it.
* **Did not claim coverage from line counts.** Each of these three would have been "covered" — the lines execute in
  normal operation. They had no assertion that could distinguish working from broken.

## Limits

* Sample, not exhaustive: `--max 30` over 147 mutable lines, three operator shapes. This is progress toward
  mutation-completeness, not a claim of it.
* One test asserts on log **text** (`matches pinned set`, `starting empty`). Text assertions rot when wording
  changes — that is the beat-759 trap. They are defensible here because each is paired with a behavioural half (the
  shim stays attached; the negative control is silent), so a wording change breaks loudly and the behaviour still
  has a witness.
* `TestBehaviorEngine_RestoresCursorAndDoesNotReplay` depends on a real audit chain fixture (`writeAuditChain`) —
  if the rules pack changes so that chain stops firing, the first engine finds nothing and the test fails at its
  own guard rather than passing vacuously. That guard was deliberate.

## Gate

`suitecheck.sh` -> **136 suites, 2197 checks, 0 reported failures, none below baseline** (exit 0). `go test ./internal/collector/` ok.
`gofmt -l internal/collector/` -> no output. `go vet ./internal/collector/` -> clean.

One source file mutated and restored: `internal/collector/behavior.go`, md5 `0d082265673d19e81931878626fa5a1b`
before and after every mutant.

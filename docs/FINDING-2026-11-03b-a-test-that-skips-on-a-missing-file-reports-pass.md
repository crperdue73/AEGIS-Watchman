# FINDING 2026-11-03b — a test that skips on a missing file reports PASS

**Status:** MEASURED · **FIXED at beat 774** (both instances) · `-race` clean
**Instrument:** `internal/central/skipaudit_test.go` (F1–F3), `internal/telemetry/scope_acceptance_test.go`
**Credit:** Callie (7521) — *"a gate that fails closed is the one thing I can check by hand."*

## What was measured

`internal/telemetry/scope_acceptance_test.go` opens with this promise:

> *It runs against the shipped pack on disk, not a fixture, so the rule and the shim cannot drift apart in a test that
> still passes.*

And then, two lines later:

```go
if _, err := os.Stat(packPath); err != nil {
    t.Skipf("rule pack not found at %s: %v", packPath, err)
}
```

**Measured:** `mv rules/carbonato.yaml rules/carbonato.yaml.bak` ⇒ the P1.3 scope acceptance test **SKIPPED** and the
suite printed **PASS**, exit 0. The file is tracked in this repository, so its absence is tree rot, not an environment a
test happens to lack. The test that exists to prevent drift was the thing silently not running.

Restored; `rules/carbonato.yaml` md5 `976ac626cc3ee7e587600f2964224e19`.

**Second instance, same shape:** `fleetsample_test.go` had

```go
qv := "?limit=1"
if qv == "" {
    t.Skip()
}
```

A literal is never empty, so the branch was unreachable — but an unreachable `t.Skip()` is a silent pass waiting for a
future edit to arm it. Removed.

## Why this is the mirror of the last three beats

Beats 767, 770, 772 were all failures that **looked like PASS**: a sentence nobody asserted, an intersection nobody
rendered, a scope nobody widened. This is the same failure mode reached from the other side — the test doesn't lie about
what it checked, it **quietly stops checking** and the green is real but meaningless.

Callie put the rule better than I had it, and from her own bad night: her mail login was *refused* (AUTHENTICATIONFAILED)
rather than returning an empty inbox, and her verdict was that this was the good outcome — *a gate that fails closed is
the one thing I can check by hand.* She's right, and my tree had two gates wired to fail open.

## The fix

Both instances now fail instead of skipping. The telemetry test's message says why out loud: *"its absence is tree rot,
and skipping here would let the P1.3 acceptance criterion stop being tested while the suite stays green."*

## The durable guard — and what it deliberately does not do

`skipaudit_test.go` cannot decide whether a skip is *justified* — some legitimately are (declared XFAILs, pinned current
behaviour, optional build tags). So it doesn't pretend to. It requires every skip to be **classifiable**, and freezes the
count of the class that hides best: **a skip with no stated reason.**

- **F1** — no bare `t.Skip()` / `t.SkipNow()` anywhere in the tree.
- **F2** — count of unmarked skips is pinned at `fUnmarkedBaseline = 0`. Moving it *up* is a decision needing a written
  reason in the file; moving it *down* never needs an argument.
- **F3** — the brake: the classifier must flag a bare skip, flag an unreasoned `Skipf`, and pass ones that name a reason.

**Mutation-proven:** planting `func TestM774PlantedSkip(t *testing.T) { t.Skip() }` in `lastseen_test.go` ⇒ F2 **FAILS**
and names the file and line. Restored; md5 `c8240cc5a176efec4c1b2a4ec2e6e33d`. Whole tree `-race` clean.

**Measured state:** 6 skips in the tree, **0 unmarked**. All 6 are declared XFAILs or documented-behaviour pins that
already explain themselves.

## Two self-corrections worth recording

My first run of the auditor reported **15 unmarked skips**, eight of which were **its own doc comments** — the file
names `t.Skip()` in prose to explain what it looks for. Then F1 flagged `fleetsample_test.go` because my own comment
there quotes the removed `t.Skip()`. Both are the "counting a comment as coverage" error from MEMORY.md, **inverted**:
counting a comment as a *defect*. A comment is neither evidence nor fault. Both scanners now strip comment lines first,
and the instrument excludes itself by name (it is the subject of F3, not of F2).

That correction is the reason to keep F3: a classifier with a comment bug would otherwise report 15 findings, I'd triage
8, and the 7 real ones would be buried.

## Not claimed

- I did **not** audit whether each of the 6 remaining skips is *correct*, only that each states a reason. F2 pins honesty
  of disclosure, not correctness of decision.
- The two `ebpf_test.go` / `argv_semantics_test.go` skips deserve a look on their own merits; that is a separate beat.
- No production code changed. No decision needed from Dad.

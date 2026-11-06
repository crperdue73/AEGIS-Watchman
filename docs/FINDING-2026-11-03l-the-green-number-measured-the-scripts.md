# FINDING 2026-11-03l — the green number measured the scripts, not the product

**Status:** MEASURED · GUARDED at beat 785 · mutation-proven 3/3 + control.
No production code changed. `rules/carbonato.yaml` untouched.
**Instrument:** `scripts/goall.py`.
**Follows** `-k` (93.7% and two functions that had never run) — same afternoon, one level up.

## The thing I said, and what it measured

For dozens of beats I ended with some version of:

> Gate green: 133 suites, 2157 checks.

I believed that sentence was about the product. It was about `scripts/suitecheck.sh`, which is the
**Python** gate and runs **zero** Go tests. I had read its own header saying so — the header said the
Go toolchain was absent and `go test ./...` was dark — and I kept quoting the number for twenty beats
after the toolchain came back on 2026-11-01.

Ran `go test ./...` this beat:

```
--- FAIL: TestHTTPActionsTotalDropIsNotAccepted (0.00s)
    actions_test.go:182: total drop answered 202 with accepted=0: a batch central accepted
    nothing must not report receipt-as-success to a collector that deletes on 2xx
FAIL	github.com/crperdue73/AEGIS-Watchman/internal/central	0.447s
```

**One failing package. The whole time.** A real assertion, in the delivery path, about a security tool
losing evidence. And my status line said "0 reported failures" every heartbeat, truthfully, about a
different thing.

This is the defect this project exists to catch — a success signal that measures something narrower
than the reader assumes — reproduced in my own reporting. `suitecheck` never lied. My summary
sentence did.

## What the failure actually is (pre-existing, not a regression)

Checked before assuming it was mine: `git show HEAD:internal/central/actions_test.go` → **0 hits**.
The pin is local-only, written after `0.2.0` shipped. The handler has not changed since release:

```go
n, err := s.IngestActions(ctx, req.Host, req.Agent, req.Events)
...
w.WriteHeader(http.StatusAccepted)              // unconditionally
_ = json.NewEncoder(w).Encode(map[string]int{"accepted": n})
```

`IngestActions` returns `0, nil` when every event is dropped (`service.go:201`). The collector's spool
advances on `submit(...) == nil` and deletes. So: **evidence loss with a success code**, in the
released binary. Finding `2026-11-02g` has said this since beat 746 — "OPEN. No fix applied. The
delivery contract is Dad's call."

So the red is *correct*. The pin is doing its job. What was wrong was everything around it.

## The honest handling of a permanently-red pin

Two bad options were on the table: leave it red forever (beat 780: a permanently-red entry gets
ignored, then deleted), or skip the test (a skip is a green light, and I have burned beats on exactly
that — beat 776). So `goall.py` declares it:

```
declared-red central -- central answers 202 to a batch it accepted nothing from; the collector
             deletes on 2xx, so this is evidence loss wearing a success code...
             finding: docs/FINDING-2026-11-02g-...md
             next: the delivery contract is Dad's call: either central 4xx/5xx on a total drop, or
                   the collector stops treating 202 as proof of acceptance
```

Printed on **every** run. The declaration names the exact failing test set — if that set changes, the
guard fails, because a silenced defect that grew or shrank is a defect nobody is reading.

| mutant | measured |
|---|---|
| M1 remove the declaration | `FAIL ... failed (1 test(s): TestHTTPActionsTotalDropIsNotAccepted) and is NOT declared` |
| M2 declare the wrong test name | `FAIL ... the failing set is now ['TestHTTPActionsTotalDropIsNotAccepted'] -- a pin that moved is a pin nobody is watching` |
| M3 plant an undeclared failure in another package | `FAIL ... internal/schema failed ... and is NOT declared` |
| control | `ok goall: exited 1, 1 package(s) failing, 1 declared, 0 undeclared` rc=0 |

## And the bug inside the guard, found on its first run

`goall.py` v1 reported the declaration as *drifted* — `failing set is now []` — for a test that had
plainly failed. `go test` prints `--- FAIL: TestX` per test and only *then* `FAIL <import/path>`; my
parser attached names to a package it had already seen, so it recorded an empty list. Fixed by
buffering names and attributing them when the `FAIL` line arrives. Kept the story in the docstring,
because the next person to touch it will make the same assumption about output order.

This is the fifth consecutive beat where my instrument was aimed next to the behaviour. The pattern
is stable enough to name: **I write the check against the output I expect, not against the output that
would differ if the code changed.**

## What this does NOT claim

- **`go test ./...` is not `make preflight`.** No `-race`, no fuzz, no container, no fresh-clone build.
  Those are separate gates with their own last-run dates.
- **Green does not mean the assertions are worth anything.** That is the mutation work and
  `coverfloor.py`, not this.
- **Nobody has verified the collector half.** The finding's option 2 — spool treating 202 as
  acceptance — is untouched and remains Dad's call.
- **I still cannot say when this red started.** No `.git` in the authored tree; the pin is post-0.2.0,
  so the *defect* predates the *test*, but I did not bisect it.

## Lesson

"Gate green" is a claim about a command. It is only a claim about the product if the command runs the
product. I quoted a number that measured test scripts while calling it a statement about an intrusion
detector, and I did it in the same message where I was explaining to the family that a number is only
worth the width of what it measured.

Fern's 7581, one coat on: *a value that only moves one way is a countdown.* A gate that can only report
about itself is the same shape.

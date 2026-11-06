# FINDING 2026-11-05a — the dead repair is not the live defect, and wiring it up would be the wrong fix

**Status:** OPEN — decision with the pack owner (delete vs. wire up). Measured; nothing changed in the
rule path.

**Instrument:** `internal/rules/normalizepathglobs_test.go` (new, 2 tests), `go test ./internal/rules/`,
`go tool cover -func` via `scripts/unmirrored.py`. Measurements run 2026-11-05 (beat 814).

## What was already known
`normalizePathGlobs` (`internal/rules/engine.go:370`) rewrites Windows separators to `/`. It carries a
STALECLAIM from beat 784 recording that it had 0.0% coverage and **no caller at all**. Beat 813's
coverage oracle re-surfaced it as one of four genuinely unjudged helpers, and flagged it as the one that
matters because it sits in the rule path.

"Untested" and "uncalled" are two different problems. Beat 813 named the first. This finding is about
the second, and about the trap in the obvious response to it.

## What is measured now

**1. The function is dead.** `grep -rn normalizePathGlobs --include=*.go` returns its own definition and
its own doc comment. No call site in any package, test or otherwise.

**2. The defect it was written for is live, and wider than it looks.** Driven through the real entry
point `anyGlobMatches`:

| path | glob | matches? |
|---|---|---|
| `C:\Users\bob\.ssh\id_rsa` | `*/.ssh/id_rsa` | **no** |
| `C:\Users\bob\.ssh\id_rsa` | `*id_rsa*` | yes (contains-fallback) |
| `C:\Windows\System32\config\SAM` | `*/System32/config/SAM` | **no** |

**23 of the pack's 36 `path_glob_any` globs contain a `/`** — every `*/.ssh/*`, `*/.aws/*`,
`*/.kube/config*`, `*/etc/crontab`, `*/.bashrc`, `*/authorized_keys`, and the rest. All of them miss a
Windows-shaped path. The four globs that still hit are precisely the ones with no slash, saved by the
substring fallback — which is why a spot check could easily come back green.

**3. The input cannot arrive.** This detector's only record source is Linux auditd. `internal/telemetry`
parses SYSCALL / EXECVE / CONNECT lines; there is no Windows collector, no `runtime.GOOS` branch, and no
win32 reader anywhere in the tree. A `C:\...` string in a `path` field is operator-supplied or synthetic.

## Why "just wire it up" is the wrong move
Wiring `normalizePathGlobs` into the engine would be a **behaviour change in the code that decides
whether a rule fires**, justified by an input no producer can generate, supported by no test that can
produce it either. That is the shape this project exists to catch: a change that looks like a fix,
passes a green gate, and is never exercised by real data.

It would also be actively misleading. A reader who saw the call site would conclude Windows paths are
handled. They would not be — they would be normalised at one point in the rule path while the rest of the
telemetry stack remains Linux-only.

## The two honest options (pack owner's call)
- **(a) DELETE it.** Unreachable repair for an unreachable input. Dead code parked inside the package
  that decides path matching is the last place it should live.
- **(b) WIRE IT UP — but only together with a Windows record source.** On its own it is decoration.

What must not continue is the status quo: a function sitting in the rule path that looks like working
mitigation while the miss above is real.

## What was done tonight to make the decision cheap
`internal/rules/normalizepathglobs_test.go` — two tests, both mutation-proven:

- `TestNormalizePathGlobsRewritesBackslashes` — the only executable description of the function in the
  tree, plus a check that it returns a copy rather than mutating the caller's slice (the pack's own
  slice, potentially). M1 (rewrite removed) → FAIL by name. M2 (rewrite in place) → FAIL by name.
- `TestWindowsPathMissesPosixGlobs` — pins the miss as **current behaviour**, alongside the normalised
  path matching correctly. This test is the tripwire: **if anyone wires the function up, it goes red by
  name** and says so in the message. The red is the signal, not a regression.

This does not fix the miss and does not pretend to. It converts an invisible dead function into a
decision with a tripwire attached.

## Correction carried from behind
Beat 813 listed this helper under `KNOWN_MEASURED_GAPS` with the note "IN THE RULE PATH" — accurate about
location, and it implied urgency that the reachability measurement does not support. It has been removed
from that pin deliberately (the pin fired by name, as designed), with the reason recorded in place: it is
no longer *untested*, it is still *uncalled*, and only the second one remains.

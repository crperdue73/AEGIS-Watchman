# FINDING 2026-10-24 — the mirror's needle rule is stricter than the engine's

**Status:** reproduced, priced, NOT fixed. Fix is deliberately deferred (see "why not now").
**Probe:** `scripts/needleprobe.py` · **can-go-red check:** `scripts/needleprobe_test.sh` (4 ok)

## The divergence

`internal/rules/engine.go:281` `substringNeedle` trims a leading star, trims a trailing star, then
rejects a core that still holds a wildcard. **A `/` is not special to it.**

```
if strings.ContainsAny(core, "*?[") { return "", false }
```

`scripts/neargo.py:82` `substring_needle` adds a fourth refusal the engine does not make:

```
if any(ch in core for ch in "*?[") or "/" in core: return None
```

So on any glob whose trimmed core contains a `/`, **the engine has a needle and the mirror does
not.** Since every probe in this tree measures through the mirror, the mirror under-reports what
production actually fires on.

Beat 546 recorded this as *latent* — "no disagreement on current pack content". That was too
generous. Measured today:

- glob values in `rules/carbonato.yaml`: **83**
- values where engine and mirror disagree on the needle step: **33**
- values where that disagreement changes a final verdict, on realistic subjects
  (`.aws/credentials`, `.bashrc`, `.netrc`, `.kube/config`, …): **0 of 640 pairs**

## Why zero, and why that is the honest headline

The suffix chain gets there first. `matchOne` tries whole → every suffix → basename → needle.
For a path like `/home/dev/.aws/credentials`, the suffix `dev/.aws/credentials` matches
`*/.aws/*` under `path.Match` alone, so the needle is never reached.

The needle only decides the outcome when the slash-bearing core appears **inside a longer string
that is not itself path-shaped** — e.g. a joined argv line. Constructing subjects from the pack's
own globs that way yields **6 verdict differences** (`.bash_profile`, `.bashrc`, `.profile`,
`.zshrc`, `authorized_keys`, `crontab` embedded mid-string). Those subjects are synthetic. I am not
claiming a live miss.

## Direction of the error

Every diff runs **engine=True / mirror=False** — the mirror is stricter. For a human reading output
that is the safe direction. For a *detector's own test suite* it is the dangerous one: a test that
measures through the mirror reports a rule as dead or as not-firing when production fires it. That
is the exact failure 545 named — "my mirror is more permissive than the engine" — with the sign
flipped, which is why it went unnoticed.

## Why not fixed now

`neargo` is the measurement instrument for every other probe in this tree. Changing a matcher that
every probe reads through, in the same beat that I found the discrepancy, would invalidate the
numbers in ~20 findings at once and I could not tell which of them changed because the metric
changed vs. because the pack changed.

Correct sequence: land `needleprobe` (done, green, self-checked), then change `neargo` in an
isolated beat and re-run every probe, diffing each result against its recorded value. Any probe
whose number moves gets re-derived and its finding annotated.

## What is proven vs. read

- **Proven here:** the two rules differ; the difference reaches a verdict; the mirror is the strict
  one; 33 of 83 pack globs are subject to the difference.
- **Read, not run:** the engine rule itself is parsed out of `engine.go` by brace-counted source
  reading (`go_block`). No Go toolchain on this host, so nothing here is a compiled run.
- **Not decidable here:** whether any production audit record has ever produced a subject where the
  needle step was load-bearing.

## Self-check (the probe can go red)

`scripts/needleprobe_test.sh` asserts four things, including that patching `engine.go` to reject
`/` makes the probe go **RED** — it must refuse to silently pass when its premise dies, because a
green result then would mean "mirror fixed", which is exactly wrong.

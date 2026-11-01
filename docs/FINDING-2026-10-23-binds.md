# FINDING — 2026-10-23 — the binds predicate is position-dependent

**Status:** finding, no pack change. `rules/carbonato.yaml` untouched. No commit.
**Instrument:** `scripts/bindprobe.py` · **Test:** `scripts/bindprobe_test.py` (21 checks, green)
**Beat:** 533 · written 20:31 ET

## The claim

`carbonato.container.host_root_mount` is a **critical**-severity rule whose entire job is to catch a container
started with the host root or the Docker socket mounted. It is globbing a field that `docker.go` builds by joining
every bind mount with a comma:

```go
detail["binds"] = strings.Join(cfg.Binds, ",")      // internal/telemetry/docker.go:145
```

The engine evaluates `detail_glob_any` against that whole joined string (`engine.go:166-181` → `anyGlobMatches`).
So the value under test is not `/:/host` — it is `/:/host,/data:/data`.

**The rule only fires when the dangerous mount is the LAST mount in the list.**

| bind set | joined value | fires |
|---|---|---|
| `/:/host` | `/:/host` | yes |
| `/etc/localtime:/etc/localtime,/:/host` | `...,/:/host` | **yes** |
| `/:/host,/data:/data` | `/:/host,/data:/data` | **no** |
| `/var/run/docker.sock:/var/run/docker.sock` | — | yes |
| `/data:/data,/var/run/docker.sock:...` | `...,/var/run...` | **yes** |
| `/var/run/docker.sock:...,/data:/data` | `...,/data:/data` | **no** |

7 of 9 cases reach the intended verdict. The two that do not are the same two shapes: the escape mount listed
first, followed by anything else. **The same container, with its mounts in a different order, is invisible.**

## Why — and it is not a transcription error

I did not infer this from my port. Three independent confirmations:

1. `matchOne` never gets a literal needle for any of the three globs: `substringNeedle` strips the outer `*` and
   then **refuses any core containing `/`**. `*:/host*` → core `:/host` → refused. `/:/` → refused.
   `*/var/run/docker.sock*` → refused. So the contains-fallback, which *would* have caught a `,whatever` tail,
   is switched off for exactly the globs that need it.
2. `path.Match` cannot span a comma: the glob's literal `host` cannot match `host,/data:/data`.
3. `pathSuffixes` only cuts at `/`, so the trailing `,/data:/data` never becomes a suffix to try.

So the mechanism is: a mount set is rendered as one string, the glob is anchored at its right edge by literal
text, and nothing in the match ladder is allowed to ignore a trailing tail. Only a mount that happens to sit last
survives.

Note this cuts the *other* way too: `/data:/data,/tmp/x:/tmp/x` does not fire, which is correct, but a container
with `/etc:/host-etc` mounts would fire on `*:/host*` — the rule reads the *destination* tail anywhere in the
string. The glob is doing substring work with anchored semantics.

## Why this matters more than the earlier glob findings

526 (rename reports the source) and 527 (delete reported as write) are misfires that produce a **wrong alert**.
This one produces **no alert**, on a critical rule, for the exact CARBONATO entry vector the rule's own comment
names: *"the container is created directly against an unauthenticated Docker API on :2375."* An attacker who
mounts the socket first and anything else second gets nothing reported. An attacker is not obliged to order their
binds politely.

That makes it the strongest-evidenced silent-miss in the set so far, and it belongs directly under the eBPF sensor
gap in the handover: **a rule that cannot see the mount it names is not a weaker rule, it is an absent one.**

## The fix is a decision, not a patch

I am not touching the pack. But the pack owner should know the options, because two of them look reasonable and are
not:

- **Glob-only fix** (`*:/host*,*`, `*/:/,*`, `*/var/run/docker.sock*,*` — i.e. also allow a trailing remainder):
  works, stays in YAML, but bakes the comma-join into every rule that touches `binds`, and the third one is wrong —
  `*/var/run/docker.sock*` contains `/`, so the needle path stays closed and the comma form needs its own
  enumeration. Fragile by construction.
- **Split the field at the producer** (`docker.go` emits one detail entry per mount, e.g. `bind.0`, `bind.1`, or an
  array-shaped detail): the right shape, and it fixes the class rather than the instance. Requires an `internal/`
  change and a decision about the detail schema.
- **A `detail_glob_any` over a list value** (engine treats a multi-valued detail as OR-over-elements): an engine
  change, larger, but it is the only option that makes every future list-valued detail correct by construction.

My recommendation, for what it is worth: the producer split, then re-run this probe — it should go 9/9 with no
glob edits at all. The comma-join is the defect; the globs are just where it surfaces.

## Testing note, and the part worth remembering

**The probe was wrong first, again, in the same way.** Draft 1 reported 7 of 9 *missed* — including the host root
alone, which obviously fires. Cause: the YAML glob reader kept the opening quote, so the globs read
`- "*:/host*` and `path.Match` rejected every one.

That is the second time tonight. `predaudit` (532) had the identical bug in `predicate_fields` — YAML list items
retaining their quote, turning every `detail_non_empty_any` field into an unknown. Two different scripts, same
mistake, two hours apart. The `strip('"')` fix exists in both now, and both test files pin it, but the pattern
deserves saying out loud: **when I write a new YAML reader, the quote strip is the first thing to test — before
the thing I actually wanted to measure.**

And the same rule from earlier still holds: had I trusted draft 1, I would have shipped "7 of 9 missed" about a
working rule. The printed table disagreed with my expectation in the first row, and the first row was the control.

## Reproduce

```bash
python3 scripts/bindprobe.py        # rc 0 = every case as intended; rc 1 = disagreement named per case
python3 scripts/bindprobe_test.py   # 21 checks
```

Python stdlib only. No Go toolchain needed. Match semantics come from `scripts/neargo.py`, the transcription of
`engine.go`'s matcher that `neargo_test` (13 checks) and `pathmatchdiff` (16 pairs) hold to account.

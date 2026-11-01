# FINDING 2026-10-27 (beat 611) — the host-root mount rule: one real false positive, one dead glob, one stale comment

**Status:** CONFIRMED, unfixed (needs a rule-pack decision — measured only, nothing changed).
**Severity:** MEDIUM. `carbonato.container.host_root_mount` is a **critical**-severity rule. A critical
rule that fires on a benign mount trains people to ignore it; a critical rule with a dead branch
means one escape primitive is uncovered.
**Method:** `scripts/neargo.py` (now Go-faithful after beat 610). **No Go toolchain** — this is
source-reading plus a faithful Python port, not a `go test` run.

## Why this beat exists

Beat 610 ended by claiming the contains fallback was so broad that "a `src:dst` mount spec trips
persistence rules — a precision problem." I sat down to open that as a ticket. **Measured against the
real rules, the claim was mostly wrong**, in an instructive way: I had been testing `path_glob_any`
when binds live in `detail_glob_any`, so I was asking the wrong rules entirely. The correct
measurement says the bind rules are *precise* — and found three different, sharper problems.

## Measurement — rule `carbonato.container.host_root_mount`

Globs (`rules/carbonato.yaml:430`): `*:/host*`, `/:/`, `*/var/run/docker.sock*`

| mount spec | fires? | should fire? |
|---|---|---|
| `/:/host` (host root) | **yes** — `*:/host*` + `/:/` | yes |
| `/var/run/docker.sock:/var/run/docker.sock` | **yes** — socket glob | yes |
| `/var/run/docker.sock:...` second of two binds | **yes** — position irrelevant | yes |
| `/var/lib/postgresql/data:/var/lib/postgresql/data` | no | no |
| `/etc/localtime:/etc/localtime:ro` | no | no |
| `/etc/nginx/nginx.conf:/etc/nginx/nginx.conf:ro` | no | no |
| `/data/crontab:/etc/crontab` | no | no |
| `/app:/app` | no | no |
| **`/etc:/host`** | **yes** — `*:/host*` | **no** ← false positive |
| **`/data:/host/app`** | **yes** — `*:/host*` | **no** ← false positive |

## Defect 1 — `*:/host*` fires on a mount POINT named `host`

The glob matches the *destination segment containing "host"*, not a host-root source. `docker run
-v /etc:/host` is a container mounting the host's `/etc` **into** itself at `/host` — the opposite
direction of an escape, and it trips a critical rule.

Note the asymmetry that makes this easy to miss: `/srv:/home/host` and `/opt:/usr/local/hostname` do
**not** fire, because the needle `:/host` needs the colon immediately before `host`. So the rule fires
on some "host" destinations and not others, which reads like a partial match and is worse than an
obvious over-match.

**A host-root mount is defined by the SOURCE being exactly `/`.** That is not expressible as a
substring glob over a joined string. This is the crux of the next section.

## Defect 2 — the joined-string design is why Defect 1 can't be fixed with a glob

`docker.go:151` does `detail["binds"] = strings.Join(cfg.Binds, ",")`. Every mount spec collapses into
one string, so the matcher cannot tell *source* from *destination* — `:/host` is the same eight
characters whether `host` is where the host root got mounted or merely the name the operator chose.
**No glob over the joined blob can distinguish these.** The fix is structural, not lexical:

- emit `binds` as an array-valued detail (one element per mount), and match source and destination
  separately; or
- emit derived booleans at the source (`binds_host_root`, `binds_docker_sock`) computed where the
  `src`/`dst` split is still known.

Then `*:/host*` can be retired in favour of an exact test on `src == "/"`.

## Defect 3 — the `argv_any` glob `*-v /*:/` can never fire

`rules/carbonato.yaml:352`, rule `carbonato.container.privileged_mount`:

```yaml
argv_any:
  - "*--privileged*"
  - "*-v /*:/"
  - "*--pid=host*"
  - "*--net=host*"
```

`*-v /*:/` has an **interior star**. `substringNeedle` trims one leading and one trailing `*`, then
refuses any core still containing `*?[` — so this glob yields **no needle**, and the contains fallback
is unavailable to it. Measured:

```
needle('*-v /*:/')                                  -> None
match_one('*-v /*:/', "docker run -v /:/host evil") -> False
any_argv(['*-v /*:/'], [docker run -v /:/host evil])-> False
```

The only remaining route is `path.Match`, where `*` does not cross `/`. A joined command line is full
of `/`, so a pattern needing `-v /` then anything then `:` cannot span it. **This branch is dead
code in the rule pack** — one of four alternatives in a privileged-container rule.

This is the exact failure mode the project's own doctrine names: *"a malformed rule that silently
never fires is worse than a startup error."* It is silent, it is in a rule whose siblings all work,
and nothing in the suite caught it.

**Correction to beat 610:** that finding asserted `argv ["docker","run","-v","x:/etc/crontab:/dest"]`
fires via the contains route. **That specific case is true** and re-verified here — but only because
`*/etc/crontab` has a single leading star and no interior star. I then over-generalised it to "joined
argv lines are substring-searched," which is false for any glob with an interior star such as
`*-v /*:/`. The narrow claim stands; the general one is withdrawn.

## Defect 4 — the docker.go comment names a glob the pack does not ship

**CLOSED 2026-10-27 (beat 629).** Comment rewritten; `scripts/commentglobs.py` now compares every glob
quoted in a rule-referring code comment against the *parsed* pack, and `commentglobs_test.py` (7 checks)
plants a stale claim and four look-alikes. See "Closing note" at the end of this section.

`internal/telemetry/docker.go:149-150`:

```go
// Bind mounts joined for a single detail value; rules glob on
// "*:/" to catch the host-root mount specifically.
```

`"*:/"` appears nowhere in `rules/carbonato.yaml` for this rule. Worse, if it *were* used it would be
catastrophic: `*:/` is essentially "a colon", and it fires on **every** mount spec measured —
`/var/lib/pg:/var/lib/pg`, `/etc/localtime:/etc/localtime:ro`, `/app:/app`. And it would not even
catch the thing its comment claims (`/:/` has no trailing `:/`). The comment describes a glob that
would be wrong if implemented. Documentation defect; fix the comment to match the shipped globs, or
delete it.

## What I am *not* claiming

- **Not** claiming the persistence rules are broadly fooled by mount specs. Measured: `/data:/etc/cron
  .d/evil` and `x:/etc/crontab:/dest` do fire, but the realistic benign mounts I tried
  (`/data/crontab:/etc/crontab`) do not. The "precision problem" framing from beat 610 was too strong.
- **Not** changing any rule or telemetry code this beat. Defect 2 is a schema change; that is a
  product decision, and the rule pack is a contract with the detection content.
- **Not** claiming `go test` would agree — I can only read the Go and mirror it. The probe says so.

## Proposed next steps (in order, smallest first)

1. **Defect 3** — confirm with a suite that `*-v /*:/` cannot fire, then either rewrite it as
   `*-v /*` (which *does* yield a needle `-v /`) or split it into an argv-element test. Cheapest, and
   it closes a silent hole in a privileged-container rule today.
2. **Defect 4** — one-line comment fix.
3. **Defect 1/2** — array-valued `binds` + source/destination split. Structure change; needs a rule
   pack revision and a schema decision. This is the one that actually fixes the false positive.

## Gates

46 suites · 1153 checks · 0 failures, unchanged this beat (measurement only, no code edits).

### Closing note (beat 629)

Fixed in `internal/telemetry/docker.go`. The comment now names the two globs the pack **does** ship
(`"/:/"`, `"*/var/run/docker.sock*"`) and records why `*:/` was wrong, so the next reader does not
re-derive it.

The more useful output is the guard. `scripts/commentglobs.py` treats a quoted glob in a code comment as a
**claim about the detection content**, and checks it against the *parsed* YAML — not the file text.

It was wrong three times on the way to being right, and the three bugs are worth keeping:

1. **It grepped the pack file.** `"*:/"` appears inside a YAML *comment* on `carbonato.yaml:436`
   ("REMOVED beat 614: \"*:/host*\" fired on..."). A removed glob, documented as removed, made a stale
   claim read as satisfied. Only live parsed values are globs.
2. **It flagged its own explanation** as a claim, because any quoted region with a glob character
   qualified. That is why the final comment no longer quotes `*:/` at all.
3. **It then over-tightened** the keyword filter until it found *zero* claims — silencing the very defect
   it exists to catch. Caught by re-reading the output and noticing a suspicious clean.

Bug 3 is the dangerous one: it is invisible, because a guard reporting "nothing to verify" looks exactly
like a guard reporting success. `commentglobs_test.py` exists because of it — the planted stale claim (M0)
and the four look-alikes (M2: YAML-comment glob, M3: `filepath.Match` dialect, M4: struct-field `e.g.`
example) fail in different directions, so no retune can pass all of them while being blind.

**Limit, stated:** it reports presence anywhere in the loaded tree, not which rule owns the glob. A glob
that moved between rules still reads as present. And a comment naming a glob that *is* shipped can still
be stale in ways static text cannot see — a renamed rule, a narrowed match. It catches one direction, the
one that matters: a comment promising a protection the pack does not ship.

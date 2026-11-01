# Finding — the `host_root_mount` false positive is gone, and it went by deletion

**Beat 614 · 2026-10-27**
Supersedes the *remediation proposal* in `FINDING-2026-10-27-host-root-mount-rule.md` (defects 1 & 2).
The defect analysis there stands. The proposed fix did not.

## What was wrong

Rule `carbonato.container.host_root_mount` (severity: **critical**) shipped this glob:

```yaml
binds:
  - "*:/host*"
```

Needle `:/host`, does not span `/`. It therefore fired on **any** bind whose destination segment
contained `host`:

| bind | fires | actually a host-root mount? |
|---|---|---|
| `/:/host` | yes | **yes** |
| `/:/host/etc` | yes | **yes** |
| `/etc:/host` | yes | **no** — mounts host `/etc` *into* the container |
| `/data:/host/app` | yes | **no** |
| `/:/hostname` | yes | **no** |
| `/srv:/home/host` | no | no |
| `/opt:/usr/local/hostname` | no | no |

The last two rows are the part that makes this worse than a simple over-match. The glob's badly
formed boundary meant it over-matched **unevenly**: `/etc:/host` tripped a critical alert while
`/srv:/home/host` did not, for no reason an operator could learn. Uneven over-match trains people
to ignore a critical alert, which costs you the true positives too.

## What I first proposed, and why it was wrong

Beat 611 filed the fix as a schema change: `docker.go:151` joins binds with `strings.Join(cfg.Binds, ",")`
before the rule ever sees them, so source and destination are indistinguishable in one blob. The
proposal was array-valued `binds` or derived booleans (`binds_host_root`, `binds_docker_sock`).

That is a real structural limitation (defect 2, still open) — but it was not *necessary* to fix this
defect, and filing it that way put the work behind a decision it didn't need.

Beat 614 tried the narrower glob `*/:/host*`, which pins the source to exactly `/`. That worked:

| bind | `*:/host*` | `*/:/host*` | `/:/` |
|---|---|---|---|
| `/:/host` | yes | yes | yes |
| `/:/host/etc` | yes | yes | yes |
| `/etc:/host` | yes | **no** | no |
| `/data:/host/app` | yes | **no** | no |

And then the measurement that mattered: **`*/:/host*` had zero unique witnesses.** Every bind it
matched was already matched by `/:/`, whose needle `/:/` is a plain substring of `/:/host`. The glob
was doing no work at all.

## The fix: delete the glob

```yaml
binds:
  - "/:/"
  - "*/var/run/docker.sock*"
```

The false positive was not a too-broad glob needing a narrower replacement. It was a **redundant glob
whose only distinct effect was the false positive.** Deleting it removes the false positive and
changes nothing else — which `bindprobe.py`'s 9-case table confirms empirically: `CASES` was written
against the three-glob pack, was not edited, and still passes against the two-glob pack unchanged.
That is the evidence that `/:/` already covered every true host-root mount.

## A witness that was itself a false positive

`bindglob2_test` argued `*:/host*` "earns its place on exactly one shape": `/a:/host,/host`, where two
binds look like one host-root mount because the comma is invisible to a substring test. True — it was
the glob's only unique witness. But `/a:/host,/host` is the binds `/a:/host` and `/host`: **neither
source is `/`.** The shape it uniquely proved was not a host-root mount.

> A glob can earn a unique witness by being wrong. That is not earning a place.

The same suite also listed `/host:/host` as a "genuine mount". It is not — source `/host`. The suite
had blessed a false positive as a true positive and then used it as evidence.

## Residual over-match, stated rather than hidden

`/:/` is a plain substring test, so `-v /:/hostname` still fires — a destination that begins `/:/`
is indistinguishable from host root by substring. **This is real and unfixed.** It is far narrower
than before (the destination must literally start `/:/`) and it is recorded as a known residual in
`bindglob2_test` rather than avoided by picking witnesses that pass.

## What this does NOT fix

Defect 2 stands: `docker.go:151` joins binds into one comma-separated blob, so no glob can reason
about which side of the colon is source. That requires the schema/telemetry change and is still
**Dad's call**. Today's fix did not need it. Defect 4 — the stale `"*:/"` comment at `docker.go:149` —
is also still open, and now names a glob the pack has never shipped in any form.

## Second finding, found by the first

Fixing this exposed a reader bug in `scripts/bindprobe.py`:

```python
for km in re.finditer(r"detail_glob_any:\s*\n((?:[ \t]+\S+:.*\n(?:(?:[ \t]+-[ \t]*.*)?\n)*)+)", body):
```

Adding a YAML comment under `binds:` made this return `{}` — silently. It did not raise. The caller
then `KeyError`'d on `'binds'`, which is the only reason anyone noticed. **A reader that quietly
produces nothing is indistinguishable from a rule that matches nothing** — the same failure class
as a dead rule branch, in the instrument instead of the pack. Replaced with `yaml.safe_load`.

To make it auditable I left the deletion reasoning in the pack as comments. That is what caught it.

## Gates

48 suites · 1181 checks · 0 failures · none below baseline.

Denominators moved by exactly the one deleted value: match values 125 → 124, REACH 78 → 77,
blind upper bound 161 → 159. `bindglob_test` briefly fell from 24 checks to 18 when I rewrote it;
`suitecheck.baseline` refused it, and I restored six checks asserting the deletion's matcher facts
rather than lowering the baseline. That guard does its job.

## Lesson

Beat 613's lesson was "measure the fix before shipping it." Beat 614's is the next step:
**measure whether the fix does anything.** The right question about a glob is not "is it too broad?"
but "does it match anything nothing else does?" A redundant glob, a dead glob, and an over-broad glob
all look identical in a pack read: they all appear to be catching something.

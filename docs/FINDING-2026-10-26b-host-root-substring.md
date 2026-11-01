# FINDING 2026-10-26b — `*:/host*` is a substring test, not a mount test

**Rule:** `carbonato.container.host_root_mount` — severity **critical**, type `container.start`
**Glob:** `*:/host*` (one of three `detail_glob_any.binds` values)
**Status:** OPEN — rule change needs the pack owner (Robbie). Not touched.
**Found:** beat 598, verifying the beat-593b order-dependence note.

## The claim that was wrong

Beat 593b recorded that the comma-joined `binds` string is **order-dependent** — that
`{ binds: ['/var/run/docker.sock:…,/data:/data'] }` and `'/:/host,/data:/data'` miss their globs when the
matching bind comes first.

**Re-measured, and it does not reproduce.** Every ordering I tried fires:

| `binds` value | matching globs |
|---|---|
| `/:/host` | `*:/host*`, `/:/` |
| `/:/host,/data:/data` | `*:/host*`, `/:/` |
| `/data:/data,/:/host` | `*:/host*`, `/:/` |
| `/data:/data,/:/host,/x:/x` | `*:/host*`, `/:/` |
| `/var/run/docker.sock:/var/run/docker.sock,/data:/data` | `*/var/run/docker.sock*` |
| `/data:/data,/var/run/docker.sock:/var/run/docker.sock` | `*/var/run/docker.sock*` |

The join is real (`docker.go:151`), but `matchOne`'s **substring fallback** (`engine.go:280`) runs a
`contains` test for any glob with no interior `/`… and `*:/host*` *does* have interior slashes, so it goes
through `path.Match` plus the suffix branches, which tolerate the trailing comma-joined text the same way in
either order. The note was wrong. **Recorded here so it stops being repeated.**

## What the same measurement did find

`*:/host*` matches on the **target being a prefix-match of the string `host`**, because `path.Match`'s
trailing `*` matches empty. So every one of these fires a **critical** alert:

| real bind | is it host root? | alerts? |
|---|---|---|
| `/:/host` | yes | correct |
| `/:/host/data` | yes (host root, subpath) | correct |
| `/data:/hostdata` | **no** | **false positive** |
| `/mnt:/hostile` | **no** | **false positive** |
| `/x:/host-backup` | **no** | **false positive** |
| `/:/hostname` | **no** — target is `hostname`, not `/host` | **false positive** |

`/:/hostname` is the sharp one: it is a *container* path named `hostname`, the exact naming you would expect
on a real box, and it is indistinguishable from host root to this glob.

## Why the `/:/` glob does not save it

`/:/` is a separate value and fires on `/:/` and `/:/` plus anything comma-joined after it. It does **not**
gate `*:/host*` — `detail_glob_any` is an **OR** over the list, so `*:/host*` alone is sufficient. The two
globs are not a check on each other; each is independently sufficient. That is the structure that makes the
false positive reach production rather than being absorbed.

## What a correct glob would look like (for the owner, not applied)

Host-root-at-`/host` means the bind **target** is exactly `/host` or `/host/<subpath>`. In `path.Match`
terms, matching `s` (the whole comma-joined string) is the wrong unit — the honest form needs the
right-hand side of a `:` boundary. Candidate shapes, none of which I applied:

- `*:/host` and `*:/host/*` — two values instead of one, still fuzzy on the joined string but no longer
  matching `hostdata`/`hostile`.
- `/:/host` and `/:/host/*` — strict: only the literal host root. Loses `/data:/host`, which may or may not
  be intended to alert.

The first is the closer drop-in. Both **reduce** detection breadth, so this is a security-posture decision,
not a bug fix — pack owner's call. Nothing in `rules/carbonato.yaml` was changed by this beat.

## Guard added

`scripts/bindglob2_test.py` now pins the *measured* behaviour in both directions, so the false positives are
visible in the gate instead of living only in this document:

- the true positives that must keep firing (`/:/host`, `/:/host/data`);
- the false positives recorded as **current behaviour**, asserted as `True`, with the test saying in its own
  label that this is a known over-match and not a desired property — so if the pack is tightened later the
  test fails and must be updated deliberately rather than silently.

**Reachability is not detection.** This finding is about the opposite: a rule that fires when it should not.

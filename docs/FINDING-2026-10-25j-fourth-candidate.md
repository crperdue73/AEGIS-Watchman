# FINDING 2026-10-25j — four candidates, three alive, and the dead one had the scariest output

**Status:** OPEN — measurement only. No code or pack change; proposals below.
**Instrument:** `scripts/mechanism_test.py` (16 ok) plus `scripts/neargo.py`; `internal/rules/engine.go`
read, not executed — no Go toolchain on this host. The "candidate 3 is dead" claim is a **mutation
result**, not a reading: replace `base := path.Base(s)` with `base := s` and the suite does not move.

Beat 581. Closing the mechanism inventory started in 25g/25h. I set out to document a **fourth** match
candidate. I finished by proving it doesn't do anything — and the honest version of this beat is that
my first write-up of it was wrong in the same way twice before: I read the code, saw a scary branch, and
believed it before running it.

## What `anyGlobMatches` actually does (engine.go:246-256)

For each glob it tries:

| # | candidate | code | live? |
|---|---|---|---|
| 1 | whole string | `path.Match(g, s)` | **yes** |
| 2 | every trailing suffix split at `/` | `pathSuffixes(s)` | **yes** |
| 3 | basename of the whole string | `path.Base(s)` | **no — subsumed by 2** |
| 4 | substring, for globs with no interior `/` | `substringNeedle(g)` | **yes** |

## Why candidate 3 is dead, and how I found out

`pathSuffixes('/data:/home/x/.ssh/id_rsa')` returns
`['/data:/home/x/.ssh/id_rsa', 'data:/home/x/.ssh/id_rsa', 'home/x/.ssh/id_rsa', 'x/.ssh/id_rsa',
'.ssh/id_rsa', 'id_rsa']` — **the basename is already the last element.** And when the string contains
no `/`, the whole-string candidate is the basename. So candidate 3 can never flip a result.

Measured two ways:
- every suffix of a bind spec was enumerated and `id_rsa` was reached by candidate 2;
- mutant: replace `base := path.Base(s)` with `base := s` → **`mechanism_test.py` stays 16 ok / 0 fail**.
  A surviving mutant is normally a hole in a test. Here it is the *result*: the branch is unreachable.

I had already written the alarming paragraph before checking: *"a container that mounts a secret into a
container reports host key theft via the basename branch."* The behaviour is real —
`'*id_rsa*'` does match `'/data:/home/x/.ssh/id_rsa'` — but **the cause is the suffix split, not the
basename**, and it is not a new mechanism at all. Third time this week the surname of a bug was not the
bug.

## So what's still true

- Three live candidates, one function, all four glob-bearing fields (`exe`/`argv_any`/`path_glob_any`/
  `detail_glob_any`) funnelling through it.
- A bind spec still matches path-shaped globs by suffix — `/data:/home/x/.ssh/id_rsa` matches
  `*/.ssh/*` and `*id_rsa*` — because `pathSuffixes` doesn't know the string is a `src:dst` pair.
- **It cannot reach the secret rules**, because those are `type: file.access` and binds only exist on
  `container.start`. Verified per rule:

| rule | `type:` |
|---|---|
| `secret.read_order_follows_constraint` | `file.access` |
| `secret.read_after_shell` | `file.access` |
| `secret.read_model_key` | `file.access` |
| `persist.file_write` | `file.write` |
| `container.host_root_mount` | `container.start` |

- **That type gate is the only thing standing between a bind string and a critical false positive, and
  nothing enforces it.** `mechanism_test.py` now fails if a `detail_glob_any` rule appears without a
  `type:` gate, and fails if any of those five rules changes type.

## Audited and sound (so the sweep is honest)

- `exeMatches` (engine.go:188) is **exact-or-basename, no globbing**. Only `infra.privileged_container`
  uses `exe_any`; `docker`/`podman`/`nerdctl` resolve correctly from `/usr/bin/docker` via basename. It
  uses `path.Base`, not `filepath.Base` — right on Linux, wrong on Windows, irrelevant here.
- `detail_non_empty*` (4 rules) test presence, not content — a spec can't fool them.
- `endpoint_glob_any` substring semantics are **correct** for hostnames. Candidate 4 is the intended
  behaviour there, not a defect. Don't "fix" it.

## Proposal (not applied)

Either make the invariant explicit in the engine — `detail_glob_any` values must not contain `/` — or
state it in the pack authoring guide. Right now a critical detector's false-positive immunity rests on
an unstated convention that two files agree about by accident.

Pack untouched: **117 / 39 / 78**. No code changed. `mechanism_test.py`: **16 ok / 0 fail**.

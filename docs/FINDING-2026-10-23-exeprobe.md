# FINDING 2026-10-23 — exe_any: the pack is clean, and now there is a guard that says so

**Disposition:** Clean pass. `exe_any` in the shipped pack is all names, no pinned host paths. The probe it left behind is the deliverable.
Short one, because the answer is short and most of its value is the guard it leaves behind.

## The question

`exeMatches` (engine.go:188) says: a rule term matches the exe exactly, or matches `path.Base(exe)`.
That is asymmetric on purpose — `bash` catches `/bin/bash`; `/bin/bash` does **not** catch a bare
`bash`, and catches nothing but itself. So a rule author who writes a path in `exe_any` silently
pins their rule to one host layout. Nothing in the tree asked which shape this pack uses.

## The answer

`scripts/exeprobe.py` — **2 rules carry `exe_any`, 13 terms, 39 reach checks. Every term is a bare
name. 0 terms whose reach depends on the host layout.**

- `exec.shell_spawn`: sh, bash, zsh, dash, powershell, pwsh, python, python3, perl, ruby — all names
- `infra.privileged_container`: docker, podman, nerdctl — all names

Both fire on `/bin/bash`, `/usr/local/bin/python3`, and a copy at `/tmp/python3`, exactly as the
engine comment promises. No pack defect here.

This is the first finding today that ends in "nothing wrong". It is worth logging as one, because
five corrections in a row can leave a reader (or a next-me) primed to expect a defect and invent
one. The guard is the result: `exeprobe_test.py` (9/9) fails the day someone writes
`/usr/bin/curl` into `exe_any`, and it fails with the reach table already printed on both sides of
the assertion.

## The one thing worth carrying forward

`infra.privileged_container` matches `docker`/`podman`/`nerdctl` by name. A container runtime
invoked by absolute path or through a wrapper (`/usr/bin/docker`, `sudo -u x docker`, a
`docker.exe` under a compatibility layer) is caught by name — fine — but a **renamed or vendored
binary** (`/opt/bin/podman-4.9`) is not caught by any term, and cannot be, because the rule
phrases intent as a filename. That is a limit of the rule's design, not a bug, and it is where the
`*-v /*:/` glob conversation from this morning actually belongs: if the runtime check is going to
be strengthened, strengthening the *argv* shape is the honest move, and `argv_any` has no exe gate
on those globs. Recorded here so the idea does not get re-derived from scratch.

## Honest limits

- `shape()` classifies by syntax only: a term with `*?[` is a glob, a term with `/` is a path,
  everything else is a name. A term like `[a-z]sh` is classified glob and is not modelled further.
- `fires()` is a transcription of `exeMatches`, not the function. No Go. Same caveat as `neargo`.
- Reach checks use three synthetic spellings per term. They demonstrate the asymmetry; they are not
  a survey of where binaries live on real hosts.

Vivi 🌺 — 2026-10-23, ~12:50 PM ET

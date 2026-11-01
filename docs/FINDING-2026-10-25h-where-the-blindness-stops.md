# FINDING 2026-10-25h — how far the path-blindness reaches, and where it stops

**Status:** OPEN — measurement only. No rule, pack value, or detector behaviour changed. This finding
*cuts down* the scope claim in beat 578, and pins the remaining safety as a test so it stops being an
accident.
**Instrument:** `scripts/fieldmap_test.py` (12 ok) plus `scripts/neargo.py`; the Go tree was read, not
run — no toolchain on this host.

Beat 579. Beat 578 said "one function, three mechanisms, and it feeds every rule." That last clause was
too big. This is the measurement of the actual blast radius, with the parts that are *fine* named
plainly, because a scope claim I haven't bounded is the same defect as a count I haven't checked.

## The field: 20 rules, 111 quoted match values

| field | rules | values | what it's matched against | source |
|---|---|---|---|---|
| `argv_any` | 7 | 35 | each argv element + exe + joined line | real `execve` (`telemetry.go:135`) or a text stream |
| `path_glob_any` | 4 | 30 | `detail["path"]` | `filewatch.go:66` — an audit target |
| `endpoint_glob_any` | 3 | 32 | `detail["endpoint"]` | network peer |
| `detail_glob_any` | 1 | 3 | named detail key (`binds`) | `docker.go:151` — comma-joined spec |
| `detail_non_empty*` | 4 | 7 | named detail keys | various |
| `exe_any` | 1 | 4 | executable path | `execve` |

## Where it is clean, and I checked instead of assuming

- **`path_glob_any` is NOT exposed.** It reads `detail["path"]`, and the only writer in the tree is
  `internal/telemetry/filewatch.go:66`, which sets it to an audit record's changed target. Nothing
  fills that key with a bind spec. 30 values, correctly anchored on a real path. I expected this to be
  the second front; it isn't.
- **`argv_any` is mostly fine, for a specific reason.** `argv` comes from a real `execve`
  (`telemetry.go:135`) or a text stream (`text.go:44`). So `-v x:/.ssh:/dest` appearing in a *process
  command line* really is a container asking for `~/.ssh`, and alerting on it is correct behaviour, not
  a false positive. I had 32 path-literal globs firing on spec-shaped strings and was ready to call it
  all a bug. Most of it is the rule doing its job.

## Where it is actually wrong

Three shapes, in descending order of confidence:

**1. A spec tail matching a rule via the suffix artefact.** `/:/host` → `':/host'` → `*:/host*`
(beat 577). Only `detail_glob_any` on `binds` does this, because only it passes a comma-joined
`src:dst` string. **Confirmed, measured, already pinned in `bindglob_test.py`.**

**2. `*` absorbing the `src:` prefix.** `x:/etc/crontab` matches `*/etc/crontab` on the whole string,
so a bind spec reads as a host crontab path (beat 578). For `argv_any` this is *correct* when the
command genuinely contains that bind. It is **wrong** when the string being matched isn't two things
concatenated — which is the `binds` case again.

**3. The comma. `detail_glob_any` matches each glob against the whole joined string**
`"/:/host,/data:/data"`, and `pathSuffixes` splits it at *every* `/`, including ones inside the second
bind. So a rule can fire on the tail of the *second* bind while reporting a match on the first. The
globs happen to be loose enough that this rarely changes the verdict — but the verdict is right for a
reason the matcher didn't intend, and the alert text names a mount it didn't match.

## So: 1 field, 3 globs, 1 rule — plus 8 rules that inherit the risk if their field ever changes

The path-blindness is not "every rule." It is **`detail_glob_any`, which exactly one rule uses.** The
reason to write it down anyway: `detail_glob_any` is a *general* field. Any future rule that points it
at a list-shaped or spec-shaped detail key inherits all three mechanisms silently, because nothing in
the type system or the schema says "this key must be a single filesystem path."

That's the finding I'd actually hand to the pack owner, and it's smaller and more actionable than
"the matcher is broken":

1. `anyGlobMatches` should not run `pathSuffixes` on a value it wasn't told is a path (fixes #1);
2. `detail_glob_any` should split on the separator before matching, or its schema should state that the
   value must be a single path (fixes #2 and #3);
3. `path_glob_any` staying on `detail["path"]` with one writer is load-bearing — write that down
   somewhere the next rule author will see it, because it's currently true by accident of who calls it.

## What I refuse to claim

I said in beat 578 that this "feeds every `path_glob_any`, every `detail_glob_any`, and the
joined-line candidate of every `argv_any`." **The first was wrong** — `path_glob_any` never sees a
spec. **The second was right.** **The third is right but not a bug** in most cases. Correcting a scope
claim in public the same day I made it, because the alternative is a finding that sounds bigger than
what I measured.

Pack untouched: **117 / 39 / 78**. No code changed this beat except the tests.

# FINDING 2026-10-26e — `persist.file_write` misses the directory itself

**Rule:** `carbonato.persist.file_write` — **severity: high**
**Status:** OPEN — pack owner's call. No rule changed.
**Found:** beat 601

## The gap, in one line

Every glob in this rule ends in `/*` or names a leaf file. None names a **directory**. auditd reports a
directory target whenever the syscall acts on the directory rather than a file inside it — `nametype=DELETE`
on `rm -rf`, `nametype=CREATE` on `mkdir`, `PARENT`-skipped so the dir path itself is the target.

`filewatch.go:104-108` (`firstTargetPath`) returns that directory path, `writeTarget` accepts it under
`strictWriteSyscalls`, and `WriteAction` emits `detail["path"] = "/etc/cron.d"`.

The globs then miss it.

## Measured

Ten `path_glob_any` values. Running each against the shapes the adapter can emit:

| syscall effect | emitted `path` | verdict |
|---|---|---|
| write `/etc/cron.d/evil` | `/etc/cron.d/evil` | **fire** |
| write `/root/.bashrc` | `/root/.bashrc` | **fire** |
| write `~/.ssh/authorized_keys` | `/…/authorized_keys` | **fire** |
| `rm -rf /etc/cron.d` | `/etc/cron.d` | **MISS** |
| `rm -rf /etc/cron.daily` | `/etc/cron.daily` | **MISS** |
| `rm -rf /etc/cron.hourly` | `/etc/cron.hourly` | **MISS** |
| `rm -rf /etc/systemd/system` | `/etc/systemd/system` | **MISS** |
| `mkdir /etc/cron.d` | `/etc/cron.d` | **MISS** |
| `rm /etc/crontab` | `/etc/crontab` | **fire** |

Cause is mechanical and was measured in beat 591: `*/etc/cron.d/*` requires a character after the final `/`
(the glob's trailing `*` may match empty, but the literal `/` before it must exist). `/etc/cron.d` has no
trailing slash, so there is nothing for the final `/*` to consume.

## Why this one matters more than the average finding

`persist.file_write` is the rule that catches an agent establishing persistence. Three of the four paths it
watches are **directories**, and the two ways to defeat persistence detection are:

1. **Plant something** — caught (the globs work for files).
2. **Remove or replace the mechanism** — `rm -rf /etc/cron.d`, or delete `/etc/systemd/system` and recreate
   it empty so planted units vanish from view. **Not caught.**

Removal is the anti-forensics move. An agent that wants its tracks gone deletes the directory, not the file.
The rule reports high severity and is entirely silent on that.

`mkdir /etc/cron.d` is the companion case: creating an empty watched directory is the first half of
"remove, then recreate, then plant somewhere auditd is not watching" — and it is silent too.

## What a fix looks like (none applied)

For each directory-valued glob, add the bare directory as a sibling value. Go `path.Match` semantics mean the
two shapes are genuinely different and neither subsumes the other:

```yaml
- "*/etc/cron.d/*"      # a file inside
- "*/etc/cron.d"        # the directory itself (DELETE / CREATE / RENAME)
```

Four directories × 8 globs affected: `/etc/cron.d`, `/etc/cron.daily`, `/etc/cron.hourly`,
`/etc/systemd/system`. The leaf-file globs (`*/etc/crontab`, `*/.bashrc`, `*/.bash_profile`, `*/.profile`,
`*/.zshrc`, `*/authorized_keys`) are already correct — a file delete is caught, as `rm /etc/crontab` showed.

Note the alternative is *not* simply wiring the glob to `*` — that would match `/etc/cron.d/anything` and is
already covered. The missing case is the exact-path directory.

**Cost of the fix:** more alerts on legitimate package upgrades — `apt` removing `/etc/cron.daily` entries
will now fire if it removes the directory. That is a tuning decision for the owner, which is why it is not
applied here.

## Why the shipped instrument cannot see this

`InertRules` (finding 26d) asks only whether `file.write` is attached. It is. The rule therefore reports as
**can fire** — correctly, since it does fire on files — and nothing in the startup instrumentation notices
that 40% of the watched targets are unreachable.

This is the same envelope/contents distinction, now with a security consequence rather than a tidiness one.


## Beat 604 — the chain verified end to end, and a second gap found

Beat 601 asserted that a directory delete reaches the engine with `path="/etc/cron.d"`. That was reasoning
from three functions; this beat traced it and found it holds — but only because of a detail worth writing down.

**The record `rm -rf /etc/cron.d` produces** (files gone first, then the directory):

```
type=SYSCALL syscall=rmdir success=yes exe="/usr/bin/rm"
type=PATH item=0 name="/etc/"        nametype=PARENT
type=PATH item=1 name="/etc/cron.d"  nametype=DELETE
```

Traced through `filewatch.go`:

1. `writeTarget()` (`:88`) checks `nametype` **before** the syscall map. `DELETE` on
   `/etc/cron.d` → returns `("/etc/cron.d", true)` immediately.
2. `firstTargetPath()` (`:104`) skips `PARENT`, so the `/etc/` record never wins.
3. `WriteAction()` (`:60`) emits `{path: "/etc/cron.d", syscall: "rmdir", nametype: "DELETE"}`.
4. Engine, rule, globs → **no match.**

So the action arrives intact and is thrown away by the pattern. Confirmed.

**The second gap, found while tracing it:** `rmdir`, `mkdir`, and `mkdirat` appear **nowhere in the
codebase** — not in `strictWriteSyscalls`, not in `openSyscalls`, not in any test. Verified:
`grep -rn '"rmdir"\|"mkdir"\|"mkdirat"' internal/ --include=*.go` → zero hits.

Today this is masked, because step 1 rescues those records via the nametype. But the rescue is
**conditional on auditd tagging the PATH**. Cases where it would not:

- `mkdirat` creating a directory whose only PATH record is `CREATE` — fine — but a `rmdir` on a path outside
  every configured watch key still carries `DELETE`, so also fine. The mask holds in the ordinary case.
- The real exposure is `rename`-based attacks: `renameat2` is in the map, so covered. `rmdir` is not, so a
  record that ever arrives **without** a nametype — an auditd config with `log_format=ENRICHED` quirks, or a
  PATH line the parser does not tag — is silently dropped rather than routed.

Stated as narrowly as I can: **there is no test asserting what happens to a `rmdir`/`mkdir` record whose PATH
carries no nametype.** That is a missing fixture, not a proven defect. The failure mode if it ever bites is a
silent drop, which is exactly the class this project exists to catch.

**Not fixed.** Adding `rmdir`/`mkdir`/`mkdirat` to `strictWriteSyscalls` is a one-line change to rule-relevant
behaviour, and `mkdir` in a watched directory is arguably not a write to that directory's contents. Owner's
call, same as the globs.

### What I should have done in beat 601

Traced the call chain before publishing. The conclusion survived, but publishing a four-step claim off three
function reads left a gap between "should be" and "is" that I then had to close four beats later. The
conclusion was right; the evidence standard was not.


## Beat 605 — the missing fixture is written, and the gap is worse than "untested"

`internal/telemetry/filewatch_test.go` now carries **`TestDirectoryOpsWithAndWithoutNametype`**, four cases.

Writing it settled the open question from beat 604 and moved the answer the bad way:

**An operator's watch key does not save an `rmdir`/`mkdir` record.** `writeTarget()`'s last rescue reads

```go
if r.WatchKey != "" && openSyscalls[r.Syscall] { return target, true }
```

The key is **ANDed** with `openSyscalls` — so `-k` only helps the `open` family. `rmdir`, `mkdir`, and
`mkdirat` are in neither map, so tagging a watch with `key=` and watching a directory gives **no** protection
for an untagged PATH record. The operator would reasonably believe otherwise.

The four cases, pinned:

| record | rescued by | verdict |
|---|---|---|
| `rmdir` + PATH tagged `DELETE` | nametype check (runs first) | **kept** → `/etc/cron.d` |
| `rmdir` + PATH **untagged** + `key=watch_etc` | nothing — key ANDed with `openSyscalls` | **dropped** |
| `mkdir` + PATH **untagged** | nothing | **dropped** |
| `unlinkat` + PATH **untagged**, no key | `strictWriteSyscalls` | **kept** |

So the boundary is: **tagged directory ops are fine, untagged ones vanish, and a watch key cannot rescue
them.** Today auditd tags them, so no live incident — but the defence is one `nametype=` field on one PATH
line, and the documented operator mitigation does not work for it.

**Fixture-writing caught two of my own errors, both before the gate:**

1. My first helper did `if len(recs) != 1 { t.Fatalf(...) }`. But `ParseAuditStream` **drops**
   non-actionable records (`telemetry.go:229`), so the untagged cases return *zero* records, not one with
   `ok=false`. The fixture would have failed with "got 0 records, want 1" — a true failure, for the wrong
   reason. Rewritten so zero is a legitimate answer.
2. I asserted the key was ineffective before checking `&&` versus `||`. Reading it took one line; guessing
   would have shipped a wrong test.

**Still not fixed.** Adding `rmdir`/`mkdir`/`mkdirat` to `strictWriteSyscalls` is still the owner's call, and
still arguable: `mkdir` in a watched directory modifies that directory's metadata, and treating it as a write
to the *parent* is not obviously right. What is no longer arguable is that the current state is undocumented
and untested.

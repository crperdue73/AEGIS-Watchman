# FINDING 2026-10-24 — the 12:18 pack timestamp: what's ruled out, what's still open, and the part I'll never know

**Disposition:** Open, narrowed. Three explanations are eliminated by evidence; one remains unverifiable
from this host. Recorded so the next reader starts where I stopped instead of re-asking.
**Instrument:** `scripts/packprobe.py` (12 checks, green — exit status, not recollection)

543 recorded that `rules/carbonato.yaml` has mtime **2026-10-23 12:18**, 9h46m after the investigation's
first note (02:32), while ten of my own notes describe the pack as "untouched". Lydia's note on the bus:
ask the owner *why* the file changed, not *what* — the why is what the notes couldn't record. That's
right, and it splits the question into the four parts that have answers.

## Ruled out

**Not a tree-wide event.** Within 40 minutes of 12:18 only three files have mtimes: the pack itself,
and `scripts/exeprobe.py` + `exeprobe_test.py` at 12:31 — my own work from that hour, 13 minutes later
and unrelated to the pack. No migration burst, no checkout spread, no directory of siblings rewritten
at the same second. Whatever happened at 12:18 happened to this one file.

**Not one of my probes writing.** 25 of 76 scripts reference the pack; every one of them reads. Six
shell tests *do* write `rules/carbonato.yaml`, and every one writes it **under `$(mktemp -d)`** — a
throwaway tree, never the real file. Zero files under the project hold the pack open. A probe that only
reads cannot produce that mtime.

**Not a content swap that invalidates the study.** The pack on disk today is 20 rules, 16,324 bytes,
439 lines, ids unique, ends on a complete rule (no truncation, no half-written YAML), and still
contains both dead globs the 10-23 thread rests on: `*/etc/cron*` and `*/.profile`. If the 12:18 write
had changed the pack's substance, the investigation would have been measuring a file that no longer
exists. It wasn't. sha256 `a2cffbd659905b18…`.

## Still open

Who wrote, and why. And a distinction I cannot make: **mtime does not say whether the bytes changed.**
A rewrite with identical content touches the timestamp as surely as a real edit does. Some tool that
parses-then-reserialises the pack (a formatter, an editor's save-on-focus, a pack tool that validates
and writes back) produces exactly this without changing semantics — and that would also explain why the
notes' "untouched" claim stayed *true in substance* while the timestamp says touched in fact.

The one artefact that would have settled it is a hash taken before the write. None was. So the honest
state is: *the pack may have been rewritten at 12:18 by something outside this investigation; its
content still matches what the investigation measured; the why is unknown and needs whoever owns that
file to answer it.*

## What I changed as a result

`scripts/packprobe.py` now prints the pack's **sha256** every run. One line, and the next unexplained
mtime is a diff instead of a mystery. That is the only durable fix available from here — you cannot
audit a filesystem you have no history of, but you can stop being surprised by the same thing twice.

## Limits

Filesystem and source-level only. No Go, no `.git` (gone since the 10-08 migration), no auditd trail in
the tree, and I did not read another agent's gateway log — that isn't mine. `packprobe` can prove the
three exclusions; it cannot name a culprit, and it says so in its own output rather than implying
coverage it doesn't have.

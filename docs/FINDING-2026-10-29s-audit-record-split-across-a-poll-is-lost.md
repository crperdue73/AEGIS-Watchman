# FINDING 2026-10-29s — an auditd event split across a poll boundary is lost, not duplicated

**Status:** Instrument added (`scripts/auditsplit.py`). Static analysis; no Go, no live auditd.
**Instrument:** `scripts/auditsplit.py` · **Beat:** 668

## I went looking for the opposite thing

After five beats of "a signal that cannot say no" (663–667), I set out to find the **false-positive** side: collector
noise. `Engine.Match` (rules/engine.go:56-79) has no dedupe and no memory — one event per matching rule per action, every
time the action appears. So whether the detector is noisy is entirely a question of whether the **source** re-emits.

I expected duplication. I measured the inverse: **a real event can be dropped entirely**, silently, and the bytes are
gone by the time anyone could notice. Recording that reversal here because the finding only exists because I asked a
question I thought I already knew the answer to.

## The mechanism, measured

auditd writes one logical event as **several lines** sharing a `msg=audit(TS:SERIAL)` trailer — a `SYSCALL` line plus a
`SOCKADDR` / `PATH` line. `ParseAuditStream` groups by serial *within a single read* (telemetry.go:215-228). That is
correct — for one read.

`FileSource.Actions` (engine.go:156-214) reads only `offset .. last complete newline`, then advances `f.offset` past
what it consumed. Its state is exactly three fields (engine.go:103-107): **`path`, `offset`, `cursor`. There is no
buffer for a partial record.**

So when a poll boundary falls between the two lines of one event:

**read 1 — SYSCALL only.** → `AuditRecord{Syscall: "bind", BindAddr: ""}`.
`isActionable()`: `case ListenBind, ListenListen: return r.BindAddr != ""` → **false**. Dropped. `f.offset` has already
advanced past these bytes, so they are never re-read.

**read 2 — SOCKADDR only.** → a *new* `AuditRecord{Serial: 42}` whose `Syscall` is `""` (it was never seen).
`applyConnectLine` routes on `rec.Syscall == ListenBind`, which is now false, so **the address is filed under `SockAddr`
— the connect field — not `BindAddr`** (egress.go:110-118). Then `isActionable()`: `Syscall ""` falls through to the
execve case; `Exe` and `Argv` are both empty → **false**. Dropped again.

**The bind exists in the log, complete, in two lines. Matched together it fires `carbonato.net.listener_bound`
(`detail_non_empty: ["listen"]`). Split between polls it never fires, and nothing anywhere records that a line was
skipped.**

## Why this is the cold case, not an exotic race

`watch.poll_interval` defaults to **60s**. The read window ends at the last newline present *at that instant* — so on
any live stream the boundary falls somewhere arbitrary, and a busy host has an event straddling it essentially every
poll. This is not tail-eating; it is the ordinary behaviour of a **line-oriented reader over a record-oriented source**.

## Credit — the second-line logic is right, and that is what makes it fail quietly

The authors handled the obvious case deliberately: egress.go:17-21 explains that `listen` on an already-bound socket
carries no SOCKADDR, and `decodeSockAddr` failing on it is *correct*. The code is thoughtful. The gap is that the
thought stops at **one read**. Careful handling of "this line has no address" makes a *split* record look exactly like a
legitimately empty one — right conclusion, wrong reason.

## Claim / not claimed

**Claimed:** `FileSource` carries no partial-record state (3 fields, verified); `isActionable` rejects a SYSCALL-only
bind; the orphaned SOCKADDR is misfiled into `SockAddr` because `rec.Syscall` is empty on a fresh read; both halves are
dropped with the bytes already consumed.

**Not claimed:** that I observed this on a live host — no auditd, no Go toolchain here; this is static analysis and the
routing claim is a reading of two functions I transcribed rather than executed. **Not claimed: that every rule family is
affected.** `execve` is actionable on the SYSCALL line alone (`Exe != ""`), so an exec survives a split. **The loss is
specific to action types whose actionability depends on the second line — bind/listen, connect, and file writes.** That
is narrower than "the detector misses events," and narrower is what the measurement supports.

## Fix (Dad's call)

**Carry the open tail across polls**: a small `pending []byte` on `FileSource`, prepended to the next read, so one
logical event is always parsed in one pass. Also keep any record whose serial is still open. The alternative (re-reading
from the last seen serial) needs more state and adds failure modes. A cheap companion: count and log skipped
non-actionable records, so "we dropped a SOCKADDR with no syscall" stops being invisible.

## Mutation proof

| mutation | result |
|---|---|
| `FileSource` gains `pending []byte` (**the proposed fix**) | **FAIL** ×2 ("exactly offset/path/cursor", "no pending concept") · rc=1 |
| `isActionable` bind/listen → `return true` | **FAIL**, "actionable ONLY with a BindAddr" · rc=1 |
| control | 12 rows green · rc=0 |

## Gate

**81 suites / 1608 checks / 0 failures.** Baseline rewritten (`auditsplit.py 11`).

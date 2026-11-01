# FINDING 2026-10-29n — sourceWatch checks that the log exists, not that it is saying anything

**Status:** Instrument added (`scripts/feedblind.py`). The premise under beats 659–663: that the engine is *looking at* the log.
**Instrument:** `scripts/feedblind.py` · **Beat:** 664

## The liveness test, in full

```go
// behavior.go:58-60 — this is everything
func (w *sourceWatch) observe() (string, bool) {
    _, err := os.Stat(w.path)
    present := err == nil
```

Called once per Poll (behavior.go:397). **Existence is the entire liveness signal.**

`sourceWatch`'s own comment says: *"a quiet loss of coverage is the failure this tool exists to refuse."* That is the
right principle, and it **does** honour it for a source that **disappears** — the transition is logged once, and the
recovery is logged once. That part is good work and I want it recorded as good work.

The gap is that existence is not progress. Two ways the feed goes blind while `os.Stat` keeps succeeding:

## Case 1 — auditd stops, file remains

auditd dies or is stopped. `audit.log` still exists. `FileSource.Actions` finds `size == f.offset` and returns
`nil, nil` — **not an error.** Measured over the simulation: **1000 consecutive polls → 1000 empty reads, 0 warnings.**

A host whose auditd died is **indistinguishable from a calm host.** Both look like "present, nothing new, healthy." That
is the exact failure shape this tool exists to refuse, arriving through the one state the watch does not model.

## Case 2 — rotation into an empty file

```go
// engine.go:172-176
size := st.Size()
if size < f.offset {
    f.offset = 0            // "Log rotated/truncated: start over."
}
```

`logrotate` with `create` renames `audit.log` → `audit.log.1` and creates a fresh empty `audit.log`. Measured:

- `size(0) < offset(18)` → **offset reset to 0**, 0 events read from the new file
- **the unread tail in the OLD inode is never read again** — `size < offset` redirected the reader to the new path, and
  the old inode is never reopened (measured: `pos` stays on the new file, `line4` never read)
- **the watch printed nothing at either step**

So records that landed between the last read and the rotation are dropped silently, and the reset itself is silent. The
code comment calls this "start over," which is a reasonable recovery — it is the *silence* that is the defect, not the
recovery.

## What is absent, checked as mechanism

`sourceWatch`'s struct body has exactly four fields: **`name`, `path`, `present`, `known`**. No `lastSeen`, no
`lastSize`, no `lastMod`, no `inode`. Checked by parsing the struct body — not by searching the comment, which mentions
"lost" and "quiet" and would have let a prose match masquerade as evidence.

The state the watch is missing is the obvious third one: **present but not advancing.**

## Claim / not claimed

**Claimed:** the liveness test is existence-only; a stopped auditd and a rotation-into-empty are both invisible to it;
the reader treats any shrink as a rotation, silently resets to 0, and never revisits the old inode; on a quiet host both
cases yield zero events and zero signals indefinitely.

**Not claimed:** that logrotate's default on the target hosts produces case 2 — `copytruncate` truncates in place (also
a shrink, same reset) while `create` swaps the inode (the two differ, and I did not inspect the hosts' configs). Not
claimed: that any deployment has run blind. This is code reachability plus a faithful simulation of the two functions.
**No live auditd was stopped.**

## Fix (Dad's call)

Give `sourceWatch` the third state: track `(inode, size, mtime)` and warn when the file is **present but has not
advanced for N intervals** — the same "say it once on transition" discipline it already uses for presence. That catches
both cases above and needs no wire change. It is a few fields and one comparison, and it closes the gap in the watch's
own stated purpose.

## Mutation proof

| mutation | result |
|---|---|
| add `lastSeen time.Time` to the `sourceWatch` struct | **FAIL** ×2 ("has only [4 fields]", "absent: lastSeen") · rc=1 |
| `f.offset = 0` → `return nil, fmt.Errorf("audit log shrank…")` | **FAIL**, "the reader treats a shrink as rotation and resets to 0" · rc=1 |
| control | 11 rows green · rc=0 |

The second mutant is worth noting: making shrink **loud** is achievable in one line. The silent reset is a choice, not a
constraint.

## Gate

**77 suites / 1560 checks / 0 failures.** Baseline rewritten (`feedblind.py 11`).

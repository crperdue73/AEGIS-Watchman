# FINDING 2026-11-06c — the loss-of-coverage log can say a file vanished, but not that it ROTATED

**Instrument:** `internal/collector/behavior.go` `sourceWatch.observe()` (:58-77), the one place the collector reports losing its auditd feed.

**Status:** observer gap. Product change left to Dad; the documented claim understates the tool.

## What the message carries, measured (beat 839)

Three ways to lose the feed, run against a real temp dir:

| cause | message |
|---|---|
| `rm` | `… "gone.log" disappeared — Layer 3 loses its auditd feed until it returns: stat …/gone.log: no such file or directory` |
| `mv x.log x.log.1` (log rotation) | `… "rot.log" disappeared — … : stat …/rot.log: no such file or directory` |
| `chmod 000` by an unprivileged owner | **empty string.** `os.Stat` succeeds with mode 000 when the caller is root, so presence is unchanged and the watch correctly stays silent. |

**The deleted and the rotated cases are byte-identical except for the path.** Both print `no such file or directory` — which is true of the syscall and false of the world. The single most common way to lose an auditd feed on a healthy host is **log rotation**: the daemon renames the file and creates a new one, the collector keeps polling the old inode's path, and the operator gets a message that reads like somebody deleted evidence.

## Why that matters more than it looks

The `sourceWatch` comment says the failure this tool exists to refuse is *a quiet loss of coverage*. This log line is the refusal. But a message that cannot distinguish "the file is gone" from "the file was rotated out from under me" pushes the hardest question back onto the operator at exactly the moment they have the least context:

- rotation → the collector should be re-opened on the new file, and the loss may be a few seconds;
- deletion → someone is covering tracks, and the loss may be the incident itself.

Same bytes in the log, opposite next actions. A detector that reports a tracking-cover deletion in the same words as routine rotation has handed the operator a false positive and a false negative in one line.

## What the code already has, and what it would take

`observe()` holds `err` from `os.Stat` and interpolates `%v` — so `errors.Is(err, fs.ErrNotExist)` is one branch away, and Go's `*PathError` already distinguishes `ENOENT` from `EACCES`/`EPERM`. It would not even need a new syscall: **stat the parent directory** (`inode` of the dir, or a re-check) to tell "path gone" from "path gone because the name changed".

Not done here, deliberately: it changes an operator-facing string and adds a syscall per poll — product behaviour, Dad's call. What I can do honestly is write the gap down and stop the doc from implying the message is diagnostic.

## The generalisable bit

A log line is an instrument. I have spent a week scoring mutants that change a *return* value; this one changes what a **human can conclude**, and no test in the tree could ever have noticed, because every assertion on this message is `strings.Contains(msg, "disappeared")`. **The tests pin the word, not the information.** That is the same disease as an equivalence claim in a test name — a green that answers a question nobody asked.

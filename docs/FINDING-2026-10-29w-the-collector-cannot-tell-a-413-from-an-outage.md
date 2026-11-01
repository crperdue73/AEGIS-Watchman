# FINDING 2026-10-29w — the collector cannot tell a 413 from an outage, so every fix that needs that distinction is blocked

**Status:** Instrument added (`scripts/opaqueupstream.py`). Static analysis; no Go, no running collector.
**Instrument:** `scripts/opaqueupstream.py` · **Beat:** 672 · **Keystone for:** 660, 662, 671

## The question

Beats **660**, **662** and **671** each ended with a fix of the same shape: *retry transport failures, treat a content
rejection as permanent — quarantine it, count it, continue.* All three fixes assume a caller can **tell those apart**.

So I measured whether it can.

## It cannot — and not by a coding slip, by a type

`Client.SubmitActions` (client.go:122-145) and `Client.Submit` (client.go:67-92) both end:

```go
resp, err := c.HTTP.Do(httpReq)
if err != nil {
    return fmt.Errorf("post actions: %w", err)          // transport
}
defer resp.Body.Close()
if resp.StatusCode < 200 || resp.StatusCode >= 300 {
    return fmt.Errorf("central returned %s", resp.Status)   // ANY non-2xx
}
```

Both branches return a plain `error`. The status survives only as a **string** — `resp.Status` is
`"413 Request Entity Too Large"`. So:

| what happened | what the collector produces |
|---|---|
| 413, permanent, on contents | `central returned 413 Request Entity Too Large` |
| 503, transient | `central returned 503 Service Unavailable` |
| 401, bad token | `central returned 401 Unauthorized` |
| TCP reset / DNS / timeout | `post actions: <net error>` |

An upstream branch can distinguish **only the last one**, and only by string matching. Measured: **zero** occurrences of
`errors.Is`, `errors.As`, `errors.New`, or a declared sentinel `Err` anywhere in `internal/collector`. There is no typed
error. The comment at client.go:121 promises a non-2xx is "an error so the caller can log or retry" — **the caller cannot
retry intelligently, because nothing in the error carries a kind.**

## The reason central wrote is closed unread

The non-2xx path calls `defer resp.Body.Close()` and never reads. Beat 660 measured that central's 413 body carries a JSON
error naming the reason. The collector discards it, so the operator reading
`collector: spool drain paused at 3/17: central returned 413 Request Entity Too Large` learns the status but not the size,
the limit, or which record. **Central took the trouble to explain itself and the explanation never leaves the socket.**

## Why this is the keystone, not one more hole

This is the cheapest item on the whole list and it **unblocks two others**. Beat 659 showed `Drain` stops on the first
failure — and beat 671 explained why that is fatal for a poison record. The version worth writing down: **Drain is not
being timid. Stop-on-first-failure is the only correct behaviour available when the error's type is
`*errors.errorString`** — it cannot know whether retrying is right, so it degrades to the safe-looking choice. The fix in
660/662/671 is not "make Drain braver"; it is "give the error a kind, then Drain can branch."

## Claim / not claimed

**Claimed:** both methods return a bare `error` with the status only as a formatted string; no typed error, sentinel, or
`errors.Is`/`errors.As` exists in `internal/collector`; the non-2xx body is never read; no backoff/retry/`time.Sleep`
exists in the package; the comment promises retryability the type cannot deliver.

**Not claimed:** that the collector *should* own a retry loop in v1 — it deliberately does not; the spool is the durability
mechanism (beat 670). **Not claimed** that a human reading logs can't tell a 413 from an outage — by eye they obviously
can. The claim is about what **code** can branch on, because that is what a fix needs.

## Fix (Dad's call — none of it changes the wire)

1. Return a typed error, e.g. `UpstreamError{StatusCode int, Body []byte}`, so callers can `errors.As`.
2. Read a **bounded** slice of the body on non-2xx (a few KiB) so the reason central wrote survives to the log.
3. Honour `Retry-After` when a proxy provides one.

## Mutation proof

| mutation | result |
|---|---|
| `NewUpstreamError(resp.StatusCode, nil)` at the SubmitActions boundary (**fix 1**) | **FAIL**, "every non-2xx becomes the same formatted error" · rc=1 |
| `io.ReadAll(io.LimitReader(resp.Body, 4096))` on the error path (**fix 2**) | **FAIL**, "never reads resp.Body before returning" · rc=1 |
| control | 12 rows green · rc=0 |

**Caught while writing it:** the first run crashed with `IndexError` because I wrote `Client.SendInventory` — a method name
I had *remembered* from my own prose. The real method is `Client.Submit` (client.go:67). Ninth instance in this project of
reading something adjacent and calling it the code; the fix is to enumerate from `grep '^func (c \*Client)'` rather than
from memory.

## Gate

**85 suites / 1649 checks / 0 failures.** Baseline rewritten (`opaqueupstream.py 12`).

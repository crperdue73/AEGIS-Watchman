#!/usr/bin/env python3
"""opaqueupstream — the collector turns every central answer into the same error,
so the retry/backoff it was designed for can never be written.

THE QUESTION
------------
Beats 660 and 671 both ended with the same recommended fix: "treat 413 as a permanent
content verdict (quarantine/drop/count/continue); retry only transport failures." That fix
requires the caller to be able to TELL a 413 from a network error. So this beat I measured
whether the collector can.

IT CANNOT, AND NOT BY ACCIDENT OF CODING -- BY TYPE
----------------------------------------------------
`Client.SubmitActions` (client.go:122-145) and `Client.SendInventory` (client.go:69-92) both end
with:

    resp, err := c.HTTP.Do(httpReq)
    if err != nil {
        return fmt.Errorf("post actions: %w", err)      // transport error
    }
    defer resp.Body.Close()
    if resp.StatusCode < 200 || resp.StatusCode >= 300 {
        return fmt.Errorf("central returned %s", resp.Status)   // ANY non-2xx
    }

Both branches return `error`. The status code exists only as a **string**: `resp.Status` is
"413 Request Entity Too Large". So:

  - 413 permanent-content-rejection  -> "central returned 413 ..."
  - 503 transient                    -> "central returned 503 ..."
  - 401 bad token                    -> "central returned 401 ..."
  - TCP reset / DNS / timeout        -> "post actions: <neterr>"

An upstream branch can distinguish exactly one of these: the last one, and only by string
matching. There is no typed error, no sentinel, no `errors.Is`/`errors.As` anywhere in
`internal/collector` (measured: zero occurrences outside tests). The comment at client.go:121
says a non-2xx is "an error so the caller can log or retry" -- but **the caller cannot retry
intelligently, because nothing in the error says whether retrying is the right move.**

THE BODY IS THROWN AWAY TOO
----------------------------
`resp.Body` is closed and never read on the error path. Beat 660 measured that central's 413
response carries a JSON error body naming the reason. The collector discards it, so an
operator reading `collector: spool drain paused at 3/17: central returned 413 Request Entity
Too Large` learns the status but not the size, the limit, or which record. The explanation
central took the trouble to write never leaves the socket.

WHY THIS IS THE KEYSTONE, NOT ONE MORE HOLE
---------------------------------------------
Beats 660, 662, and 671 each concluded with a fix that depends on this distinction. Beat 659
already showed the *consequence* of the collapse: `Drain` stops on the first failure because
it cannot know whether stopping is right -- and stopping is right for an outage and wrong for
a poison record. Drain is not being conservative out of timidity; **it is being conservative
because the error type gave it no choice.** So this is the cheapest fix on the list and it
unblocks two others:

    1. classify the error at the boundary (typed/sentinel: Transport vs Rejected-4xx vs Server-5xx)
    2. read the body before returning it, bounded (a few KiB), so the reason ships too
    3. honour Retry-After when central (a real proxy) provides one

CLAIM / NOT CLAIMED
-------------------
CLAIMED: both client methods return a bare `error` with the status only as a formatted string;
    no typed error or errors.Is/As exists in the collector; the error body is never read on
    non-2xx; no retry/backoff exists anywhere in internal/collector.
NOT CLAIMED: that the collector *should* have retry logic in v1 -- it deliberately does not, and
    the spool is the durability mechanism instead (670). NOT CLAIMED: that a network error and a
    413 are indistinguishable to a human reading logs -- they are, by eye. The claim is about
    what CODE can branch on, which is what a fix needs.
"""

import sys
import pathlib
import re


def main():
    root = pathlib.Path(__file__).resolve().parents[1]
    fails = 0

    def check(name, ok):
        nonlocal fails
        print(("ok   " if ok else "FAIL ") + name)
        if not ok:
            fails += 1

    try:
        client = (root / "internal/collector/client.go").read_text()
    except Exception as e:  # noqa: BLE001
        print(f"FAIL could not read input ({e}); a missing input is not a pass")
        return 1

    # comment-free view: a mechanism is an identifier, not a sentence (beat 669 lesson)
    body = "\n".join(l for l in client.splitlines()
                     if not l.strip().startswith("//"))

    print("-- both methods collapse every non-2xx the same way --")
    for fn in ("func (c *Client) SubmitActions(", "func (c *Client) Submit("):
        seg = body.split(fn)[1].split("\n}\n")[0]
        name = fn.split("Client) ")[1].rstrip("(")
        has = re.search(r'fmt\.Errorf\("central returned %s", resp\.Status\)', seg) is not None
        check(f"{name}: every non-2xx becomes the same formatted error", has)

    print("-- the status is a string, not a type --")
    check("exactly 2 status checks exist (both collapse)",
          len(re.findall(r"resp\.StatusCode < 200 \|\| resp\.StatusCode >= 300", body)) == 2)
    check("no typed/sentinel error: no errors.New / var Err / custom error type declared",
          "errors.New(" not in body and not re.search(r"^\s*var Err", body, re.M))
    check("no errors.Is / errors.As anywhere in the collector client",
          "errors.Is(" not in body and "errors.As(" not in body)

    print("-- the caller cannot branch on what it is told --")
    check("the error is built from resp.Status (a STRING), so the numeric code is not preserved",
          "resp.Status)" in body and "resp.StatusCode)" not in body.replace(
              "resp.StatusCode < 200 || resp.StatusCode >= 300", ""))

    print("-- the reason central wrote is discarded --")
    # the error path must not read the body between the status check and the return
    sa = body.split("func (c *Client) SubmitActions(")[1].split("\n}\n")[0]
    err_path = sa.split("resp.StatusCode < 200")[1]
    check("the non-2xx path never reads resp.Body before returning",
          "io.ReadAll" not in err_path and "Decode(" not in err_path and "Read(" not in err_path)

    print("-- and there is no retry machinery downstream either --")
    allc = "\n".join(p.read_text() for p in (root / "internal/collector").glob("*.go")
                     if not p.name.endswith("_test.go"))
    allc = "\n".join(l for l in allc.splitlines() if not l.strip().startswith("//"))
    check("no backoff/retry/sleep loop exists in the collector package",
          not re.search(r"(backoff|ExponentialBackoff|time\.Sleep\()", allc))

    print("-- the comment promises what the type cannot deliver --")
    check('client.go still says a non-2xx is "an error so the caller can log or retry"',
          "the caller can log or retry" in client)

    if fails:
        print(f"\nOPAQUE UPSTREAM RED: {fails}")
        return 1

    print("\n=> The collector cannot tell 'central refused this because of what is in it' from")
    print("   'central was briefly unreachable' from 'my token is wrong'. Every non-2xx becomes")
    print("   `central returned <status string>` -- and the response body, which is where central")
    print("   explains the rejection, is closed unread.")
    print("   This is the keystone for beats 660/662/671: each of those proposed 'retry transport,")
    print("   quarantine content' and none of them can be implemented until the error carries a")
    print("   kind. Drain's stop-on-first-failure is not timidity -- it is the only correct")
    print("   behaviour available when the error's type is `*errors.errorString`.")
    print("   Fix, Dad's call: (1) return a typed error, e.g. UpstreamError{Status int, Body []byte},")
    print("   so callers can errors.As/Is; (2) read a bounded slice of the body on non-2xx so the")
    print("   reason survives; (3) honour Retry-After when present. None of these change the wire.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

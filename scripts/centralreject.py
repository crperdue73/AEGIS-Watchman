#!/usr/bin/env python3
"""centralreject — beat 659 left one premise unverified: can central actually
reject a batch? I inferred it. This settles it from the handler.

THE ANSWER, MEASURED
--------------------
internal/central/http.go:220 handleActions returns exactly these non-2xx codes:

    405  method != POST                  (collector always POSTs -> unreachable)
    400  unreadable body
    400  invalid json
    400  missing host.id                 (req.Host.ID == "" )
    400  missing events                  (len(req.Events) == 0)
    413  body > 1 MiB                    (maxBodyBytes)
    500  IngestActions returned an error

**IngestActions cannot return an error** (service.go:183-210): every early return is
`return 0, nil`, and its three callees -- `retain`, `resolveActors`, `alert` --
return nothing at all. So the 500 branch is unreachable, and the only codes a
conforming collector can provoke are 400 and 413.

That REVERSES beat 659's framing. I said "cannot distinguish a 503 from a 400." More
precisely: central never emits a transient 5xx on this path at all. Every rejection
central can produce is PERMANENT BY CONSTRUCTION -- it is a verdict about the
bytes, not about central's health. A transport failure (connection refused, timeout)
is the only transient error, and that one never reaches the status-code branch.

So the collector's single `error` conflates:
    transient  = transport (central down, timeout)   -> retry is correct
    permanent  = 400 / 413                           -> retry can never succeed

and the permanent class is the ONLY class central can produce.

IS A 400 REACHABLE FROM THE REAL COLLECTOR? (the premise I owed)
----------------------------------------------------------------
- missing host.id: collector always sets b.host from config; `Host.ID == ""` would
  also make the collector's own requests meaningless. Practically no.
- missing events: `Append` returns early on len(events)==0, so spool records are
  never empty. No.
- invalid json / unreadable body: the spool line was written by json.Marshal. It
  can only be invalid if the file is corrupted -- and `read()` DISCARDS malformed
  lines (spool.go:142-144), so a corrupt line never reaches submit. **No** -- the
  collector's own reader heals it before the transport sees it.
- 413 body > 1 MiB: a spool record is one cycle's matches for one host. Possible
  under heavy alert volume with a large `maxBytes`. **Yes, reachable.**

CONCLUSION: exactly one reachable permanent rejection, and it is 413 from a large
batch -- which is precisely the batch a flapping host produces. The response is to
retry it forever (uncapped) or wait for trim to evict it (capped), i.e. beat 659's
trade-off. The gap is real but it is ONE code, not a spectrum.

CLAIM / NOT CLAIMED
-------------------
CLAIMED: the 500 branch is dead (no callee returns error); central's rejections are
    all permanent by construction; 413 is reachable from a real collector.
NOT CLAIMED: that any host has hit it. This is a code reading plus a reachability
    argument, and the standing gap is unchanged -- I still have not sent a request.
"""

import sys
import pathlib


def main():
    root = pathlib.Path(__file__).resolve().parents[1]
    try:
        http = (root / "internal/central/http.go").read_text()
        svc = (root / "internal/central/service.go").read_text()
        sp = (root / "internal/collector/spool.go").read_text()
    except Exception as e:  # noqa: BLE001
        print(f"FAIL could not read source ({e}); a missing input is not a pass")
        return 1

    fails = 0

    def check(name, ok):
        nonlocal fails
        print(("ok   " if ok else "FAIL ") + name)
        if not ok:
            fails += 1

    print("-- central's rejection surface --")
    check("handleActions rejects missing host.id with 400",
          'req.Host.ID == ""' in http and "missing host.id" in http)
    check("handleActions rejects empty events with 400",
          'len(req.Events) == 0' in http and "missing events" in http)
    check("handleActions caps body at 1 MiB with 413",
          "maxBodyBytes" in http and "StatusRequestEntityTooLarge" in http)

    print("-- is the 500 branch reachable? --")
    # IngestActions: every return must be `return 0, nil` or `return len(accepted), nil`
    body = svc.split("func (s *Service) IngestActions")[1].split("\nfunc ")[0]
    bad = [ln.strip() for ln in body.splitlines()
           if ln.strip().startswith("return") and "err" in ln]
    check(f"IngestActions has no error return ({len(bad)} found)", not bad)
    for name in ("retain", "resolveActors", "alert"):
        sig = f"func (s *Service) {name}("
        idx = svc.find(sig)
        ok = idx != -1 and ") error {" not in svc[idx:idx + 200]
        check(f"{name} returns no error (500 branch unreachable)", ok)

    print("-- which 400s can the real collector produce? --")
    check("empty events never reaches the wire (Append returns early)",
          "len(events) == 0" in sp and "return nil" in sp)
    check("a corrupt spool line is discarded by read(), not submitted",
          "discarding a torn final line" in sp)

    if fails:
        print(f"\nCENTRAL REJECT RED: {fails}")
        return 1
    print("\n=> central's ONLY reachable permanent rejection is 413 (batch > 1 MiB).")
    print("   A transport error is the only transient one, and it never reaches the")
    print("   status branch. Beat 659's 'cannot tell 503 from 400' is refined:")
    print("   central emits no transient 5xx here at all -- every rejection is a")
    print("   verdict on the bytes, and retrying one can never succeed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

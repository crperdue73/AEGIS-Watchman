# FINDING 2026-10-26c — 30 of 32 endpoint globs can never fire against a real auditd record

**Rules:** `carbonato.exfil.egress_after_change` (high), `carbonato.exfil.egress_after_identity_change` (critical), `carbonato.c2.blockchain_rpc` (medium)
**Status:** OPEN — pack owner's call. No rule changed.
**Found:** beat 599
**Instrument:** `scripts/endpointfixture.py` (asserts the reachable set), with `scripts/endpointglob_test.py` and `scripts/eventcheck.py` covering the same globs. Re-runnable: `python3 scripts/endpointfixture.py`.

## The chain

1. **`internal/telemetry/egress.go:150`** decodes the connect record's SOCKADDR into `"host:port"`. For
   auditd-driven egress the host is an **IP**. It is never a hostname.
2. **`egress.go:158-161`** — *"If we can name the service, add it as a second endpoint candidate so rules
   authored against `*slack.com*` fire without DNS."* The only way a domain ever reaches the matcher is this
   reverse map.
3. **`egress.go:276-279`** — the entire reverse map:

```go
var knownServices = []knownService{
	{name: "slack.com",          cidrs: { 3.120.129.226/32 }},
	{name: "api.telegram.org",   cidrs: { 149.154.167.0/24 }},
}
```

Two entries. One of them a **single /32**.

**Therefore:** a domain glob fires only if the peer IP is literally one of those two ranges. Everything else
sees `"1.2.3.4:443"` and the glob is inert.

## Measured

Of the **32** `endpoint_glob_any` values across the three rules, the globs that can receive a name at all:

| glob | rule |
|---|---|
| `*slack.com*` | `exfil.egress_after_change`, `exfil.egress_after_identity_change` |
| `*api.telegram.org*` | `exfil.egress_after_change` |

**30 of 32 globs have no service-name path and no hostname path.** They cannot fire on any endpoint the
collector produces. This includes all eleven `c2.blockchain_rpc` globs, all six non-Telegram/non-Slack exfil
globs, and — notably — `*hooks.slack.com*`, which can never match because the map yields `"slack.com"` and
`matchOne` would need the literal string `hooks.slack.com` inside the joined endpoint.

**Caveat, stated plainly:** the rules are not *unreachable code*. If an endpoint ever arrives as a hostname —
a different collector, a proxied egress path, a test fixture — the globs work. This finding is about the
**shipped collector**, which is where the alert has to come from.

**Be honest about certainty:** I cannot run the Go tests here (no toolchain). The claim rests on reading
`egress.go` at the cited lines and measuring `matchOne` against the shapes the code produces. The reverse map
is 4 lines long and I read all of it.

## Why this is the worst kind of finding

It has the shape this project has spent a week hunting: **a rule that looks armed and is not.** The pack
reads as a broad exfil detection surface — 32 endpoints, blockchain RPC coverage, paste sites, transfer.sh.
The instrument reports on all of it. What fires is two brands on three IP ranges.

It also explains something from earlier beats: the `evil-slack.com.evil` *containment* false positive
(beat 590) is real but academic for the same reason — you have to already be on slack.com's single /32 for
the question to arise.

## What the owner's options look like (none applied)

- **Extend `knownServices`** toward the pack. The comment says *"minimum viable set; a production deployment
  would load from a feed"* — so the design anticipates this. But Slack alone publishes dozens of ranges, and
  a wrong range is a wrong name (the comment's own warning).
- **Loosen the pack to what the collector can actually see** — i.e. accept that domain rules need a DNS-side
  signal that does not exist yet, and stop presenting 30 globs as coverage.
- **Add a hostname source** (DNS query telemetry, eBPF, or proxy logs) so the domain rules get a real
  subject. This is real work and probably the correct long-term answer.

Recommendation: **(c) is the fix, (a) is a stopgap, (b) is honesty.** What must not happen is the current
state being reported as endpoint coverage.

## Ledger impact

The ledger says **47 LIVE / 78 REACH / 0 unread**. This finding does not change the match-key accounting —
the `endpoint_glob_any` key *is* read and *does* fire. What it changes is the meaning of "REACH": for 30
values, reach is **structural, not incidental**. That distinction belongs in the ledger and does not
currently exist there. Flagged, not silently renumbered.


## Correction (beat 600)

Beat 599 published this as **29 of 32**. Re-measuring the same question against the two endpoint shapes the
collector emits gives **30 of 32**. The error: `*slack.com*` was counted as reachable in
`egress_after_identity_change` on the strength of the brand, but reachability is a property of the
**emitted string**, not the brand — and only `egress_after_change` was checked. The correct reachable set is
**two**: `*slack.com*` in `egress_after_change` and `*slack.com*` in `egress_after_identity_change` both fire
on `"slack.com 3.120.129.226:443"`; `*api.telegram.org*` fires on the Telegram shape. Everything else is dead.

The count moved in the direction that makes the finding worse, which is the direction an error usually moves
when you re-derive it instead of re-reading it.


## Beat 607 — a comment that describes a restriction the code does not implement

Checking whether the CIDR path could rescue any of the 30, I read `substringNeedle` (`engine.go:287`) and its
doc comment on the call site (`engine.go:269`):

> Contains fallback for globs shaped like `"*needle*"` or `"prefix*"` that name a domain/substring rather
> than a path: `'*'`/`'?'` do not cross `/`, so `"*api.telegram.org*"` would miss `"api.telegram.org/bot123/send"`
> without this. **Only applied to globs with no interior `/` beyond the wildcard.**

**The bolded restriction is not in the code.** `substringNeedle` rejects only `*`, `?`, `[`. A `/` passes
straight through:

```
*/api/v2/events*     -> needle "/api/v2/events"      has_slash=True, fallback applied
*etherscan.io/api*   -> needle "etherscan.io/api"    has_slash=True, fallback applied
*slack.com*          -> needle "slack.com"           fallback applied (as documented)
```

So two of the pack's endpoint globs behave as **contains tests**, not path globs. `*/api/v2/events*` matches any
endpoint string containing `/api/v2/events` regardless of position.

**Measured, and it is not harmful here.** Egress strings are `"host:port"` or `"service host:port"`
(`egress.go:150-160`) — no path component ever appears, so neither glob can match anything the collector
emits. The globs were authored for URLs that `egress.go` does not produce; that is 26c's finding, not this one.
The divergence makes them match *more* than the comment intends, in a place where they match nothing either
way.

**Recorded as a documentation defect, not a detection defect.** The risk is future: someone tightens
`substringNeedle` to match its comment, or someone authors a slash-shaped glob and assumes it behaves like
`path.Match`. Either way the code and the comment must not disagree.

**Not changed.** Fixing the comment to describe the code is one line; changing the code to match the comment
would silently narrow matching for any glob containing a slash — a rule-semantics change. Owner's call; the
comment fix is the safe half if it is wanted.

**The absolute claim in the body holds.** With the CIDR path checked, only `slack.com` and `api.telegram.org`
can ever receive a service name (`egress.go:276-279`, two entries, one a single `/32`). No other glob can match
an emitted endpoint. 30 of 32 stands.

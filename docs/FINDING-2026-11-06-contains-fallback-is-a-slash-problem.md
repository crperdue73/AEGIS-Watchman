# FINDING 2026-11-06 — the contains fallback is not a path problem or an endpoint problem. It is a SLASH problem.

**Status:** OPEN — recommendation to the pack owner (Dad's call). No engine change made. Instrument
built, pinned, and wired into the gate.

**Instrument:** `scripts/needlescope.py` (new, beat 831), pinned by `scripts/needlescope_test.py`
(6 pins, mutation-proven: a fallback-free arm replaced with the real one moves 3 pins, rc=1; control rc=0).

Beat 634 said: `substringNeedle` + `strings.Contains` over-matches paths.
Beat 641 said: five more over-matches, all the same root.
Beat 644 said: retract the "refuse a `/`" fix — endpoints *depend* on the fallback, so the change
must be per-family at the call site.

All three are right about what they measured, and all three were reasoning from the family they
happened to be looking at. This note asks the question beat 644 left open — *which* families — and
the answer is not the one the per-family framing predicted.

## What was measured

`scripts/needlescope.py` runs the pack's globs through **two arms that differ in exactly one thing**:
`packcheck.match_one` (whole → suffix → basename → contains) versus the same function with the
contains block deleted. Same port, same suffixes, same basename route. A difference between the arms
is therefore attributable to the fallback and nothing else.

Ported verdicts, per field family (`matchOne` serves all four):

| family | globs | globs whose contains route is reachable | rules that need it on realistic values |
|---|---|---|---|
| `path_glob_any` | 30 | 28 | 0 |
| `endpoint_glob_any` | 32 | 32 | 6 |
| `detail_glob_any:binds` | 2 | 1 | 1 |
| `argv_any` | 39 | — | 0 |

Two numbers, and they are not the same number. *Reachability* says 28 of 30 path globs can be made
to fire through contains — but only with a **constructed** witness (`/x/y/etc/crontab/z`), a value
built to defeat whole, suffix and basename at once. *Need on realistic values* — the existing
`pathfixture` / `endpointfixture` rows, written before this question existed — is what actually
decides policy, and it says: endpoints need it, paths never do.

## The thing I got wrong by calling it "per-family"

The per-family framing implies four different answers. There are **two**, and the dividing line is
not the field. It is whether the needle contains a slash.

- Slash-free needles (`*id_rsa*`, `*slack.com*`, `*credentials*`, `*.env`): the fallback is what
  makes them work at all against a full path or URL, because `*` cannot cross `/`. That is
  load-bearing, and it is also exactly the looseness that fires on `credentials_help.html`,
  `slack.com.evil.tld`, `/x/y/credentials/z`.
- Slash-bearing needles (`*/etc/crontab`, `*/.bashrc`, `*/.netrc*`, `*/.ssh/*`, `*/etc/cron.d/*`):
  the glob already carries a segment boundary. Substring matching throws that boundary away, and
  **every** measured false positive on paths is this shape — `.bashrc.bak`, `.netrcfile/`,
  `crontab.local`, `authorized_keys.bak`, `/x/sub-/.ssh/-dir/z`. The contains route buys these globs
  nothing they don't already have and costs them every wrong answer they give.

So the correct unit is *needle shape*, and the correct rule is narrower than "per-family":

> contains is sound only for a slash-free needle. A needle containing `/` is a segment-aware glob;
> substring matching cannot honour a segment boundary.

That formulation covers all four families with one clause, and it explains both prior findings
without needing a per-family table. It also predicts the endpoint lookalike problem as
**unavoidable, not fixable** — `*slack.com*` has no slash, so under this rule it still fires on
`slack.com.evil.tld`. That is the beat-644 "host-aware matcher or documented tolerance" question,
and this measurement makes it concrete: ten endpoint globs cannot be tightened by glob surgery at
all, because the loosest thing about them is the fallback they need to function.

## What I did NOT do

I did not change `engine.go`. The rule above is a recommendation to Dad, backed by an instrument, and
the fixture files already declare the affected rows as XFAIL (`engine:` prefix) so the day the change
lands, those rows fail loudly and the change has to be acknowledged rather than absorbed.

I also did not move any pin to make a suite green. `needlescope_test.py` pins all six numbers; there
is no pin here that was chosen after seeing a run except the six themselves, which is what a pin *is*.

## Limits, stated

- Every verdict is from the Python port, not the Go engine. `go_path_match` is a mirror with its own
  known divergence on bracket classes (beat 642/643); the pack uses no bracket globs, so it does not
  bite here, and the script says so if one ever appears.
- Reachability uses constructed witnesses, deliberately. They prove a route exists; they do not claim
  it is likely. The realistic column is the one to make policy from.
- `argv_any` shows zero dependence because argv candidates carry spaces, not slashes — a different
  shape entirely and not swept here.

Instrument: `scripts/needlescope.py` (exit 0, printing). Pins: `scripts/needlescope_test.py` (wired
into `suitecheck.sh` SELFHARNESS). Gate: 140 suites / 2221 checks / 0 failures.

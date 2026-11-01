# FINDING — 2026-11-01g: a host is named by a body field, not by its certificate

**Status:** measured from source. **Gap stands. NOT APPLIED** — the fix changes what a collector operator
must provision, so it is Dad's call.
**Instrument:** `scripts/hostclaim.py`.
**Scope in one line:** the fleet is **authenticated as somebody, attributed as anybody.**

## What holds (the control, and it is real)

`cmd/central/main.go:106` gives the ingest listener `cfg.TLS.ServerConfig()`, and
`internal/mtls/mtls.go:124` sets `ClientAuth: tls.RequireAndVerifyClientCert`. An unauthenticated party is
rejected **at the handshake, before a body byte is read**. That is a genuine control and I am not going to
argue it down to make this finding look bigger.

## What it does not establish

Who the client *is*. `tls.RequireAndVerifyClientCert` with a shared fleet CA proves exactly:

> "this peer holds a certificate signed by our CA"

and nothing about which of the fleet's hosts it is. Then `handlers` decode the request and
`service.go:118` does `key := AgentKey{HostID: host.ID, Root: inv.Root}` — where `host` is `req.Host`, **the
JSON body field**.

A grep across `internal/central/**` and `cmd/**` for `PeerCertificates|r.TLS|TLS.PeerCerts|FromContext(`
(non-test, excluding the `mtls` package that *builds* the config) returns **nothing**. There is no cert→host
binding to bypass, because there is no cert→host binding.

So the chain is:

```
client cert      ->  "some enrolled party"            (verified)
body.host.ID     ->  "which host this is"             (unauthenticated)
baseline key     ->  (body.host.ID, inv.Root)         (attribution is now a claim)
```

## Two consequences, deliberately kept apart

**1. False attribution — real, high, and needs no malice.**
`RecordFromEvent` copies `host.ID`/`host.Name` into every `Record`, and `Alert.Host` is the same value. Every
record and every page the operator reads carries the *claimed* name. The product's output is attribution; if the
input to attribution is a caller-supplied string, **each alert is a claim, not a finding.** The realistic trigger
is not an attacker — it is a collector agent deployed from a config file copied off another host. The result is a
plausible, wrong incident story, and a responder chasing the wrong machine.

**2. Baseline clobber — real, bounded by the same premise.**
Host A's inventory is stored under B's key. Two shapes, and the second is the bad one:
- B's next real change diffs against A's tree → a storm of false drift (noisy, visible, recoverable).
- **B's change happens to match A's tree → the drift is empty and the change is silently swallowed.** A false
  negative, on the detection path, caused by a misconfiguration rather than an attack.

## What I am NOT claiming

- **Not** "unauthenticated ingest." An unauthenticated caller cannot reach the handler. Say the scope out loud
  every time this comes up, because the dramatic version of this sentence is false.
- **Not** that this is exploitable in a correctly-provisioned single-cert-per-host fleet. In that fleet, the
  binding is *convention*, and convention is not enforced by the code.
- **Not** a Go measurement. Source reads only; no toolchain on this host (beat 642).

## Where the bug actually lives

Not in `mtls`. Not in the handlers. In the **absence** of the one line that would join them — and the reason it
is easy to miss is that both halves read as correct in isolation:

- `mtls.go` says "an unauthenticated collector is rejected at the handshake" — **true**.
- `service.go` says the key is `(host.ID, root)` — **true**.
- Nobody wrote "…and `host.ID` is whatever the authenticated peer says it is."

**This is beat 724's shape again, one level up from a comment: two correct statements, and the defect lives in
the sentence between them that nobody wrote.** A reviewer reading either file agrees with it.

## Shape of the fix (NOT applied)

Either is small; both are product decisions:

- **Cert-carried identity.** Subject CN or a SAN URI of `host:<id>`, checked against `req.Host.ID` at the top of
  the ingest handlers. Fails closed, no new config surface for the fleet, but it constrains how certs are issued
  and **breaks every deployment that shares one cert across a box.**
- **Enrolment map.** Cert fingerprint (or SAN) → host id, consulted before `Ingest`, config-provided. More
  flexible, more to misconfigure, and it introduces a second source of truth that has to be kept in sync.

A third option — reject a `req.Host.ID` that disagrees with the baseline's existing HostName — is worse than
either: it trusts the *first* claimant and only catches the second, which turns a misconfiguration into a lockout.

**Which one is right depends on how Dad expects fleets to provision certs, which I do not know and should not
guess on a heartbeat.**

## Why the script asserts the gap instead of asserting a fix

`hostclaim.py` asserts the *current* state — mTLS on, attribution from the body, no peer read — so that **if
someone adds the binding, the script goes RED and this finding gets re-derived instead of standing as a lie about
code that moved.** That is the failure I keep catching in this tree (723, 725: a document describing a tree that
changed). A finding about a *missing* line has to be pinned to the missing line, or it quietly describes history.

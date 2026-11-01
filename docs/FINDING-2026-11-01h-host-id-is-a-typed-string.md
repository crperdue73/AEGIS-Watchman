# FINDING — 2026-11-01h: `host.ID` is a typed string, and beat 726's fix does not survive that

**Status:** measured from source. **Gap stands. NOT APPLIED.**
**Instrument:** `scripts/hostidshape.py`.
**Supersedes part of:** `FINDING-2026-11-01g` — not its measurement, its *remedy*.

## The sentence beat 726 assumed and did not check

Beat 726 said: *a cert for host A can post under host B's id.* That is measured and true.

But "post under host B's id" quietly assumes **host B's id is a thing a fleet can pin.** I followed the id
back to where it is produced, and it isn't:

```go
// cmd/collector/main.go
29:  hostID := flag.String("host-id", "", "stable host id (defaults to hostname)")
176: func resolveHost(idOverride, nameOverride string) schema.Host {
177:     hn, _ := os.Hostname()
178:     id := idOverride
179:     if id == "" { id = hn }        // <-- the "identity"
186:     return schema.Host{ID: id, Name: name}
```

There is no machine identity in it. `host.ID` is **an operator-chosen string that defaults to a hostname.**
The word "stable" in the flag's own help text is a hope, not a property.

## Four consequences, measured

**(a) Two boxes, one host.** Any two machines sharing a hostname — same image, same default, a fleet provisioned
from one template — are **one host** to central. They write the same baseline key `(host.ID, inv.Root)` and each
one's inventory is diffed against the other's. That is beat 726's clobber, reachable with **zero** misconfigured
certs, just twenty identical containers.

**(b) A rename is a silent full reset.** `Ingest` on a key it has never seen takes the `!found` branch
(`service.go:110`), sets `events = []schema.Event{}`, and returns `FirstSeen: true`. So after a hostname change,
the first ingest **reports no changes** — correctly, because there is no baseline — and every change made before
the rename becomes uncomparable. The log line reads like routine enrolment. **One branch carries two meanings
(enrolment / rename) and the operator cannot tell them apart without reading the response flag.**

**(c) Nothing derives an identity.** A sweep of every non-test `.go` file in `internal/` and `cmd/` for
`/etc/machine-id`, DMI `product_uuid`, or `sha256.Sum256(...host...)` returns **nothing**. There is no fallback,
no derivation, no persisted machine identity anywhere in the tree.

**(d) `Name` is independent of `ID`.** Both are separate flags defaulting to the same hostname, so the pair can
disagree — and `Name` is cosmetic while `ID` is the key. A fleet that sets `-host-name` and forgets `-host-id`
gets a display name it likes and a key that is still whatever the box happened to be called.

## Why this is a separate finding: beat 726's fix does not survive (b)

Beat 726 proposed binding a cert to the declared id (`host:<id>` SAN, or fingerprint → host enrolment). Now
combine them:

> A cert proves "this peer is enrolled as `web-01`." The peer then says `host.ID = "web-01"`. Central checks
> them, they match, and it stores the baseline under `("web-01", /root)`.
>
> **`web-01` is still a string the box typed.** Bind a cert to it and you have authenticated *a name*, not *a
> machine*. Two boxes that both believe they are `web-01` both hold valid certs for `web-01`, both pass the
> check, and both write the same key.

726 makes the claim *who the peer says it is* trustworthy. It does not make the name **refer to one machine**.
Those are different problems, and the second one is upstream of the first — no cert binding can create a
distinction that the identifier itself doesn't carry.

## The shape that would work (NOT applied — Dad's call)

The identifier has to come from something the **machine derives and cannot trivially share**:
`/etc/machine-id`, DMI `product_uuid`, or a per-host key generated on enrolment and persisted. Then 726's cert
binding has something real to pin, and (a)/(b) become *detectable*: two hosts with one id, or one host whose id
changed, are **reported anomalies** instead of being absorbed into whatever the config says.

Every option there is a fleet-provisioning decision — cloned images share `/etc/machine-id` too, so "derive" is
not automatically "unique". **I am not choosing one on a heartbeat, and I am not writing code that pretends the
choice is obvious.**

## What I am NOT claiming

- **Not** that renaming a host is common. It is rare. That is why (b) has survived: a silent reset on a rare event
  looks like nothing at all.
- **Not** that cloned-image collision is an attack. It is an operator hazard, and it is the *likely* one.
- **Not** a Go measurement. Source reads; no toolchain here (beat 642).
- **Not** that these four are the whole list. They are what a sweep of the id's production and consumption sites
  surfaced. The script pins them; it does not claim to enumerate the space.

## Why the script asserts the gap

Same reason as 726: `check_no_derived_identity` **fails if someone adds a derived identity.** That is the day this
finding becomes history, and I want the red to force a rewrite rather than a stale document quietly disagreeing
with the tree. The finding about a *missing* thing is the one most likely to rot unnoticed.

## The shape, said once

Beat 724: two correct statements, defect in the sentence nobody wrote.
Beat 726: same, one level up — mTLS says *who is asking*, the body says *which host*, and nothing joins them.
**Beat 727: the same move again — 726's remedy assumed the *name* was a *fact*.** Three beats, one habit: the
defect keeps living in an unexamined word.

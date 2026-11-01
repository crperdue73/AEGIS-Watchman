#!/usr/bin/env python3
"""hostclaim — *who* a host is, is decided by the same string it is *named* by.

THE OBSERVATION
---------------
`cmd/central/main.go:106` gives the ingest listener `tls.RequireAndVerifyClientCert`.
That is a real control and it is worth saying plainly: **an unauthenticated party
cannot reach a handler.** Any cert signed by the configured CA is accepted — which is
correct, it is what a fleet CA is for.

But then `Ingest` (central/service.go:118) does this:

    key := AgentKey{HostID: host.ID, Root: inv.Root}

and `host` is `req.Host` — **the JSON body field**, straight from the request. Nothing
in the tree reads `r.TLS.PeerCertificates` or marks the request context with a peer
identity; the grep for `PeerCertificates|VerifyPeer|ClientCAs` across
`internal/central/` returns nothing but the `mtls` package itself constructing the
config.

So the chain is:
    cert          -> proves "some enrolled party"
    body.host.ID  -> names WHICH host, and is unauthenticated
    baseline key  -> (body.host.ID, inv.Root)

**A cert for host A can post an inventory under host B's id and overwrite B's
baseline.** The records it produces, and every alert derived from them, carry B's name.

WHY THIS MATTERS MORE THAN IT LOOKS
-----------------------------------
Two different consequences, with two different severities, and I am not going to let
one inflate the other:

1. **FALSE ATTRIBUTION (real, high).** An operator reads "host B's identity file
   changed" when host A changed it. The tool's whole output is attribution; if
   attribution is a caller-supplied string, every alert is a *claim*, not a finding.
   This is severity-agnostic — it needs no malice, only a misconfigured agent reusing
   another's config file, and it produces a plausible, wrong story.

2. **BASELINE CLOBBER (real, but bounded by premise).** A's inventory replaces B's
   baseline, so the next genuine change on B diffs against A's tree — a storm of false
   drift, or worse: B's real change *matches* A's inventory and is silently swallowed
   (a false negative, the one that matters).

**The premise I am NOT papering over:** both consequences require a party holding a
CA-signed cert that is presenting someone else's id. That is the trust boundary's job
to keep small, and mTLS already shrank it a lot. This is not "unauthenticated ingest".
It is **"authenticated as somebody, attributed as anybody."**

WHAT A FIX WOULD LOOK LIKE (NOT APPLIED — Dad's call)
-----------------------------------------------------
The shape is small: name the identity at the handshake, then bind. A cert carrying a
subject/CN (or SAN URI) of `host:<id>` checked against `req.Host.ID` at the top of the
handlers would close it; so would a per-agent enrolment map (cert fingerprint -> host
id) consulted before `Ingest`. Either is a **product decision** — it changes what a
collector operator must provision, and it can break existing deployments that share
one cert across a box. Not mine to make on a heartbeat.

WHAT THIS SCRIPT DOES
---------------------
It does not simulate a fix. It pins the *shape* so the gap cannot close silently in one
direction or open in another without somebody noticing:

  A. the listener still requires a verified client cert          (control must hold)
  B. attribution still comes from the body, not the peer cert    (the gap)
  C. no peer-identity binding exists anywhere on the ingest path (the gap)
  D. every Record/alerts attribution field is derived from the body host

B and C are *assertions of the current state*: if someone adds the binding, this
script goes RED and the finding gets re-derived rather than left standing as a lie
about code that changed. That is deliberate — the failure mode I keep hitting (beat
723/725) is a document describing a tree that moved.

NO CLAIM: that a Go build behaves this way. Simulated from source reads; no toolchain
on this host (beat 642).
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

FAILS = []
OKS = 0


def ok(m):
    global OKS
    OKS += 1
    print("ok   " + m)


def fail(m):
    FAILS.append(m)
    print("FAIL " + m)


def read(rel):
    return (ROOT / rel).read_text(errors="replace")


def non_test_go(subre):
    return [p for p in sorted(ROOT.glob(subre)) if not p.name.endswith("_test.go")]


# --- A. the control: the listener really does demand a client cert -----------
def check_mtls_control():
    src = read("cmd/central/main.go")
    if re.search(r"cfg\.TLS\.ServerConfig\(\)", src) and \
       re.search(r"ListenAndServeTLS", src):
        ok("A control: ingest listener uses ServerConfig() + ListenAndServeTLS")
    else:
        fail("A control: the TLS listener wiring changed — this finding's premise "
             "('authenticated as somebody') may no longer hold; re-derive")
    m = read("internal/mtls/mtls.go")
    if re.search(r"ClientAuth:\s*tls\.RequireAndVerifyClientCert", m):
        ok("A control: ServerConfig requires and verifies the client certificate")
    else:
        fail("A control: ClientAuth is no longer RequireAndVerifyClientCert")


# --- B. attribution is the JSON body ----------------------------------------
def check_attribution_source():
    src = read("internal/central/service.go")
    m = re.search(r"func \(s \*Service\) Ingest\(([^)]*)\)", src)
    if not m:
        fail("B: Ingest signature not found")
        return
    params = m.group(1)
    if "host schema.Host" in params:
        ok("B gap: Ingest takes the Host as a value parameter — its provenance is "
           "the caller's, not a verified identity")
    else:
        fail("B: Ingest no longer takes host as a parameter — re-derive this finding")
    m2 = re.search(r"key := AgentKey\{HostID:\s*host\.ID", src)
    if m2:
        ok("B gap: the baseline key's HostID comes from that body-supplied host")
    else:
        fail("B: the AgentKey construction changed — re-derive")
    # Where does the handler get req.Host from? The decoded body.
    h = read("internal/central/http.go")
    if re.search(r"json\.Unmarshal\(body,\s*&req\)", h) and \
       re.search(r"s\.Ingest(?:Actions)?\(ctx,\s*req\.Host", h):
        ok("B gap: the handler passes req.Host (decoded from the JSON body) straight in")
    else:
        fail("B: handler wiring changed — re-derive")


# --- C. no peer-cert binding anywhere on the ingest path --------------------
def check_no_peer_binding():
    hits = []
    for p in non_test_go("internal/**/*.go") + non_test_go("cmd/**/*.go"):
        if p.name == "mtls.go":
            continue  # constructs the config; not a binding
        s = p.read_text(errors="replace")
        if re.search(r"PeerCertificates|r\.TLS\b|TLS\.PeerCerts|FromContext\(", s):
            hits.append(str(p.relative_to(ROOT)))
    if hits:
        fail("C: peer-identity reads appeared in %s — IF this is a real binding, the gap "
             "is CLOSED and this finding must be re-derived (not reopened)" % ", ".join(hits))
    else:
        ok("C gap: nothing on the ingest path reads the peer certificate; there is no "
           "cert->host binding to bypass")


# --- D. everything the operator reads is built from that body host ----------
ATTR_FIELDS = [
    ("Record.HostID", "internal/central/eventstore.go"),
    ("Record.HostName", "internal/central/eventstore.go"),
    ("Alert.Host", "internal/central/alert.go"),
]


def check_derived_attribution():
    src = read("internal/central/eventstore.go")
    body = src[src.find("func RecordFromEvent("):]
    body = body[:body.find("\n}\n")]
    if re.search(r"HostID:\s*host\.ID", body) and re.search(r"HostName:\s*host\.Name", body):
        ok("D: Record.HostID/HostName are copied from the caller-supplied host")
    else:
        fail("D: RecordFromEvent no longer copies host fields — re-derive")
    # Alert.Host is CONSTRUCTED in service.go:170, not in alert.go (where only the
    # type lives). Beat 726 pinned this check to the wrong file first and got a red
    # that named the wiring when the fault was my pointer — so the assertion now
    # covers both the construction site and the type, separately, and says which.
    svc = read("internal/central/service.go")
    if re.search(r"a := Alert\{[^}]*Host:\s*host\b", svc):
        ok("D: Alert.Host is built in service.go from the same caller-supplied host, "
           "so every page inherits the claim")
    else:
        fail("D: the Alert{Host: host} construction in service.go changed — re-derive")
    typ = read("internal/central/alert.go")
    if re.search(r"Host\s+schema\.Host", typ):
        ok("D: Alert.Host is a schema.Host value (not a verified identity type) — "
           "the type cannot carry provenance even if someone wanted to check it here")
    else:
        fail("D: Alert.Host field type changed — if it is now a verified-identity type, "
             "the gap may have closed; re-derive")


# --- E. the boundary this finding is scoped to -------------------------------
def check_scope_honesty():
    """The doc must not read as 'unauthenticated ingest'. Assert the scope line exists."""
    doc = ROOT / "docs/FINDING-2026-11-01g-host-identity-is-a-body-field.md"
    if not doc.exists():
        fail("E: the finding doc is missing")
        return
    text = doc.read_text()
    for phrase, label in [
        ("authenticated as somebody, attributed as anybody", "the one-line scope"),
        ("NOT APPLIED", "the not-fixed marker"),
    ]:
        if phrase in text:
            ok("E: doc carries %s" % label)
        else:
            fail("E: doc lost %s (phrase %r) — the scope is the point" % (label, phrase))


def main():
    check_mtls_control()
    check_attribution_source()
    check_no_peer_binding()
    check_derived_attribution()
    check_scope_honesty()
    print()
    if FAILS:
        print("hostclaim: %d FAIL / %d ok" % (len(FAILS), OKS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("hostclaim: %d ok, 0 FAIL" % OKS)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""scopekey — the scope capability key keeps the port, so a CDN change looks like a new capability.

WHY THIS EXISTS (beat 722)
--------------------------
`capabilityOf` (internal/telemetry/scope.go:332) derives the novelty key for a `net.egress` action. Its comment says:

    // Prefer the named service (stable, DNS-free) over the raw IP:port, so
    // the capability key is "slack.com" rather than a rotating IP that would
    // look novel every time the CDN changes.
    ...
    // take the first token as the key so identity survives the IP suffix.
    return ScopeKindEndpoint, strings.Fields(v)[0]

The goal is stability across IP churn. The code CANNOT deliver it in the branch that matters:
  * `strings.Fields` splits on WHITESPACE, not on the port colon.
  * That branch is only reached when `detail["service"]` is ABSENT.
  * When `service` is absent, `endpoint` is the bare "host:port" built by decodeSockAddr (egress.go:155).
  * A bare "host:port" contains no whitespace, so Fields(v)[0] returns the WHOLE "host:port".

Result: every peer outside the short hardcoded `knownServices` list is keyed by `ip:port`, port included. The same host
on a new port is a new capability -> `carbonato.tool.scope_new` (severity: high) fires on `novel: true`.

WHAT THIS CHECKS
----------------
1. The egress branch still keys on `strings.Fields(v)[0]` (the construct under audit).
2. `detail["service"]` is still set only for peers matching `knownServices` (so the unnamed branch is the common case).
3. The comment still claims stability the code does not provide -- if the comment is corrected, or the key is changed
   to SplitHostPort, the finding is stale and this check says so.

Each clause is independently falsifiable. This is a DECLARED defect: it exits 0 while the shape is unchanged and
prints what it found, and exits 1 the moment the shape moves -- in either direction -- so the finding is re-derived
rather than quoted forever.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SCOPE = ROOT / "internal" / "telemetry" / "scope.go"
EGRESS = ROOT / "internal" / "telemetry" / "egress.go"


def read(p):
    if not p.exists():
        print(f"FAIL  missing {p}")
        sys.exit(1)
    return p.read_text()


def fn_body(src, sig):
    i = src.find(sig)
    if i < 0:
        return None
    j = src.find("\n}\n", i)
    return src[i : j + 3] if j > 0 else src[i:]


def key_uses_fields(scope):
    body = fn_body(scope, "func capabilityOf(")
    if body is None:
        return False, "capabilityOf() not found"
    if "strings.Fields(v)[0]" in body:
        return True, "egress key is `strings.Fields(v)[0]`, which cannot strip a port (no whitespace in host:port)"
    if "SplitHostPort" in body:
        return False, "capabilityOf() now uses SplitHostPort -- the key may be fixed; re-derive the finding"
    return False, "the egress key derivation changed shape -- re-derive before repeating the finding"


def endpoint_is_bare_hostport(egress):
    # The unnamed branch only gets the bare form if EgressAction sets endpoint to "host:port" and service separately.
    body = fn_body(egress, "func (r *AuditRecord) EgressAction(")
    if body is None:
        return False, "EgressAction() not found"
    bare = 'detail := map[string]any{"endpoint": ep}' in body
    svc = 'detail["service"] = svc' in body
    if bare and svc:
        return True, 'endpoint is the bare "host:port" and service is set only for known peers: the unnamed branch is common'
    return False, "EgressAction's detail shape changed -- re-derive"


def comment_overclaims(scope):
    # The claim lives above the egress branch. If someone corrects it, the doc half is stale.
    body = fn_body(scope, "func capabilityOf(")
    if body is None:
        return False, "capabilityOf() not found"
    claims = "identity survives the IP suffix" in body
    if claims:
        return True, 'the comment still claims identity "survives the IP suffix" -- the code does not do that'
    return False, "the comment no longer overclaims -- the doc half of the finding is stale"


def main():
    scope, egress = read(SCOPE), read(EGRESS)

    checks = [
        ("key cannot strip port", key_uses_fields(scope)),
        ("unnamed branch is common", endpoint_is_bare_hostport(egress)),
        ("comment overclaims", comment_overclaims(scope)),
    ]

    hold = True
    for name, (ok, evidence) in checks:
        print(f"  ok {name}: {evidence}" if ok else f"  changed {name}: {evidence}")
        hold = hold and ok

    # Show the actual keys this derivation produces, so the claim is demonstrated and not just asserted.
    print("\n  measured keys (transcribed from capabilityOf, egress branch):")
    for ep in ("93.184.216.34:443", "93.184.216.34:8443", "93.184.216.34:4444"):
        print(f"    endpoint {ep!r:26} -> key {re.split(chr(92)+'s+', ep.strip())[0]!r}")

    if hold:
        print("\n  DECLARED DEFECT (live, unchanged): scope capability key keeps the port for unnamed peers.")
        print("  See docs/FINDING-2026-11-01c-scope-capability-key-keeps-the-port.md. Re-derived each run, not muted.")
        print("  Fix is one line (SplitHostPort vs Fields) but changes what a HIGH-severity rule fires on: Dad's call.")
        return 0

    print("\n  The finding's shape changed. Re-derive it before repeating it -- do NOT keep quoting the old claim.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

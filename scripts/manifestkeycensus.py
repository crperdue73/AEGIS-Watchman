#!/usr/bin/env python3
"""manifestkeycensus — the approval key is a directory, not an identity.

BEAT 741. /api/v1/agents renders a trust column ("ok"/"modified"/"unsigned"/"unverified")
that is the fleet-hygiene answer to "is someone else's agent identity running on my box".
This instrument pins WHAT THAT COLUMN IS KEYED ON.

Checks:
  A. ForRoot keys on root alone (normalizeRoot(root)) — one manifest per directory
  B. nothing checks for a root collision at load or reload (silent overwrite)
  C. ProvenanceEvents looks up ForRoot(inv.Root) — the ROOT IS A WIRE FIELD
  D. buildAgentsView + rosterTrust derive Trust from that root lookup
  E. provenance.Verify does NOT consult AgentID — every decision ignores it
  F. AgentID IS carried and signed (so 'unused' would be the wrong claim; 'unconsulted' is)
  G. the harness is nil-safe where it looks unsafe (recorded so a cold reader isn't misled)
  H. the doc keeps the narrow claim and states the sound part
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FAILS, OKS = [], 0


def ok(m):
    global OKS
    OKS += 1
    print("ok   " + m)


def fail(m):
    global FAILS
    FAILS.append(m)
    print("FAIL " + m)


def read(rel):
    return (ROOT / rel).read_text(errors="replace")


def func_body(src, needle):
    i = src.find(needle)
    if i < 0:
        return ""
    j = src.find("\nfunc ", i + 1)
    return src[i:] if j < 0 else src[i:j]


def check_a_forroot_key():
    src = read("internal/central/manifest.go")
    b = func_body(src, "func (r *ManifestRegistry) ForRoot(")
    if not b:
        fail("A: ForRoot not found")
        return
    # Quote the SIGNATURE (beat 731: a line that must hold, not a word nearby). Beat 741's M1
    # re-keyed the lookup and this stayed green because it only quoted the call body — the
    # signature is the contract. One-parameter ForRoot IS the finding.
    sig = re.search(r"func \(r \*ManifestRegistry\) ForRoot\(([^)]*)\)", src)
    params = [p.strip() for p in sig.group(1).split(",") if p.strip()] if sig else []
    if params == ["root string"]:
        ok("A gap: ForRoot's signature takes ONE parameter, `root string` — there is no host "
           "or agent id in the key, by construction. Two agents sharing a root resolve to the "
           "same manifest and nothing at the type level can stop them.")
    else:
        fail("A: ForRoot now takes %r — the key may have widened to include identity; "
             "re-derive the finding before reusing it" % params)
    if re.search(r"r\.byRoot\[normalizeRoot\(root\)\]", b):
        ok("A gap: the body keys the map on normalizeRoot(root) alone, consistent with the "
           "signature")
    else:
        fail("A: ForRoot's map key changed — re-derive")
    st = func_body(read("internal/central/manifest.go"), "func (r *ManifestRegistry) Set(")
    if re.search(r"r\.byRoot\[normalizeRoot\(root\)\] = m", st):
        ok("A gap: Set overwrites by the same key — a second manifest for one root silently "
           "replaces the first, so approvals are attributed to whichever loaded last")
    else:
        fail("A: Set's key changed")


def check_b_no_collision_check():
    src = read("internal/central/manifest.go")
    reg = func_body(src, "func NewManifestRegistry(")
    rel = func_body(src, "func (r *ManifestRegistry) Reload(")
    hits = []
    for label, b in (("NewManifestRegistry", reg), ("Reload", rel)):
        if not b:
            fail("B: %s not found" % label)
            continue
        # Beat 741 M2: a DECLARED variable named `duplicateRoot` must not satisfy this —
        # beat 735's species (declared != enforced). Quote the COMPARISON that would reject:
        # byRoot must be read before it is written, i.e. an existing entry is compared to a
        # new one. `_, exists := r.byRoot[...]` / `if _, seen := r.byRoot[...]` / `errors.New`.
        # Beat 741, second pass: my first version of this required a literal \(%s\) verb, so a
        # real fix using \(%q\) would have slipped past — a check stricter than the code it
        # reads (beat 738's species). Accept any verb.
        cmp = re.search(r"if\s+[^\n]*,\s*(?:exists|seen|dup\w*)\s*:=\s*r?\.?byRoot\[", b)
        rtn = re.search(r"errors\.New\(\"[^\"]*(?:root|manifest)[^\"]*\"", b) or \
            re.search(r"fmt\.Errorf\(\"[^\"]*(?:root|manifest)[^\"]*%[qsdv]", b)
        if cmp and rtn:
            hits.append(label)
    if not hits:
        ok("B gap: neither NewManifestRegistry nor Reload checks for two manifests sharing one "
           "root — the collision the previous two checks make possible is never reported, in "
           "a registry whose own contract says a bad approval list is a fault to surface")
    else:
        fail("B: %r now checks for root collisions — re-derive (this may be the fix landing)"
             % hits)


def check_c_provenance_uses_wire_root():
    b = func_body(read("internal/central/manifest.go"), "func (s *Service) ProvenanceEvents(")
    if not b:
        fail("C: ProvenanceEvents not found")
        return
    if re.search(r"s\.manifests\.ForRoot\(inv\.Root\)", b):
        ok("C gap: Layer 2 detection looks up approval with inv.Root — the root the AGENT "
           "REPORTED. The key that decides whether a file is approved is a body field.")
    else:
        fail("C: ProvenanceEvents lookup changed — re-derive")
    if "ForRoot(agent.Root)" in b:
        fail("C: it now uses agent.Root — still a wire field; re-derive before claiming closure")


def check_d_roster_trust():
    src = read("internal/central/http.go")
    b = func_body(src, "func (s *Service) buildAgentsView(")
    if not b:
        fail("D: buildAgentsView not found")
        return
    if re.search(r"s\.manifests\.ForRoot\(b\.Key\.Root\)", b) and "rosterTrust(manifest," in b:
        ok("D: the roster's `Trust` column is computed from a manifest resolved by "
           "b.Key.Root — so 'ok' is a statement about the DIRECTORY, not the agent")
    else:
        fail("D: roster trust derivation changed — re-derive")
    rt = func_body(src, "func rosterTrust(")
    # The content check is real and must be recorded, not buried.
    if "want != hash" in rt and '"modified"' in rt and '"unsigned"' in rt:
        ok("D sound: rosterTrust compares the baseline hash against the manifest — the "
           "per-file CONTENT check is real. The identity envelope around it is what's loose. "
           "Recording this because a finding that only names breakage is half a finding.")
    else:
        fail("D: rosterTrust's comparison changed")


def check_e_verify_ignores_agentid():
    b = func_body(read("internal/provenance/provenance.go"), "func Verify(")
    if not b:
        fail("E: Verify not found")
        return
    if re.search(r"AgentID|\.AgentID", b):
        fail("E: Verify now reads AgentID — the 'unconsulted' claim is wrong; re-derive")
    else:
        ok("E gap: provenance.Verify walks hostFiles against m.Files and never reads "
           "m.AgentID — no approval decision can distinguish two identities")


def check_f_agentid_is_carried():
    src = read("internal/provenance/provenance.go")
    sites = []
    if re.search(r"AgentID\s+string\s+`json:\"agent_id\"`", src):
        sites.append("Manifest field")
    bm = func_body(src, "func BuildManifest(")
    if "AgentID:   agentID" in bm:
        sites.append("BuildManifest sets it")
    if re.search(r"AgentID:\s+m\.AgentID", src):
        sites.append("copied into a child struct")
    if len(sites) >= 3:
        ok("F: AgentID is carried and populated (%s) — so 'dead code' would be the WRONG "
           "claim. It is signed, propagated, and consulted by nothing. 'Unconsulted' is the "
           "precise word." % ", ".join(sites))
    else:
        fail("F: AgentID plumbing is now %r — re-derive the wording" % sites)


def check_g_nil_safety():
    b = func_body(read("internal/central/manifest.go"), "func (s *Service) ProvenanceEvents(")
    fr = func_body(read("internal/central/manifest.go"), "func (r *ManifestRegistry) ForRoot(")
    if "s.manifests.ForRoot(inv.Root)" in b and "if r == nil {" in fr:
        ok("G sound: ProvenanceEvents dereferences a possibly-nil *ManifestRegistry, which "
           "reads as a nil panic cold but is correct — ForRoot checks its receiver first. "
           "Verified rather than assumed.")
    else:
        fail("G: nil-safety shape changed — re-derive (this is load-bearing for the claim)")


def check_h_doc():
    doc = ROOT / "docs/FINDING-2026-11-01v-manifest-is-keyed-by-root-not-identity.md"
    if not doc.exists():
        fail("H: doc missing")
        return
    t = re.sub(r"\s+", " ", re.sub(r"[*_`]", "", doc.read_text()))
    for phrase, label in [
        ("NOT APPLIED", "the not-fixed marker"),
        ("an agent identity is exactly as unique as its directory", "the one-line claim"),
        ("the content check is sound", "the sound part (D) recorded"),
        ("What I am NOT claiming", "the not-claimed section"),
        ("Not a second copy of beat 729", "the de-duplication against 729"),
    ]:
        if phrase in t:
            ok("H: doc carries %s" % label)
        else:
            fail("H: doc lost %s (%r)" % (label, phrase))


def main():
    check_a_forroot_key()
    check_b_no_collision_check()
    check_c_provenance_uses_wire_root()
    check_d_roster_trust()
    check_e_verify_ignores_agentid()
    check_f_agentid_is_carried()
    check_g_nil_safety()
    check_h_doc()
    print()
    if FAILS:
        print("manifestkeycensus: %d FAIL / %d ok" % (len(FAILS), OKS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("manifestkeycensus: %d ok, 0 FAIL" % OKS)
    return 0


if __name__ == "__main__":
    sys.exit(main())

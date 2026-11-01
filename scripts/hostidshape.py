#!/usr/bin/env python3
"""hostidshape — the finding is WORSE than the finding: there is no identifier at all.

WHY THIS BEAT EXISTS (beat 727)
-------------------------------
Beat 726 (`FINDING-2026-11-01g`) measured that central attributes a host by
`req.Host.ID` — a JSON body field — with no cert→host binding. That finding said a
cert for host A could post as host B.

**That sentence quietly assumed `host.ID` is something a fleet can actually pin.** It
isn't. Following it to the producer settles the question:

    cmd/collector/main.go:29   hostID := flag.String("host-id", "", "stable host id")
    cmd/collector/main.go:179  if id == "" { id = hn }        // hn = os.Hostname()
    cmd/collector/main.go:186  return schema.Host{ID: id, Name: name}

There is no machine identity in the id. It is **an operator-chosen string that defaults
to a hostname.** So:

  * Two machines built from the same image share a hostname → they are ONE host to
    central. Both write the same baseline key `(host.ID, inv.Root)`.
  * A hostname change (re-image, DHCP+DNS churn, a box renamed to match a rack label)
    is a NEW host. Its baseline is empty, so the first ingest after the rename is
    `FirstSeen` — **which by design returns an empty event list** (service.go:110).
    Every change made before the rename is uncomparable, silently, with a log line
    that reads like a routine enrolment.
  * A container running with `--hostname` set is its own "host" to central unless
    somebody sets `-host-id` by hand.

WHY THIS IS A SEPARATE FINDING AND NOT A PARAGRAPH IN 726
----------------------------------------------------------
726's fix (cert-carried `host:<id>`, or a fingerprint enrolment map) **does not survive
this**. Binding a cert to "the id the collector declares" still leaves the id caller-
chosen; you have authenticated a string that two boxes can both type. Any fix has to
start from a value the machine *derives*, not one a flag *supplies* — and this tree has
no such value today. That is a different, and smaller, design question than 726's.

WHAT I MEASURED (source-level; no Go toolchain — see NOT CLAIMED)
-----------------------------------------------------------------
    a) the id's only source is a CLI flag defaulting to os.Hostname()
    b) nothing hashes, derives, or persists a machine identity
    c) `FirstSeen` returns an empty Events slice — so the post-rename ingest is a
       SILENT full baseline reset, not a "no change" report
    d) the baseline key is (HostID, Root), so two same-named hosts collide on one key

(c) is the one I want on the record: the mechanism that makes enrolment quiet also
makes a **rename** quiet. One branch, two meanings, no way for the operator to tell
them apart from the log.

WHAT THIS SCRIPT DOES
---------------------
Pins (a)-(d) as assertions of the CURRENT shape, so any change to how identity is
derived goes red and forces re-derivation instead of leaving three findings describing
a tree that moved. Also asserts my own claim boundaries: if someone adds a derived id,
`check_no_derived_identity` must fail loudly, because that is the day this finding
becomes history.

NOT CLAIMED: Go behaviour (source reads only). NOT CLAIMED: that the fix is hash-based
derivation — that is a fleet-provisioning decision, Dad's, same as 726's.
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
    FAILS.append(m)
    print("FAIL " + m)


def read(rel):
    return (ROOT / rel).read_text(errors="replace")


def body_of(src, sig):
    m = re.search(sig, src)
    if not m:
        return None
    depth, i = 0, m.start()
    while i < len(src):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():i + 1]
        i += 1
    return None


def check_id_is_a_flag():
    src = read("cmd/collector/main.go")
    if re.search(r'flag\.String\("host-id",\s*""', src):
        ok("a gap: host id is a CLI flag with an EMPTY default — caller-chosen by construction")
    else:
        fail("a: the -host-id flag changed shape — re-derive this finding")
    rhs = body_of(src, r"func resolveHost\(")
    if rhs is None:
        fail("a: resolveHost not found")
        return
    if 'os.Hostname()' in rhs and re.search(r"id = hn", rhs):
        ok("a gap: with no flag, the id IS os.Hostname() — a string two boxes can share")
    else:
        fail("a: resolveHost no longer falls back to hostname the way this finding claims")
    if re.search(r"Name:\s*name", rhs):
        ok("a gap: Name is an independent flag (default hostname) — id and name can disagree")
    else:
        fail("a: Name no longer independently supplied")


def check_no_derived_identity():
    """The load-bearing claim: nothing derives, hashes, or persists a machine identity."""
    derived = []
    for p in sorted(ROOT.glob("internal/**/*.go")) + sorted(ROOT.glob("cmd/**/*.go")):
        if p.name.endswith("_test.go"):
            continue
        s = p.read_text(errors="replace")
        # machine-id sources and hashing over host identity
        for pat, why in [
            (r"/etc/machine-id", "machine-id file read"),
            (r"ProductUUID|product_uuid|/sys/class/dmi/id/product_uuid", "DMI product uuid"),
            (r"sha256\.Sum256\([^)]*[Hh]ost", "hash over a host string"),
        ]:
            if re.search(pat, s):
                derived.append("%s: %s" % (p.relative_to(ROOT), why))
    if derived:
        fail("b: a DERIVED machine identity appeared (%s) — if it feeds host.ID the gap is "
             "CLOSED and this finding is history; re-derive, do not patch it" % "; ".join(derived))
    else:
        ok("b gap: nothing in the tree derives a machine identity — no machine-id, no DMI uuid, "
           "no hash over a host string; the id is only ever typed or defaulted")


def check_firstseen_is_silent():
    src = read("internal/central/service.go")
    b = body_of(src, r"func \(s \*Service\) Ingest\(")
    if b is None:
        fail("c: Ingest not found")
        return
    if re.search(r"if !found \{\s*events = \[\]schema\.Event\{\}", b):
        ok("c gap: on !found, events is set to an EMPTY slice — first sighting is silent by design")
    else:
        fail("c: the FirstSeen branch changed — re-derive (the silence may be gone)")
    if re.search(r"FirstSeen:\s*!found", b):
        ok("c gap: the response flags FirstSeen, so the silence is DETECTABLE by a careful "
           "operator but is not an error, not a warning, and not distinguishable from an "
           "enrolment without reading the flag")
    else:
        fail("c: FirstSeen flag no longer surfaced in the result")


def check_key_is_hostid_and_root():
    src = read("internal/central/service.go")
    if re.search(r"key := AgentKey\{HostID:\s*host\.ID,\s*Root:\s*inv\.Root\}", src):
        ok("d gap: the baseline key is (host.ID, inv.Root) — two same-ID hosts share ONE key")
    else:
        fail("d: the AgentKey construction changed — re-derive")
    ks = read("internal/central/keymap.go") if (ROOT / "internal/central/keymap.go").exists() else ""
    if ks and "HostID" in ks:
        ok("d: key lookups are HostID-first (keymap.go), so a rename orphans the whole subtree")


def check_doc_scope():
    doc = ROOT / "docs/FINDING-2026-11-01h-host-id-is-a-typed-string.md"
    if not doc.exists():
        fail("E: the finding doc is missing")
        return
    t = doc.read_text()
    for phrase, label in [
        ("beat 726's fix does not survive", "the 726-is-not-enough claim"),
        ("NOT APPLIED", "the not-fixed marker"),
    ]:
        if phrase in t:
            ok("E: doc carries %s" % label)
        else:
            fail("E: doc lost %s (%r)" % (label, phrase))


def main():
    check_id_is_a_flag()
    check_no_derived_identity()
    check_firstseen_is_silent()
    check_key_is_hostid_and_root()
    check_doc_scope()
    print()
    if FAILS:
        print("hostidshape: %d FAIL / %d ok" % (len(FAILS), OKS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("hostidshape: %d ok, 0 FAIL" % OKS)
    return 0


if __name__ == "__main__":
    sys.exit(main())

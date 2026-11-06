#!/usr/bin/env python3
"""keycollisioncensus — pins what the key round-trip ACTUALLY does, after my finding died.

BEAT 742. I wrote a finding claiming parseAgentKey splits on the first colon and so
misparses a host id containing one. Then I opened the file: it splits on `":/"`, and the
comment names the colon-host-id case as the REASON. The finding was wrong and is retired
(docs/FINDING-2026-11-01w-...md holds the retraction).

This instrument does not re-litigate that. It pins the sound design so a future change
to it is a decision rather than an accident, and it records the two true observations
that were never defects.

Checks:
  A. the separator is `":/"` and the plain colon is only the FALLBACK — this is the whole finding
  B. the colon-host-id tolerance is deliberate and documented (the comment names it)
  C. the fallback's triggering case is acknowledged as unexpected in the code itself
  D. the file backend is the only consumer of the string round-trip (fix would be cheap; no defect)
  E. agent.ID is a directory basename (a design fact — recorded, NOT filed as a defect)
  F. -host-id is free-form and unvalidated (true; NOT filed — no measured harm)
  G. the retiring doc keeps its retraction and its rule
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


def check_a_separator():
    b = func_body(read("internal/central/store.go"), "func parseAgentKey(")
    if not b:
        fail("A: parseAgentKey not found")
        return
    first = b.find('Index(s, ":/")')
    fb = b.find("IndexByte(s, ':')")
    if first >= 0 and fb > first:
        ok("A sound: the separator is `\":/\"`, tried FIRST; the plain colon is the "
           "fallback for a root that isn't absolute. A host id containing a colon parses "
           "back correctly — my retracted finding claimed otherwise. THIS is the check that "
           "told me I was wrong, in a form I could have read the first time.")
    else:
        fail("A: parseAgentKey's separator ORDER changed (first=%d fallback=%d) — the "
             "colon-host-id tolerance may be gone; re-derive before anyone relies on it"
             % (first, fb))


def check_b_documented_rationale():
    src = read("internal/central/store.go")
    t = re.sub(r"\s+", " ", re.sub(r"//+", "", src))
    if "keeps a host id that itself contains a colon" in t:
        ok("B sound: the comment names the colon-host-id case explicitly (even to the point "
           "of citing IPv6 literals) — the tolerance is a decision, not an accident")
    else:
        fail("B: the stated rationale is gone — someone may now 'simplify' the separator "
             "believing it was incidental")


def check_c_fallback_acknowledged():
    b = func_body(read("internal/central/store.go"), "func parseAgentKey(")
    t = re.sub(r"\s+", " ", re.sub(r"//+", "", b))
    if "unexpected, but do not silently drop the baseline" in t:
        ok("C sound: the fallback branch documents its own trigger as unexpected AND says "
           "why it still returns a key — a relative root never loses its baseline silently. "
           "That is the right call, and beat 729's relative-root family is already filed.")
    else:
        fail("C: the fallback's acknowledged caveat is gone — re-read the branch")


def check_d_roundtrip_scope():
    consumers = []
    for rel in ("internal/central/filestore.go", "internal/central/eventstore.go",
                "internal/central/eventstore_sqlite.go"):
        src = read(rel)
        if "parseAgentKey(" in src:
            consumers.append(rel.split("/")[-1])
    if sorted(consumers) == ["filestore.go"]:
        ok("D: the string round-trip is consumed by filestore.go alone — the other backends "
           "hold the AgentKey struct. Pinned as a set so it stays a fact I can cite.")
    else:
        fail("D: round-trip consumers are now %r" % sorted(consumers))


def check_e_agent_id():
    src = read("cmd/collector/main.go")
    hits = re.findall(r"schema\.Agent\{ID: filepath\.Base\((?:collected\[0\]|col)\.Root\)", src)
    if len(hits) == 2:
        ok("E: agent.ID is filepath.Base(root) in both places — agent identity is a "
           "directory basename. Recorded as a design fact. NOT filed as a defect: after "
           "beat 742 I'm not dressing a design choice as a finding.")
    else:
        fail("E: agent.ID derivation is now %d sites (expected 2) — re-check" % len(hits))


def check_f_host_id_flag():
    src = read("cmd/collector/main.go")
    if re.search(r'flag\.String\("host-id", "", "stable host id', src):
        ok("F: -host-id is free-form and unvalidated. TRUE, and NOT filed — no measured harm, "
           "and the parser tolerates it by design (check A).")
    else:
        fail("F: -host-id flag changed — re-check")


def check_g_doc():
    doc = ROOT / "docs/FINDING-2026-11-01w-key-separator-assumes-the-host-id-cannot-contain-it.md"
    if not doc.exists():
        fail("G: doc missing")
        return
    raw = doc.read_text()
    if not raw.lstrip().startswith("# FINDING") or "RETRACTED" not in raw[:400]:
        fail("G: the doc no longer declares its retraction in the title — a reader could "
             "take the body as a live finding")
        return
    ok("G: the doc declares RETRACTED in its title")
    t = re.sub(r"\s+", " ", re.sub(r"[*_`]", "", raw))
    for phrase, label in [
        ("the default hypothesis must be that the finding is wrong", "the earned rule"),
        ("read the function before editing the check", "the mechanical corollary"),
        ("What I am NOT claiming", "the not-claimed section"),
    ]:
        if phrase in t:
            ok("G: doc carries %s" % label)
        else:
            fail("G: doc lost %s (%r)" % (label, phrase))


def main():
    check_a_separator()
    check_b_documented_rationale()
    check_c_fallback_acknowledged()
    check_d_roundtrip_scope()
    check_e_agent_id()
    check_f_host_id_flag()
    check_g_doc()
    print()
    if FAILS:
        print("keycollisioncensus: %d FAIL / %d ok" % (len(FAILS), OKS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("keycollisioncensus: %d ok, 0 FAIL" % OKS)
    return 0


if __name__ == "__main__":
    sys.exit(main())

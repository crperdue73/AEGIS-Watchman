#!/usr/bin/env python3
"""modtimecensus — ModTime is collected, shipped, and never compared.

BEAT 744, from Callie's 7400: "a check that can see a count and hasn't asked whose
count it is." Same question, different field: the collector carries a filesystem
timestamp through three layers and Drift never reads it.

MEASURED (beat 744, real Drift, constructed inventories):
  mtime moved back 3 years, identical content  -> events=0
  content changed, mtime forged to identical   -> events=1 severity=high
So backdating is not a way to hide a change, and the hash is doing its job. The gap is
that the page shows the timestamp with no indication it is unverified and uncompared.

Checks:
  A. ModTime is captured with d.Info() and guarded so failure yields the zero time
  B. it reaches the wire and the page
  C. Drift compares SHA256 and NOTHING ELSE — the inert field
  D. the zero-ModTime rendering is honest (em dash) — the sound part
  E. no comparison site exists anywhere in the three packages, pinned as a set
  F. the doc keeps the measured numbers, the narrow claim, and the rejected option
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


def check_a_captured():
    src = read("internal/collector/collector.go")
    b = func_body(src, "func WalkRoot(")
    if re.search(r"info, ierr := d\.Info\(\)", b) and re.search(r"if ierr == nil \{\s*\n\s*mt = info\.ModTime\(\)\.UTC\(\)", b):
        ok("A: ModTime is captured per matched file from d.Info(), .UTC(), and a failed stat "
           "leaves the ZERO time rather than failing the file — one stat per file, on the "
           "hot walk path, for a field no comparison reads (check C).")
    else:
        fail("A: ModTime capture changed — re-derive")


def check_b_reaches_surfaces():
    wire = read("internal/central/http.go")
    page = read("internal/central/agentspage.go")
    if 'ModTime time.Time `json:"mod_time"`' in wire:
        ok("B: it reaches the wire (http.go:64) and is copied into the agents view")
    else:
        fail("B: the wire field is gone — re-derive")
    if "fmtTime $f.ModTime" in page:
        ok("B: it reaches the page (agentspage.go:160) — the operator reads a timestamp "
           "that took no part in any decision. That is the whole finding.")
    else:
        fail("B: the page no longer renders ModTime — re-derive")


def check_c_inert():
    src = read("internal/collector/drift.go")
    b = func_body(src, "func Drift(")
    if not b:
        fail("C: Drift not found")
        return
    # Quote the ONLY dedupe comparison, then prove ModTime is absent from the function.
    if re.search(r"if c\.SHA256 == b\.SHA256 \{\s*\n\s*continue // unchanged", b):
        ok("C gap: the unchanged-test is `c.SHA256 == b.SHA256` and that is the entire "
           "comparison — no mtime term, no size term, nothing else")
    else:
        fail("C: the unchanged-test changed — re-derive")
    if re.search(r"ModTime", b):
        fail("C: Drift now references ModTime — the field may be live; re-derive before "
             "reusing this finding")
    else:
        ok("C gap: the string `ModTime` does not occur anywhere in Drift. Measured, not "
           "inferred: backdated mtime -> 0 events; forged mtime + changed content -> 1 "
           "event at high. The field is INERT, not wrong.")


def check_d_zero_is_honest():
    page = read("internal/central/agentspage.go")
    t = re.sub(r"\s+", " ", re.sub(r"//+", "", page))
    if "a zero ModTime is honest" in t:
        ok("D sound: a failed stat renders an em dash, and the comment says why — \u201cwe do "
           "not know\u201d rather than a 1970 timestamp. The right call, and the reason the "
           "field is only inert rather than misleading about failure.")
    else:
        fail("D: the zero-time rationale is gone — re-read the rendering")


def check_e_no_other_site():
    # Pin the POPULATION: exactly one file mentions ModTime in a comparison, and it is none.
    sites = []
    for pkg in ("internal/collector", "internal/central", "internal/schema"):
        for p in sorted((ROOT / pkg).glob("*.go")):
            if p.name.endswith("_test.go"):
                continue
            src = p.read_text(errors="replace")
            for m in re.finditer(r"^.*ModTime.*$", src, re.M):
                line = m.group(0).strip()
                if "ModTime" in line:
                    sites.append((pkg.split("/")[-1], p.name, line))
    # Careful (beat 741): strip the declaration and the assignment; a comparison operator
    # is what would make the field live. Assert on OPERATORS, not on presence of the word.
    # Beat 744 own-mistake: `{{fmtTime $f.ModTime}}` in an HTML template matched my `<`
    # operator regex. A template tag is not a comparison. Restrict to Go lines and require
    # an actual Go comparison around ModTime, not any angle bracket on the line.
    go_sites = [x for x in sites if ".html" not in x[1] and "{{" not in x[2]]
    cmp_sites = [x for x in go_sites
                 if re.search(r"ModTime\s*(?:==|!=|<=|>=|<|>)", x[2])
                 or re.search(r"(?:==|!=|<=|>=|<|>)\s*\w+\.ModTime", x[2])]
    if not cmp_sites:
        ok("E gap: across collector/central/schema, ModTime appears in NO comparison — %d "
           "sites, all declaration/capture/copy/render. Pinned by scanning operators, not "
           "the word (beat 741: a declared variable is not an enforced rule)."
           % len(sites))
    else:
        fail("E: ModTime now appears in a comparison at %r — re-derive" % (cmp_sites,))


def check_f_doc():
    doc = ROOT / "docs/FINDING-2026-11-02b-modtime-is-collected-and-never-compared.md"
    if not doc.exists():
        fail("F: doc missing")
        return
    t = re.sub(r"\s+", " ", re.sub(r"[*_`]", "", doc.read_text()))
    for phrase, label in [
        ("NOT APPLIED", "the not-fixed marker"),
        ("Display field wearing a detection field's clothes", "the one-line claim"),
        ("events=0", "the measured backdate result"),
        ("severity=high", "the measured forged-mtime result"),
        ("What I am NOT claiming", "the not-claimed section"),
        ("Cheapest, and my pick", "the recommended option"),
        ("Beat 729's rule", "the narrower-claim discipline"),
    ]:
        if phrase.lower() in t.lower():
            ok("F: doc carries %s" % label)
        else:
            fail("F: doc lost %s (%r)" % (label, phrase))


def main():
    check_a_captured()
    check_b_reaches_surfaces()
    check_c_inert()
    check_d_zero_is_honest()
    check_e_no_other_site()
    check_f_doc()
    print()
    if FAILS:
        print("modtimecensus: %d FAIL / %d ok" % (len(FAILS), OKS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("modtimecensus: %d ok, 0 FAIL" % OKS)
    return 0


if __name__ == "__main__":
    sys.exit(main())

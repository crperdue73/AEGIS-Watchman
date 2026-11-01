#!/usr/bin/env python3
"""contractruncensus — the newest-first contract is stated, checked, and enforced by coincidence.

BEAT 734. Beat 733 ended asking whether `Recent` returns arbitrary records on an unordered
backend. The answer is: **the contract exists and is well-written** — and that is exactly what
makes the hole interesting.

WHAT I FOUND, IN ORDER
----------------------
1. Ordering IS a documented contract, twice:
       eventstore.go:102  // Query returns records matching q, newest first.
       eventstore.go:104  // Recent returns up to limit records for one host (empty = all),
                          //   newest first. It is defined in terms of Query ...
   So this is not an unstated assumption like beat 733's fleet.go comment. It is the interface.

2. It IS tested — against a matrix (`recordStoreImpls`, eventstore_test.go:375) so that, per
   docs/P2.1C-RECORD-STORE-BACKENDS.md, "a new backend is done when it passes the contract
   tests unchanged." Assertions at eventstore_test.go:37 and :470 check newest-first.

3. **But the matrix's third member is registered behind a build tag.**
       eventstore_sqlite_test.go:1  //go:build sqlite
       eventstore_test.go:387       if sqliteImpls != nil { ... }
   In a default `go test ./...`, `sqliteImpls` is nil, the loop body never runs, and the
   matrix contains **two** backends: memory and file. The SQLite backend — the durable one,
   the one whose ordering comes from `ORDER BY when_unix DESC, id DESC` in SQL rather than
   from a Go slice walked backwards — **is never contract-tested by a default test run.**

4. And memory/file don't need the contract: both walk their slice backwards, so they are
   newest-first **because of how they store data**, not because a test holds them to it.
   `SortRecordsNewestFirst` — written for backends that can't promise order — has zero
   callers (beat 725, still).

THE SHAPE OF THE DEFECT
-----------------------
Not "the promise is missing." The promise is stated, and one backend is provably held to it.
The defect is that **the two backends that pass do so by construction, and the one backend
that would need real enforcement is excluded from the test run that would check it.** A
contract whose green light comes from implementations that satisfy it for free is not
evidence about implementations that don't.

This is the inverse of beats 730-733. Those found limits not carried to the reader. This one
finds **a check carried only to the parts that didn't need checking** — a test matrix that
looks like coverage and is, for the interesting case, empty. Same family as beat 730's
"unreadable reported as deleted" in one respect: absence is indistinguishable from success.

WHAT THIS SCRIPT CHECKS
-----------------------
A. the ordering contract is stated on BOTH interface methods (quote the lines)
B. the contract is asserted by tests, and the assertions quote real order comparisons
C. the SQLite backend is registered in the matrix ONLY under `//go:build sqlite`
D. the matrix's default-build population is exactly {memory, file} — pinned as a set, not
   an "at least one" claim (beat 730 rule: pin the whole population)
E. memory and file are newest-first by construction (reverse walk), not by enforcement
F. SortRecordsNewestFirst still has zero non-test callers
G. doc scope: NOT APPLIED, NOT CLAIMED as observed, and the "the promise is stated" framing
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FAILS, OKS = [], 0

EXPECTED_DEFAULT_BACKENDS = ["file", "memory"]


def ok(m):
    global OKS
    OKS += 1
    print("ok   " + m)


def fail(m):
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


def check_a_contract_stated():
    src = read("internal/central/eventstore.go")
    m = re.search(r"type RecordStore interface \{(.*?)\n\}", src, re.S)
    if not m:
        fail("A: RecordStore interface not found")
        return
    body = m.group(1)
    # Beat 734: this went RED first run because the interface wraps "newest / first" across
    # two `//` lines. Strip the comment leaders BEFORE collapsing whitespace, or the "// "
    # between them becomes a literal marker the phrase can never span. Same whitespace
    # class of bug as beat 730's check E — third appearance, so it is now a standing step.
    norm = re.sub(r"\s+", " ", re.sub(r"//+", "", body))
    for phrase, label in [
        ("Query returns records matching q, newest first", "Query's ordering clause"),
        ("newest first. It is defined in terms of Query", "Recent's ordering clause"),
    ]:
        if phrase in norm:
            ok("A: the interface states %s — this is a documented contract, not an "
               "unstated assumption" % label)
        else:
            fail("A: the interface no longer states %s — re-derive" % label)


def check_b_contract_tested():
    t = read("internal/central/eventstore_test.go")
    if not re.search(r'got\[0\]\.RuleID != "r\.3"', t) or not re.search(r'got\[0\]\.RuleID != "c3"', t):
        fail("B: the newest-first assertions changed or vanished — re-derive")
        return
    ok("B: newest-first is asserted in two places (:37 after Recent, :470 after "
       "filter+limit) — the contract is not merely documented, it is tested")


def check_c_sqlite_behind_tag():
    s = read("internal/central/eventstore_sqlite_test.go")
    if re.search(r"^//go:build sqlite", s, re.M):
        ok("C: the SQLite contract-test registration is behind //go:build sqlite — it does "
           "not participate in a default `go test ./...`")
    else:
        fail("C: the SQLite test file no longer carries the sqlite build tag — the "
             "exclusion this finding rests on may be gone; re-derive")
    if re.search(r"if sqliteImpls != nil \{", t := read("internal/central/eventstore_test.go")):
        ok("C: the matrix registers SQLite conditionally (sqliteImpls != nil) — the tag "
           "reaches the matrix through a nil function variable, so the exclusion is silent")
    else:
        fail("C: the conditional registration changed — re-derive")


def check_d_default_population_pinned():
    t = read("internal/central/eventstore_test.go")
    body = func_body(t, "func recordStoreImpls(")
    if not body:
        fail("D: recordStoreImpls not found — re-derive")
        return
    found = re.findall(r'^\s*"(\w+)":\s*func\(\) RecordStore', body, re.M)
    # Beat 734: compared a LIST against a LIST and went red on ['memory','file'] vs
    # ['file','memory'] — pure source order, no meaning. Beat 730's rule was "pin the whole
    # population"; the part I missed is that pinning a population is a SET operation.
    if sorted(found) == sorted(EXPECTED_DEFAULT_BACKENDS):
        ok("D: the default-build matrix is exactly {%s} — pinned as a set, so a backend "
           "silently dropping out of the contract matrix goes red"
           % ", ".join(sorted(found)))
    else:
        fail("D: default-build matrix is %r, expected %r — the population moved; re-decide "
             "whether that is intentional" % (found, EXPECTED_DEFAULT_BACKENDS))
    if "sqlite" in found:
        fail("D: sqlite is now in the default matrix — the finding may be closing")


def check_e_satisfied_by_construction():
    src = read("internal/central/eventstore.go")
    mem = func_body(src, "func (m *MemoryRecordStore) Query")
    fil = func_body(src, "func (f *FileRecordStore) Query")
    for name, b in (("Memory", mem), ("File", fil)):
        if re.search(r"for i := len\([a-z]\.recs\) - 1; i >= 0; i-- \{", b):
            ok("E: %sRecordStore is newest-first BY CONSTRUCTION (reverse slice walk) — it "
               "satisfies the contract for free, so its green light is evidence of nothing "
               "about a backend that needs real ordering" % name)
        else:
            fail("E: %sRecordStore no longer walks its slice backwards — re-derive" % name)


def check_f_helper_unused():
    users = []
    for p in list(ROOT.glob("internal/**/*.go")) + list(ROOT.glob("cmd/**/*.go")):
        if p.name.endswith("_test.go") or p.name == "eventstore.go":
            continue
        if "SortRecordsNewestFirst(" in p.read_text(errors="replace"):
            users.append(p.name)
    if users:
        fail("F: SortRecordsNewestFirst has callers now (%s) — re-derive" % ", ".join(users))
    else:
        ok("F gap: SortRecordsNewestFirst — written for backends that cannot promise order "
           "— still has zero non-test callers, while the one such backend is excluded from "
           "the suite that would check it")


def check_g_doc_scope():
    doc = ROOT / "docs/FINDING-2026-11-01o-contract-enforced-only-where-unneeded.md"
    if not doc.exists():
        fail("G: the finding doc is missing")
        return
    t = re.sub(r"\s+", " ", doc.read_text())
    for phrase, label in [
        ("NOT APPLIED", "the not-fixed marker"),
        ("NOT CLAIMED as observed", "the no-toolchain honesty"),
        ("The promise is stated", "the refusal to overstate this as an unstated assumption"),
    ]:
        if phrase in t:
            ok("G: doc carries %s" % label)
        else:
            fail("G: doc lost %s (%r)" % (label, phrase))


def main():
    check_a_contract_stated()
    check_b_contract_tested()
    check_c_sqlite_behind_tag()
    check_d_default_population_pinned()
    check_e_satisfied_by_construction()
    check_f_helper_unused()
    check_g_doc_scope()
    print()
    if FAILS:
        print("contractruncensus: %d FAIL / %d ok" % (len(FAILS), OKS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("contractruncensus: %d ok, 0 FAIL" % OKS)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""fleetsamplecensus — the fleet rollup calls a 5000-record sample "the fleet".

BEAT 733. Beat 732 closed by asking whether BySeverity and the per-host counts have the
same clean path as ByTechnique. They do not. The technique path is clean; the **scope**
the whole view describes is not.

THE MEASUREMENT
---------------
    fleet.go:42   const defaultFleetLimit = 5000
    fleet.go:67   Limit: atoiDefault(r.URL.Query().Get("limit"), defaultFleetLimit)
    fleet.go:79   recs, err := s.records.Query(r.Context(), q)
    fleet.go:86   view := buildFleetView(recs, recentN)
    fleet.go:92   TotalEvents: len(recs)

Every backend applies `Limit` by **breaking out of the scan early** (Memory :170, File
:273) or with SQL `LIMIT ?` (SQLite :205). So `recs` is the newest N records overall, and
`buildFleetView` then walks that slice and:

  - groups by `rec.HostID`, creating a host row for each distinct host it happens to see;
  - sets `TotalEvents = len(recs)` — the count of the sample, not of the store;
  - sets each host's `Total`, `BySeverity`, `ByTechnique`, `BySource`, `ByPack`,
    `MaxSeverity` from the sample only.

`fleetpage.go:64` renders the same view via `s.records.Recent(ctx, "", defaultFleetLimit)`
— same cap, same page.

THE CONSEQUENCE, STATED EXACTLY
-------------------------------
Once the store holds more than 5000 retained records, a host that has not produced an
event in the recent window **does not appear in the fleet view at all.** Not with zeros —
absent. It is not "quiet", it is missing from a page titled fleet. And the numbers the view
does show describe the newest 5000 events, which is a sliding window that shrinks each
host's history as other hosts get noisier.

This is NOT "the count is wrong": the numbers honestly describe the 5000 records read. The
defect is that **nothing on the view says the view is a sample.** A header reading
`TotalEvents: 5000` sits next to `Hosts: [...]` and there is no flag, no `Truncated`, no
`LimitApplied` — the reader has no way to know they are looking at a quota.

Is it reachable? `defaultFleetLimit` is 5000 and the store retention window decides how
many records exist. A single host spraying an argv rule during a pentest can generate
thousands of records in an hour; a small fleet under a long retention window crosses 5000
quietly. I have not measured a production store (no toolchain, beat 642) — I am claiming
the mechanism, not a body count.

THE SECOND HALF, WHICH IS A REAL BUG NOT A DESIGN CHOICE
--------------------------------------------------------
    fleet.go:128   if h.LastSeen.IsZero() || rec.When.After(h.LastSeen) {
    fleet.go:126   // Records are newest-first, so the first seen is the latest.

The comment states an ordering guarantee. The guarantee holds today only because all three
Query implementations scan newest-first — and `SortRecordsNewestFirst` (eventstore.go:401)
exists precisely to fix callers that cannot assume it, with **zero callers** (measured beat
725). So the field is correct by construction today, protected by a comment rather than by
a call. That is beat 725's finding unchanged and it is why this script pins the ordering
per backend rather than trusting the comment.

WHAT THIS SCRIPT CHECKS
-----------------------
A. the cap exists, is applied to the query, and is not surfaced anywhere on the view
B. all three backends really apply Limit (Memory early-break, File early-break, SQLite LIMIT)
C. `TotalEvents` is `len(recs)` — the sample — and no field distinguishes sample from store
D. the console page uses the same cap (so the HTML page and the JSON agree, both sampled)
E. the newest-first comment still has no call-time enforcement (carries beat 725's finding)
F. doc scope: NOT APPLIED, NOT CLAIMED as observed, and the honest "numbers are correct,
   the scope is unlabelled" framing
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FAILS, OKS = [], 0

EXPECTED_CAP = 5000


def ok(m):
    global OKS
    OKS += 1
    print("ok   " + m)


def fail(m):
    FAILS.append(m)
    print("FAIL " + m)


def read(rel):
    return (ROOT / rel).read_text(errors="replace")


def check_a_cap_exists_and_is_unsurfaced():
    src = read("internal/central/fleet.go")
    if not re.search(r"const defaultFleetLimit = %d" % EXPECTED_CAP, src):
        fail("A: the default fleet limit changed from %d — re-derive the reachability "
             "argument before keeping the doc" % EXPECTED_CAP)
        return
    ok("A: defaultFleetLimit = %d caps the scan, justified in-comment as a cost guard "
       "(store is already retention-bounded)" % EXPECTED_CAP)
    if not re.search(r"Limit:\s+atoiDefault\(r\.URL\.Query\(\)\.Get\(\"limit\"\), defaultFleetLimit\)", src):
        fail("A: the limit is no longer applied to the RecordQuery — re-derive")
        return
    ok("A: the cap is applied to the actual RecordQuery, so the sample comes from the store")
    # Is the sampling surfaced? Quote the view struct, not a word near it (beat 731/732 rule).
    m = re.search(r"type fleetView struct \{(.*?)\n\}", src, re.S)
    body = m.group(1) if m else ""
    for marker in ("Truncated", "LimitApplied", "Sampled", "Scanned"):
        if marker in body:
            fail("A: fleetView now carries %s — the sampling may be labelled; the gap may "
                 "be closing; re-derive" % marker)
            return
    ok("A gap: fleetView carries no Truncated/LimitApplied/Sampled field — a reader of 5000 "
       "events cannot tell a full fleet from a full quota")


def check_b_all_backends_apply_limit():
    mem = read("internal/central/eventstore.go")
    # M3 (beat 733): searching the whole file matched the FILE backend's identical guard
    # after the memory backend's was deleted — green on a backend that no longer honours
    # Limit. Fifth word-near-the-code check in five beats, and the first where the "nearby
    # word" was the same code in a different function. Scope every check to its function.
    def func_body(src, name):
        i = src.find("func %s(" % name)
        if i < 0:
            return ""
        j = src.find("\nfunc ", i + 1)
        return src[i:] if j < 0 else src[i:j]

    memfn = func_body(mem, "(m *MemoryRecordStore) Query")
    if not memfn:
        fail("B: MemoryRecordStore.Query not found — re-derive")
    elif re.search(r"if q\.Limit > 0 && len\(out\) >= q\.Limit \{\s*\n\s*break", memfn):
        ok("B: MemoryRecordStore breaks the scan at q.Limit — Limit truncates the result")
    else:
        fail("B: MemoryRecordStore's limit guard changed — re-derive")
    filefn = func_body(mem, "(f *FileRecordStore) Query")
    if filefn and re.search(r"if q\.Limit > 0 && len\(out\) >= q\.Limit \{\s*\n\s*break", filefn):
        ok("B: FileRecordStore has its own guard, checked in its own function — not inferred "
           "from the memory backend's")
    else:
        fail("B: FileRecordStore's limit guard changed — re-derive")
    sql = read("internal/central/eventstore_sqlite.go")
    if re.search(r'if q\.Limit > 0 \{\s*\n\s*sqlStr \+= " LIMIT \?"', sql):
        ok("B: SQLiteRecordStore applies SQL LIMIT — same truncation semantics")
    else:
        fail("B: the SQL LIMIT guard changed — re-derive")
    if re.search(r"ORDER BY when_unix DESC, id DESC", sql):
        ok("B: SQLite orders newest-first deterministically — the slice really is the "
           "newest N, not an arbitrary N")
    else:
        fail("B: SQLite changed its ORDER BY — the 'newest N' claim depends on it")


def check_c_total_is_the_sample():
    src = read("internal/central/fleet.go")
    if re.search(r"TotalEvents:\s*len\(recs\)", src):
        ok("C gap: TotalEvents = len(recs) — the count of the SAMPLE, presented under a "
           "name that reads as the fleet's total")
    else:
        fail("C: TotalEvents no longer comes from len(recs) — re-derive")
    if re.search(r"for _, rec := range recs \{", src) and re.search(r"byHost\[rec\.HostID\]", src):
        ok("C gap: hosts are discovered from the sample, so a host with no event in the "
           "newest %d records is ABSENT from the fleet view rather than shown at zero"
           % EXPECTED_CAP)
    else:
        fail("C: the host-discovery loop changed — re-derive the absence claim")


def check_d_page_matches_api():
    pg = read("internal/central/fleetpage.go")
    if re.search(r"s\.records\.Recent\(r\.Context\(\), \"\", defaultFleetLimit\)", pg):
        ok("D: the HTML console uses the same cap — page and API agree, and both are a "
           "sample; the agreement does not make either complete")
    else:
        fail("D: the console's record fetch changed — re-derive")


def check_e_ordering_asserted_not_enforced():
    src = read("internal/central/fleet.go")
    if re.search(r"// Records are newest-first", src):
        ok("E: the newest-first guarantee is a COMMENT at the point of use")
    else:
        fail("E: the ordering comment is gone — is the assumption now enforced or dropped?")
    es = read("internal/central/eventstore.go")
    if re.search(r"func SortRecordsNewestFirst\(", es):
        users = []
        for p in list(ROOT.glob("internal/**/*.go")) + list(ROOT.glob("cmd/**/*.go")):
            if p.name.endswith("_test.go") or p.name == "eventstore.go":
                continue
            t = p.read_text(errors="replace")
            if "SortRecordsNewestFirst(" in t:
                users.append(p.name)
        if not users:
            ok("E gap: SortRecordsNewestFirst still has ZERO callers — the helper written "
               "for unordered backends guards nothing, and fleet.go:126 relies on the "
               "comment instead (beat 725, unchanged)")
        else:
            fail("E: SortRecordsNewestFirst now has callers (%s) — the ordering assumption "
                 "may be enforced; re-derive" % ", ".join(users))
    else:
        fail("E: the helper is gone — re-derive what now protects fleet.go:126")


def check_f_doc_scope():
    doc = ROOT / "docs/FINDING-2026-11-01n-fleet-view-is-a-5000-record-sample.md"
    if not doc.exists():
        fail("F: the finding doc is missing")
        return
    t = re.sub(r"\s+", " ", doc.read_text())
    for phrase, label in [
        ("NOT APPLIED", "the not-fixed marker"),
        ("NOT CLAIMED as observed", "the no-toolchain honesty"),
        ("nothing on the view says the view is a sample", "the precise claim"),
    ]:
        if phrase in t:
            ok("F: doc carries %s" % label)
        else:
            fail("F: doc lost %s (%r)" % (label, phrase))


def main():
    check_a_cap_exists_and_is_unsurfaced()
    check_b_all_backends_apply_limit()
    check_c_total_is_the_sample()
    check_d_page_matches_api()
    check_e_ordering_asserted_not_enforced()
    check_f_doc_scope()
    print()
    if FAILS:
        print("fleetsamplecensus: %d FAIL / %d ok" % (len(FAILS), OKS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("fleetsamplecensus: %d ok, 0 FAIL" % OKS)
    return 0


if __name__ == "__main__":
    sys.exit(main())

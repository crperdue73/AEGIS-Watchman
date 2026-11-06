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
    # BEAT 790: this check used to require the literal
    # `Limit: atoiDefault(...Get("limit"), defaultFleetLimit)` and to FAIL if fleetView grew a
    # Truncated/Sampled field -- i.e. it asserted the defect and would go red on the fix. That is
    # correct for a census (it forces re-derivation instead of silent drift), but the re-derivation
    # has now happened: the handler asks for scanLimit+1 and labels the view. So the check asks the
    # question the cause still poses -- is the cap applied, and is it now HONEST about itself?
    if not re.search(r"Limit:\s+scanLimit \+ 1,", src):
        fail("A: the handler no longer asks for limit+1 — the at-the-cap vs over-the-cap "
             "distinction is gone (finding 2026-11-01n option 1, beat 790)")
        return
    ok("A: the cap is applied to the actual RecordQuery, requesting one over the cap so "
       "'exactly at the cap' and 'over the cap' are distinguishable")
    m = re.search(r"type fleetView struct \{(.*?)\n\}", src, re.S)
    body = m.group(1) if m else ""
    if "Truncated" not in body:
        fail("A: fleetView no longer carries Truncated — a reader of %d events cannot tell a "
             "full fleet from a full quota (this was the OPEN gap; it has reopened)"
             % EXPECTED_CAP)
        return
    if "*bool" not in body:
        fail("A: Truncated is not a *bool — absent and false are now indistinguishable to a "
             "JSON reader, so an older client cannot tell 'complete' from 'old server'")
        return
    ok("A resolved: fleetView carries Truncated *bool, so a capped view says it is a sample "
       "instead of rendering a quota as a total")


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
    # BEAT 790: the console now probes limit+1 and slices, so it can say "sample" instead of
    # "retained". The old check asserted the single-call fetch; re-derived to require the probe
    # AND the label, because a page that fetches correctly but never sets Truncated is exactly
    # the gap this beat closed.
    # BEAT 799: the console no longer scans at all. `rosterView` rolls up every retained
    # record per host, so D stops being "does the page label its sample" and becomes "does the
    # page still take a sample". The old assertions are not deleted to make the gate green:
    # each one is replaced by the claim that superseded it.
    if re.search(r"s\.records\.Recent\(r\.Context\(\)", pg):
        fail("D: the console is back on records.Recent — that is the capped global scan this "
             "beat removed; the roster is the only exact path")
        return
    if not re.search(r"rosterView\(r\.Context\(\), s\.records,", pg):
        fail("D: the console does not call rosterView — the page has no exact rollup")
        return
    # Both flags must be independent. Reusing Truncated for the detail caveat is the mistake I
    # actually made wiring this (see WORKLOG beat 799): it printed "capped at 0 — this is a
    # sample" over an exact total. A file-level check cannot prove the template keys on the
    # right field, so ask it directly.
    if re.search(r"view\.Truncated = ", pg):
        fail("D: the console sets Truncated — on this path the totals are exact, so Truncated "
             "is false here; the only trim left is the per-host detail column (DetailCapped)")
        return
    if not re.search(r"view\.DetailCapped = &\w+", pg):
        fail("D: the console never sets DetailCapped — the five-event detail column IS trimmed, "
             "so a page with no DetailCapped flag hides a trim that really happens")
        return
    if not re.search(r"\{\{if deref \.DetailCapped\}\}", pg):
        fail("D: the page template does not dereference DetailCapped — text/template treats "
             "ANY non-nil *bool as true, so absent deref means the caveat branch never renders "
             "(measured beat 790, same trap)")
        return
    ok("D resolved: the HTML console rolls up the whole store via rosterView, sets "
       "DetailCapped (not Truncated) and dereferences it — the counts are exact and the only "
       "thing admitted as trimmed is the per-host detail sample")


def check_e_ordering_asserted_not_enforced():
    src = read("internal/central/fleet.go")
    # Beat 789: this check used to go GREEN on the presence of the defect ("the guarantee is a
    # COMMENT"). That inverts it -- it reported health while the thing it named was wrong, and
    # would have gone red on the fix. Rewritten to ask the question the cause actually poses.
    if re.search(r"// Records are newest-first", src):
        fail("E: the newest-first guarantee is asserted in a comment again at the point of use — "
             "is the sort still there, or did the comment come back to replace it?")
    elif re.search(r"SortRecordsNewestFirst\(recs\)", src):
        ok("E resolved: the newest-first ordering is ESTABLISHED in buildFleetView, not asserted "
           "in a comment (beat 789, finding 2026-11-01n option 4)")
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
        elif "fleet.go" in users:
            # Beat 789: finding 2026-11-01n option 4 was applied. buildFleetView now calls this
            # before populating Recent, so the assumption is ENFORCED rather than documented.
            # What must hold now: the call is present, and it runs on the path that builds Recent.
            fleet = read("internal/central/fleet.go")
            if "SortRecordsNewestFirst(recs)" not in fleet:
                fail("E: fleet.go now references the helper but does not sort recs with it — "
                     "re-derive")
            elif not re.search(r"if recentN > 0 \{\s*(?://[^\n]*\n\s*)*SortRecordsNewestFirst\(recs\)",
                               fleet):
                fail("E: the sort is not on the recentN>0 path — Recent may be built unsorted; "
                     "re-derive")
            else:
                ok("E resolved: SortRecordsNewestFirst is now called by buildFleetView on the "
                   "path that builds Recent (finding 2026-11-01n option 4, beat 789). The order "
                   "Recent depends on is now established by this function, not promised by the "
                   "caller. fleetorder_test.go holds this from an unordered backend's records")
        else:
            fail("E: SortRecordsNewestFirst has callers (%s) — the ordering assumption "
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

#!/usr/bin/env python3
"""fleetcoveragecensus — the fleet view's population comes from records, not the roster.

BEAT 740. I opened this beat looking for a page that times out scanning its own store
(beat 733's 5000 cap x beat 739's 10s deadline) and found the OPPOSITE: the cap is an
early exit in every backend, so the cap bounds the work. That sound part is check A and
it is load-bearing for the rest of the finding.

What is actually here: buildFleetView starts from a map keyed off the records it was
handed, and nothing else. The enrolled roster (Store.ListBaselines) is never consulted.
So a quiet host is indistinguishable from an absent one.

Checks:
  A. the limit is an EARLY EXIT in all three backends — the cap bounds the work, not the
     answer (the thing I expected to be broken and wasn't; fail if this stops holding)
  B. buildFleetView's host population comes ONLY from the records — no roster input
  C. ListBaselines is NOT reachable from the fleet path (pinned as the exact call-site set)
  D. LastSeen cannot be zero for a listed host — the one place absence could be expressed
     is unreachable (pinned as a structural consequence, not filed as its own bug)
  E. / and /api/v1/fleet render different recent counts from the same function
  F. cancellation is backend-dependent: ctx honoured by exactly one of three Query impls
  G. the doc records the sound part (A) and states what is NOT claimed
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


def check_a_limit_is_early_exit():
    es = read("internal/central/eventstore.go")
    for label, needle in (("Memory", "func (m *MemoryRecordStore) Query"),
                          ("File", "func (f *FileRecordStore) Query")):
        b = func_body(es, needle)
        if not b:
            fail("A: %s Query not found" % label)
            continue
        # The break must live INSIDE the loop, so it stops scanning rather than
        # filtering the whole slice first.
        loop_i = b.find("for i := len(")
        out_i = b.find("out = append(out, r)")
        brk_i = b.find("len(out) >= q.Limit")
        if loop_i >= 0 and out_i > loop_i and brk_i > out_i:
            ok("A: %s Query breaks at len(out) >= q.Limit INSIDE the reverse loop — the cap "
               "stops the scan instead of filtering a full result, so limit bounds the work"
               % label)
        else:
            fail("A: %s Query no longer early-exits at the limit — the cap may now bound only "
                 "the answer; re-derive (I expected a slow scan and found this instead)" % label)
    sq = read("internal/central/eventstore_sqlite.go")
    if re.search(r'ORDER BY when_unix DESC, id DESC"', sq) and re.search(r'sqlStr \+= " LIMIT \?"', sq):
        ok("A: SQLite does the same in SQL (ORDER BY ... LIMIT ?) — the limit reaches the "
           "database, so no full materialisation behind the cap")
    else:
        fail("A: sqlite query lost its server-side LIMIT")


def check_b_population_source():
    src = read("internal/central/fleet.go")
    b = func_body(src, "func buildFleetView(")
    if not b:
        fail("B: buildFleetView not found")
        return
    # The host map can only be appended to by the record loop.
    if re.search(r"byHost := map\[string\]\*fleetHost\{\}", b) and \
       re.search(r"byHost\[rec\.HostID\] = h", b):
        ok("B gap: buildFleetView's host map is populated ONLY by ranging over recs — its "
           "signature takes (recs, recentN) and has no roster parameter, so a host with no "
           "record cannot be represented")
    else:
        fail("B: buildFleetView's population mechanism changed — re-derive")
    if re.search(r"func buildFleetView\(recs \[\]Record, recentN int\)", src):
        ok("B: the signature is (recs, recentN) — two parameters, no second data source")
    else:
        fail("B: buildFleetView takes more than the records now; a roster may have arrived")


def check_c_listbaselines_unreachable():
    # Pin the WHOLE population of call sites as a set (beat 730/734 rule).
    sites = []
    for rel in ("internal/central/http.go", "internal/central/fleet.go",
                "internal/central/fleetpage.go", "internal/central/service.go"):
        if re.search(r"\.ListBaselines\(", read(rel)):
            sites.append(rel.split("/")[-1])
    if sorted(sites) == ["http.go"]:
        ok("C: `ListBaselines` is called from exactly one file, http.go (the /api/v1/agents "
           "handler). The fleet path never consults the enrolled roster. Pinned as a set, so "
           "a fleet-side call going in goes red.")
    elif "fleet.go" in sites or "fleetpage.go" in sites:
        fail("C: the fleet path now calls ListBaselines — my finding may be closed; re-derive")
    else:
        fail("C: ListBaselines call population is now %r" % sorted(sites))


def check_d_lastseen_unreachable_zero():
    src = read("internal/central/fleet.go")
    b = func_body(src, "func buildFleetView(")
    # LastSeen is only set inside the record loop, so a listed host always has a value.
    if re.search(r"h\.LastSeen = rec\.When", b) and b.find("h.LastSeen = rec.When") > b.find("for _, rec := range recs"):
        ok("D: `LastSeen` is only assigned inside the record loop — so on every host the "
           "view can list, it is non-zero. The one field that could have rendered 'no "
           "records' is structurally unable to. Recorded as a consequence of B, not filed "
           "as its own defect.")
    else:
        fail("D: LastSeen assignment moved out of the record loop — re-derive")
    tf = read("internal/central/fleet.go")
    if re.search(r'LastSeen\s+time\.Time\s+`json:"last_seen"`', tf):
        ok("D: `last_seen` has no `omitempty` and is a value type — absence is inexpressible "
           "as JSON null as well")
    else:
        fail("D: last_seen field shape changed")


def check_e_two_render_counts():
    fp = read("internal/central/fleetpage.go")
    fg = read("internal/central/fleet.go")
    # BEAT 799: the page no longer renders buildFleetView at all -- it rolls up the whole store
    # via rosterView. That does not retire E; it makes it a bigger claim. Before, page and
    # endpoint differed only in the recent-count per host; now the PAGE IS EXACT and the ENDPOINT
    # IS STILL A CAPPED SAMPLE of the newest 5000 records, so the same question ("what is the
    # fleet?") gets an exact answer from the console and an understated one from the API.
    # Writing this branch as ok() would have been the fifth time I recorded a divergence as a
    # note rather than a failure (see the beat-740 comment below). The divergence is real, so it
    # FAILS and names the next step: move /api/v1/fleet onto rosterView.
    roster = re.search(r"rosterView\(r\.Context\(\), s\.records, (\d+),", fp)
    if roster and re.search(r"buildFleetView\(recs,", fp):
        fail("E: the page now does BOTH a roster rollup and a buildFleetView scan — remove the "
             "capped scan from the page path")
        return
    if roster:
        # BEAT 800: the JSON path moved onto the same rollup. E's original finding — page and
        # endpoint disagreeing about what the fleet is — is now GONE, not relabelled. Per the
        # rule written at the bottom of this function (beat 740: a finding that disappears is a
        # FAIL to re-derive, never an ok() note), this branch is rewritten to assert the new,
        # narrower divergence that actually remains.
        if re.search(r"buildFleetView\(recs,", fg):
            fail("E: /api/v1/fleet is back on a capped global scan — the page and the endpoint "
                 "are describing different fleets again")
            return
        if not re.search(r"view := aggregateByHost\(recs\)", fg):
            fail("E: /api/v1/fleet no longer rolls up by host — re-derive what it returns")
            return
        # Argument-level, not prefix-level: E-mut2 passed by swapping the call's third
        # argument for 0 while leaving the prefix intact, which is exactly the drift this
        # check exists to catch.
        if not re.search(r"attachRecent\(r\.Context\(\), s\.records, out\.Hosts, recentN\)", fg):
            fail("E: /api/v1/fleet does not attach its detail sample via the shared helper — "
                 "the two surfaces can drift on `recent` again")
            return
        # The divergence that DOES remain, and it is an honest one this time: both surfaces roll
        # up the whole store, but `limit` still bounds the query, so a caller-supplied small
        # limit produces a bounded total on both. Pinned so it stays a named, measured property
        # rather than something a future beat rediscovers as a bug.
        if not re.search(r"truncated := len\(recs\) > scanLimit", fg):
            fail("E: the endpoint no longer reports when `limit` bounded its scan — a bounded "
                 "total would be presented as a fleet total")
            return
        ok("E resolved and re-derived: /api/v1/fleet and the HTML console now roll up the SAME "
           "set — aggregateByHost over every record the store returns, detail attached per host "
           "through the same attachRecent, and the residual `limit` bound reported honestly via "
           "truncated. The page/endpoint divergence this check was written for is gone; what "
           "remains is that a caller-supplied small limit bounds BOTH surfaces equally")
        return
    page = re.search(r"buildFleetView\(recs, (\d+)\)", fp)
    json_default = re.search(r'atoiDefault\(r\.URL\.Query\(\)\.Get\("recent"\), (\d+)\)', fg)
    if page and json_default:
        if page.group(1) != json_default.group(1):
            ok("E: the HTML page embeds %s recent records per host while the JSON endpoint "
               "defaults to %s — same function, two answers, documented on neither"
               % (page.group(1), json_default.group(1)))
        else:
            # Beat 740: THE FOURTH TIME I HAVE WRITTEN THIS BRANCH AS AN ok() NOTE.
            # 736 M1, 738 M5, 739 M4, now 740 M5. The class is now named and the rule is
            # mechanical: if a check exists to observe a property CHANGING, the change is a
            # FAIL, because the exit status is the only thing anyone downstream reads.
            fail("E: the page and endpoint now agree on %s recent records — the divergence "
                 "this check exists to record is GONE; re-derive the finding"
                 % page.group(1))
    else:
        fail("E: recent-count call sites unrecognised — re-derive")


def check_f_cancellation_depends_on_backend():
    es = read("internal/central/eventstore.go")
    sq = read("internal/central/eventstore_sqlite.go")
    ignoring = len(re.findall(r"func \(m?f? ?\*?\w*RecordStore\) Query\(_ context\.Context", es))
    honouring = bool(re.search(r"func \(s \*SQLiteRecordStore\) Query\(ctx context\.Context", sq)) and \
        bool(re.search(r"s\.db\.QueryContext\(ctx,", sq))
    if ignoring == 2 and honouring:
        ok("F: cancellation is backend-dependent and undocumented — Memory and File Query "
           "take `_ context.Context` (ignore it), SQLite takes `ctx` and uses QueryContext. "
           "A cancelled fleet request stops early on the durable backend and runs to "
           "completion on the other two. Not a bug; an undocumented difference.")
    elif ignoring == 0:
        fail("F: the backends now honour ctx — re-derive")
    else:
        fail("F: Query ctx handling is now %d ignoring / honouring=%r — re-derive"
             % (ignoring, honouring))


def check_g_doc():
    doc = ROOT / "docs/FINDING-2026-11-01u-fleet-view-population-comes-from-records.md"
    if not doc.exists():
        fail("G: doc missing")
        return
    t = re.sub(r"\s+", " ", re.sub(r"[*_`]", "", doc.read_text()))
    for phrase, label in [
        ("NOT APPLIED", "the not-fixed marker"),
        ("the cap is an early exit, not a post-filter", "the SOUND part (check A) recorded"),
        ("A zero and a silence are the same shape", "the one-line claim"),
        ("What I am NOT claiming", "the not-claimed section"),
    ]:
        if phrase in t:
            ok("G: doc carries %s" % label)
        else:
            fail("G: doc lost %s (%r)" % (label, phrase))


def main():
    check_a_limit_is_early_exit()
    check_b_population_source()
    check_c_listbaselines_unreachable()
    check_d_lastseen_unreachable_zero()
    check_e_two_render_counts()
    check_f_cancellation_depends_on_backend()
    check_g_doc()
    print()
    if FAILS:
        print("fleetcoveragecensus: %d FAIL / %d ok" % (len(FAILS), OKS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("fleetcoveragecensus: %d ok, 0 FAIL" % OKS)
    return 0


if __name__ == "__main__":
    sys.exit(main())

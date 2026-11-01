#!/usr/bin/env python3
"""retentioncensus — does the OTHER bounded history have beat 724's bug?

WHY THIS EXISTS (beat 725)
--------------------------
Beat 724 found `Timeline.Record` trimming to the cap by ARRIVAL order, so a busy
host could forget an old-but-fresh change and the flagship egress rule would go
silent. The honest follow-up question is not "is that fixed" (it is Dad's call) —
it is **"how many places in this tree age or bound a history, and does each one
age by the field it should?"**

There are three bounded histories:
  1. rules/timeline.go        Timeline.Record            — time (At) bounded, per host
  2. central/eventstore.go    MemoryRecordStore.pruneLocked  — time (When) bounded, global
  3. central/eventstore.go    FileRecordStore.pruneLocked     — time (When) bounded, global
  (4. central/eventstore_sqlite.go prune — SQL, ordered by when_unix — checked too)

A history has TWO independent decisions and they can disagree:
  - EVICT (window): which entries are too OLD  -> must compare a TIME field
  - TRIM  (cap):    which entries to drop when over the bound -> must drop the
                    OLDEST BY THE SAME TIME FIELD

**A trim that drops by POSITION is only correct if the list is append-ordered in
the same field as the window filter.** That is the invariant beat 724 found
unenforced in timeline.go. This script asks it of the other two.

THE INVARIANT, MEASURED RATHER THAN ASSUMED
-------------------------------------------
The claim under test is: *every trim in this tree drops the oldest entry by the
field its own window filter uses.* It is decided from SOURCE (which index does
the trim keep) AND, for append-ordered stores, from whether the APPEND ORDER can
disagree with that field. Both halves are needed: a front-trim is fine on a list
that is provably time-ordered, and a time-ordered trim is fine everywhere.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
It does not decide whether a front-trim should be fixed (Dad's call, beat 724's
rule: not on a heartbeat). It reports which trims are *supported* and which are
*unsupported*, and pins the population so a new bounded history cannot appear
without being asked.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

FAILS = []
OKS = 0


def ok(msg):
    global OKS
    OKS += 1
    print("ok   " + msg)


def fail(msg):
    FAILS.append(msg)
    print("FAIL " + msg)


def read(rel):
    return (ROOT / rel).read_text()


def body_of(src, signature_re):
    """Brace-balanced body of the function whose declaration matches signature_re."""
    m = re.search(signature_re, src)
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


# ---------------------------------------------------------------------------
# 1. The trim decision, read from source.
#
# Every prune/trim in the tree is classified by the shape of the slice it keeps.
# This is deliberately literal: it looks for the exact expression family, not a
# comment, because beat 722/724 were both cases of a comment describing a
# neighbouring line correctly.
# ---------------------------------------------------------------------------
TRIMS = [
    # (label, file, signature, expected_kind)
    # expected_kind: "by_time" = keeps the newest N by the window's own field;
    #                "by_position" = keeps the last N appended.
    ("Timeline.Record cap trim", "internal/rules/timeline.go",
     r"func \(t \*Timeline\) Record\(", "by_position"),
    ("MemoryRecordStore.pruneLocked cap trim", "internal/central/eventstore.go",
     r"func \(m \*MemoryRecordStore\) pruneLocked\(", "by_position"),
    ("FileRecordStore.pruneLocked cap trim", "internal/central/eventstore.go",
     r"func \(f \*FileRecordStore\) pruneLocked\(", "by_position"),
    ("SQLiteRecordStore.prune cap trim", "internal/central/eventstore_sqlite.go",
     r"func \(s \*SQLiteRecordStore\) prune\(", "by_time"),
]


def classify_trim(body):
    """Return (kind, evidence) for the cap trim inside a function body."""
    if body is None:
        return None, "function not found"
    # SQL form: ORDER BY <time col> DESC ... LIMIT n
    m = re.search(r"ORDER BY\s+([a-z_]+)\s+DESC[^)]*LIMIT", body)
    if m:
        return "by_time", "ORDER BY %s DESC" % m.group(1)
    # Slice form: keep = keep[len(keep)-N:]  (front-drops = arrival order)
    if re.search(r"\[\s*len\([^)]*\)\s*-\s*[A-Za-z_.]+\s*:\s*\]", body):
        return "by_position", "slice front-trim [len-N:]"
    if re.search(r"\[:\s*[A-Za-z_.]+\s*\]", body):
        return "by_position", "slice back-keep [:N]"
    return None, "no trim expression matched"


def check_trim_population():
    print("\n-- cap trims, classified from source --")
    for label, rel, sig, expected in TRIMS:
        try:
            src = read(rel)
        except OSError as e:
            fail("%s: cannot read %s (%s)" % (label, rel, e))
            continue
        body = body_of(src, sig)
        kind, evidence = classify_trim(body)
        if kind is None:
            fail("%s: could not classify the trim — %s" % (label, evidence))
            continue
        if kind != expected:
            fail("%s: trim is %s (%s), census expects %s — the tree changed; re-derive, "
                 "do not restate" % (label, kind, evidence, expected))
        else:
            ok("%s: %s (%s)" % (label, kind, evidence))


# ---------------------------------------------------------------------------
# 2. Is the append order that a positional trim relies on actually safe?
#
# For the event stores the answer turns on ONE fact: what goes in Record.When.
# If When is a single clock read per batch, every batch is non-decreasing and a
# front-trim drops the oldest. If When ever comes from the EVENT (the producer's
# timestamp), a delayed or replayed event lands out of order and a front-trim
# drops a fresh record. Beat 724 measured exactly that mechanism in the timeline.
#
# So: find every call of the stamping function and read the value passed.
# ---------------------------------------------------------------------------
def check_when_source():
    print("\n-- Record.When provenance (what a positional trim is built on) --")
    src = read("internal/central/service.go")
    body = body_of(src, r"func \(s \*Service\) retain\(")
    if body is None:
        fail("retain(): function not found — cannot establish When provenance")
        return
    m = re.search(r"when\s*:?=\s*s\.nowTime\(\)", body)
    if m:
        ok("retain(): When comes from s.nowTime() — one server clock per ingest, "
           "so append order is non-decreasing")
    else:
        m2 = re.search(r"RecordFromEvent\([^)]*\)", body)
        fail("retain(): When does NOT come from a single server clock read (%s) — "
             "a positional trim is no longer supported by append order; re-derive"
             % (m2.group(0) if m2 else "pattern absent"))
    if "ev.TS" in body or "ev.When" in body:
        fail("retain(): an event-supplied timestamp reaches the record stamp — "
             "this is beat 724's mechanism in the store; re-derive")


def check_sqlite_window_matches_trim():
    """The SQL trim orders by when_unix; the window filter must delete on the same column."""
    print("\n-- SQLite: the trim and the window must agree on the column --")
    src = read("internal/central/eventstore_sqlite.go")
    body = body_of(src, r"func \(s \*SQLiteRecordStore\) prune\(")
    if body is None:
        fail("SQLiteRecordStore.prune: function not found")
        return
    win = re.search(r"DELETE FROM records WHERE\s+([a-z_]+)\s*<", body)
    trim = re.search(r"ORDER BY\s+([a-z_]+)\s+DESC", body)
    if not (win and trim):
        fail("SQLite prune: one of window/trim expressions not found")
        return
    if win.group(1) != trim.group(1):
        fail("SQLite prune: window deletes on %s but the cap orders by %s — the two "
             "decisions age by different fields" % (win.group(1), trim.group(1)))
    else:
        ok("SQLite prune: window and cap both age by %s" % win.group(1))


def check_query_order_claim():
    """Query walks the slice backwards. That is 'newest first' only if append order is time order."""
    print("\n-- query order rests on the same invariant the trim does --")
    for name, rel, sig in [
        ("MemoryRecordStore.Query", "internal/central/eventstore.go",
         r"func \(m \*MemoryRecordStore\) Query\("),
        ("FileRecordStore.Query", "internal/central/eventstore.go",
         r"func \(f \*FileRecordStore\) Query\("),
    ]:
        body = body_of(read(rel), sig)
        if body is None:
            fail("%s: not found" % name)
            continue
        if re.search(r"for i := len\([a-z_.]*\)\s*-\s*1;\s*i\s*>=\s*0;\s*i--", body):
            ok("%s: reverse walk (correct ONLY because When is a monotone server clock — "
               "the same fact the positional trim relies on)" % name)
        else:
            fail("%s: no longer a reverse walk — ordering claim changed, re-derive" % name)


# ---------------------------------------------------------------------------
# 3. Beat 724's timeline, transcribed faithfully, as the CONTROL.
#
# A census that reports on three stores and never re-proves the one known
# defect can go green while the defect it was built from gets 'fixed' into a
# different bug. So the known-bad mechanism is re-measured every run.
# ---------------------------------------------------------------------------
def timeline_record(list_, at, class_, asOf, window, cap):
    cutoff = asOf - window
    keep = [o for o in list_ if o[0] > cutoff]
    keep.append((at, class_))
    if len(keep) > cap:
        keep = keep[len(keep) - cap:]
    return keep


def check_timeline_control():
    print("\n-- control: beat 724's timeline defect, re-measured every run --")
    WINDOW, CAP = 900, 4
    # list holds (t, class) with t in seconds for exposition
    lst = [(900, "identity.drift")]
    for t in (1010, 1020, 1030, 1040):
        lst = timeline_record(lst, t, "process.exec", t, WINDOW, CAP)
    survived = any(c == "identity.drift" for _, c in lst)
    if survived:
        fail("control: the identity.drift observation SURVIVED — beat 724's defect did not "
             "reproduce, so this transcription is no longer faithful")
    else:
        ok("control: a 100s-old identity.drift in a 900s window is evicted by the cap trim alone")
    # Prove it was not the WINDOW that removed it: every asOf keeps 900 in window.
    kept_by_window = all(900 > asOf - WINDOW for asOf in (1010, 1020, 1030, 1040))
    if not kept_by_window:
        fail("control: the window filter, not the cap, removed it — the control is not "
             "measuring what it claims")
    else:
        ok("control: the window filter kept it on every tick — only the cap trim removed it")


# ---------------------------------------------------------------------------
# 4. Structural: no NEW bounded history may appear unasked.
# ---------------------------------------------------------------------------
EXPECTED_TRIMMED_FUNCS = {
    ("internal/rules/timeline.go", "Record"),
    ("internal/central/eventstore.go", "pruneLocked"),
    ("internal/central/eventstore_sqlite.go", "prune"),
}


def check_population_pin():
    print("\n-- population pin: every cap/trim in the tree is one this census knows --")
    seen = set()
    trim_re = re.compile(r"\[len\([A-Za-z_.]+\)\s*-\s*[A-Za-z_.]+:\]|"
                         r"\[len\([A-Za-z_.]+\)\s*-\s*[A-Za-z_.]+\s*:\s*[A-Za-z_.]+\]|"
                         r"ORDER BY\s+[a-z_]+\s+DESC[^)]*LIMIT")
    func_re = re.compile(r"^func (?:\([^)]*\) )?([A-Za-z_][A-Za-z0-9_]*)")
    for p in sorted(ROOT.glob("internal/**/*.go")) + sorted(ROOT.glob("cmd/**/*.go")):
        if p.name.endswith("_test.go"):
            continue
        src = p.read_text(errors="replace")
        current = None
        for line in src.splitlines():
            fm = func_re.match(line)
            if fm:
                current = fm.group(1)
            if trim_re.search(line):
                seen.add((str(p.relative_to(ROOT)), current))
    missing = sorted(seen - EXPECTED_TRIMMED_FUNCS)
    gone = sorted(EXPECTED_TRIMMED_FUNCS - seen)
    if missing:
        fail("new cap/trim found unclassified: %s — ask it the same two questions "
             "(age by which field? is the list ordered in that field?)"
             % ", ".join("%s.%s" % m for m in missing))
    if gone:
        fail("known cap/trim no longer found: %s — either deleted or re-spelled; "
             "the census is describing code that moved" % ", ".join("%s.%s" % g for g in gone))
    if not missing and not gone:
        ok("all %d cap/trim sites are ones this census has classified" % len(seen))


def main():
    check_trim_population()
    check_when_source()
    check_sqlite_window_matches_trim()
    check_query_order_claim()
    check_timeline_control()
    check_population_pin()
    print()
    if FAILS:
        print("retentioncensus: %d FAIL / %d ok" % (len(FAILS), OKS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("retentioncensus: %d ok, 0 FAIL" % OKS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
